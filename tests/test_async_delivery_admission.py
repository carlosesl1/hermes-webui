"""Delivery identity/admission regressions, with optional read-only core SQL proof.

Set HERMES_WEBUI_TEST_CORE_SOURCE to a core checkout to run its actual delivery
functions against an in-memory ledger. Never import the core runtime or state.
"""
from __future__ import annotations

import ast
import contextlib
import importlib.util
import logging
import os
from pathlib import Path
import queue
import sqlite3
import sys
import threading
import time
import types
import uuid

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _functions(path, names, namespace):
    tree = ast.parse(path.read_text())
    nodes = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in names]
    assert {n.name for n in nodes} == set(names)
    module = ast.Module(body=[ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0), *nodes], type_ignores=[])
    exec(compile(ast.fix_missing_locations(module), str(path), "exec"), namespace)


@pytest.fixture
def helper(monkeypatch):
    spec = importlib.util.spec_from_file_location("isolated_delivery_helper", ROOT / "api/process_event_utils.py")
    mod = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, mod)
    spec.loader.exec_module(mod)
    yield mod
    mod._reset_legacy_async_delivery_dedupe_for_tests()


def _install(monkeypatch, core):
    package = types.ModuleType("tools")
    package.async_delegation = core
    monkeypatch.setitem(sys.modules, "tools", package)
    monkeypatch.setitem(sys.modules, "tools.async_delegation", core)


def _event(**extra):
    return dict(type="async_delegation", delegation_id="batch", session_key="parent", **extra)


@pytest.mark.parametrize("surface", ["legacy", "old-durable", "modern"])
def test_notice_never_claims_or_marks_final_identity(helper, monkeypatch, surface):
    core = types.ModuleType("tools.async_delegation")
    calls = []
    core.mark_completion_delivered = lambda ident: calls.append(("mark", ident))
    if surface != "legacy":
        core.claim_event_delivery = lambda evt, consumer: calls.append(("claim", evt["delegation_id"])) or "token"
        core.complete_event_delivery = lambda evt, token: calls.append(("complete", evt["delegation_id"]))
        core.release_event_delivery = lambda evt, token: calls.append(("release", evt["delegation_id"]))
    if surface == "modern":
        core.claim_event_delivery = lambda evt, consumer: "" if evt.get("task_failure_notice") else "token"
    _install(monkeypatch, core)
    for index in (0, 1):
        notice = _event(task_failure_notice=True, results=[{"task_index": index, "status": "error"}])
        claim = helper.claim_async_delegation_delivery(notice, "background")
        assert claim is not None
        assert helper.claim_async_delegation_delivery(dict(notice), "next-turn") is None
        helper.complete_async_delegation_delivery(notice, claim)
        assert calls == [], "interim events must never touch the final core ledger/marker"
    final = _event(status="completed")
    claim = helper.claim_async_delegation_delivery(final, "next-turn")
    assert claim is not None, "an accepted failure notice poisoned final completion"
    helper.complete_async_delegation_delivery(final, claim)
    assert helper.claim_async_delegation_delivery(final, "background") is None


@pytest.mark.parametrize("action", ["complete", "release", "defer"])
def test_notice_guard_survives_a_core_token_with_separate_local_identity(helper, monkeypatch, action):
    core = types.ModuleType("tools.async_delegation")

    def forbidden(*args, **kwargs):
        pytest.fail("notice touched the batch's core delivery identity")

    core.complete_event_delivery = core.release_event_delivery = forbidden
    core.defer_completion_delivery = core._update_delivery = forbidden
    core.mark_completion_delivered = core.mark_async_delegation_consumed = forbidden
    _install(monkeypatch, core)
    evt = _event(task_failure_notice=True, results=[{"task_index": 0}])
    notice_id = helper.completion_delivery_id(evt)
    assert notice_id != evt["delegation_id"]
    # Even an old in-flight token must not let the local notice identity become
    # a core batch ACK/release. Core event APIs derive their ID from the event.
    claim = helper.AsyncDelegationDeliveryClaim(notice_id, "old-core-token", True)
    outcome = getattr(helper, f"{action}_async_delegation_delivery")(evt, claim)
    if action == "defer":
        assert outcome is True


@pytest.fixture
def ledger(monkeypatch):
    source = os.environ.get("HERMES_WEBUI_TEST_CORE_SOURCE")
    if not source:
        pytest.skip("set HERMES_WEBUI_TEST_CORE_SOURCE for isolated core-SQL integration")
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE async_delegations(delegation_id TEXT PRIMARY KEY, delivery_state TEXT, delivery_claim TEXT, delivery_claimed_at REAL, delivery_attempts INTEGER, updated_at REAL, delivered_at REAL)")
    conn.execute("INSERT INTO async_delegations VALUES ('batch','pending',NULL,NULL,0,0,NULL)")
    conn.commit()

    @contextlib.contextmanager
    def transaction():
        with conn:
            yield conn

    core = types.ModuleType("tools.async_delegation")
    core.__dict__.update(time=time, os=os, uuid=uuid, logger=logging.getLogger("core-test"), _DB_LOCK=threading.RLock(), _transaction=transaction, _MAX_DELIVERY_ATTEMPTS=8)
    names = ["_update_delivery", "mark_completion_delivered", "claim_completion_delivery", "is_interim_delegation_event", "claim_event_delivery", "release_completion_delivery", "defer_completion_delivery", "complete_completion_delivery", "complete_event_delivery", "release_event_delivery", "_event_delivery"]
    _functions(Path(source) / "tools/async_delegation.py", names, core.__dict__)
    _install(monkeypatch, core)
    yield conn, core
    conn.close()


def _state(conn):
    return conn.execute("SELECT delivery_state,delivery_attempts,delivery_claim FROM async_delegations WHERE delegation_id='batch'").fetchone()


def _runner(helper, monkeypatch, response):
    class InlineThread:
        def __init__(self, target, **kwargs):
            self.target = target

        def start(self):
            self.target()

    routes = types.ModuleType("api.routes")
    routes.start_session_turn = lambda *args, **kwargs: response
    monkeypatch.setitem(sys.modules, "api.routes", routes)
    retries = []
    namespace = dict(logger=logging.getLogger("wakeup-test"), threading=types.SimpleNamespace(Thread=InlineThread), release_async_delegation_delivery=helper.release_async_delegation_delivery, _retry_unclaimed_async_delegation_event=lambda *args, **kwargs: retries.append(kwargs), _record_async_delegation_accepted=lambda evt, **kwargs: helper.complete_async_delegation_delivery(evt, kwargs["claim"]))
    if hasattr(helper, "defer_async_delegation_delivery"):
        namespace["defer_async_delegation_delivery"] = helper.defer_async_delegation_delivery
    _functions(ROOT / "api/background_process.py", ["_start_async_delegation_wakeup_turn"], namespace)
    return namespace["_start_async_delegation_wakeup_turn"], retries


@pytest.mark.parametrize("older_core", [False, True])
@pytest.mark.parametrize("error", ["process_wakeup_paused", "session already has an active stream"])
def test_unadmitted_wakeup_refunds_core_attempts(helper, ledger, monkeypatch, error, older_core):
    conn, core = ledger
    if older_core:
        del core.defer_completion_delivery
    run, retries = _runner(helper, monkeypatch, {"_status": 409, "error": error})
    evt = _event()
    for _ in range(10):
        claim = helper.claim_async_delegation_delivery(evt, "background")
        assert claim is not None
        run("parent", "result", delegation_id="batch", evt=evt, claim=claim, process_registry=object())
        assert _state(conn) == ("pending", 0, None)
    assert len(retries) == 10
    claim = helper.claim_async_delegation_delivery(evt, "next-turn")
    helper.complete_async_delegation_delivery(evt, claim)
    assert _state(conn) == ("delivered", 1, None)


def test_notice_leaves_actual_core_row_available_for_final(helper, ledger):
    conn, _ = ledger
    evt = _event(task_failure_notice=True, results=[{"task_index": 0}])
    claim = helper.claim_async_delegation_delivery(evt, "background")
    helper.complete_async_delegation_delivery(evt, claim)
    assert _state(conn) == ("pending", 0, None)
    claim = helper.claim_async_delegation_delivery(_event(), "next-turn")
    assert claim is not None
    assert _state(conn)[1] == 1


def test_actual_delivery_failures_still_exhaust_budget(helper, ledger, monkeypatch):
    conn, _ = ledger
    run, _ = _runner(helper, monkeypatch, {"_status": 500, "error": "failed"})
    for _ in range(8):
        claim = helper.claim_async_delegation_delivery(_event(), "background")
        assert claim is not None
        run("parent", "result", delegation_id="batch", evt=_event(), claim=claim, process_registry=object())
    assert _state(conn) == ("dropped", 8, None)


@pytest.mark.parametrize("older_core", [False, True])
def test_refund_is_token_scoped_and_does_not_release_another_owner(helper, ledger, older_core):
    conn, core = ledger
    if older_core:
        del core.defer_completion_delivery
    claim = helper.claim_async_delegation_delivery(_event(), "background")
    conn.execute("UPDATE async_delegations SET delivery_claim='other-owner'")
    conn.commit()
    assert helper.defer_async_delegation_delivery(_event(), claim) is False
    assert _state(conn) == ("pending", 1, "other-owner")
    assert helper.claim_async_delegation_delivery(_event(), "competitor") is None
    assert helper.async_delivery_retry_timer_count() == 0


@pytest.mark.parametrize("broken", [False, True])
def test_unknown_or_broken_refund_contract_keeps_lease_without_retry(helper, ledger, monkeypatch, broken):
    conn, core = ledger
    if broken:
        def fail(*args):
            raise RuntimeError("store unavailable")
        core.defer_completion_delivery = fail
    else:
        del core.defer_completion_delivery
        del core._update_delivery
    run, retries = _runner(helper, monkeypatch, {"_status": 409, "error": "process_wakeup_paused"})
    claim = helper.claim_async_delegation_delivery(_event(), "background")
    run("parent", "result", delegation_id="batch", evt=_event(), claim=claim, process_registry=object())
    assert _state(conn) == ("pending", 1, claim.claim_id)
    assert retries == []
    assert helper.async_delivery_retry_timer_count() == 0


def test_preledger_event_with_durable_token_can_defer(helper, ledger):
    conn, core = ledger
    conn.execute("DELETE FROM async_delegations")
    conn.commit()
    core.get_durable_delegation = lambda ident: None
    claim = helper.claim_async_delegation_delivery(_event(), "background")
    assert claim.durable and claim.claim_id
    assert helper.defer_async_delegation_delivery(_event(), claim) is True
    assert helper.claim_async_delegation_delivery(_event(), "next-turn") is not None


def test_legacy_admission_deferral_releases_local_claim(helper, monkeypatch):
    _install(monkeypatch, types.ModuleType("tools.async_delegation"))
    run, retries = _runner(helper, monkeypatch, {"_status": 409, "error": "process_wakeup_paused"})
    for _ in range(10):
        claim = helper.claim_async_delegation_delivery(_event(), "background")
        assert claim is not None
        run("parent", "result", delegation_id="batch", evt=_event(), claim=claim, process_registry=object())
    assert len(retries) == 10
    assert helper.legacy_async_delivery_dedupe_size() == 0


@pytest.mark.parametrize("delay", [float("inf"), float("-inf"), float("nan"), 1e300, "invalid"])
@pytest.mark.parametrize("path", ["arm", "schedule", "requeue"])
def test_retry_rejects_nonfinite_or_unwaitable_delays(helper, monkeypatch, delay, path):
    core = types.ModuleType("tools.async_delegation")
    core.get_durable_delegation = lambda ident: {"delivery_state": "pending"}
    _install(monkeypatch, core)
    monkeypatch.setattr(helper.threading, "Timer", lambda *a, **k: pytest.fail("invalid timer armed"))
    monkeypatch.setattr(helper.time, "sleep", lambda *a: pytest.fail("invalid sleep attempted"))
    target = queue.Queue()
    if path == "arm":
        accepted = helper._arm_async_delegation_restore_sweep(target, delay)
    elif path == "schedule":
        accepted = helper.schedule_async_delegation_claim_retry(_event(), target, delay=delay)
    else:
        accepted = helper.requeue_async_delegation_event(_event(), target, delay=delay)
    assert accepted is False
    assert target.empty()
    assert helper.async_delivery_retry_timer_count() == 0


def test_interim_retry_does_not_restore_final_ledger(helper, monkeypatch):
    core = types.ModuleType("tools.async_delegation")
    core.get_durable_delegation = lambda ident: {"delivery_state": "pending"}
    _install(monkeypatch, core)
    assert not helper.schedule_async_delegation_claim_retry(_event(task_failure_notice=True), queue.Queue())
    assert helper.async_delivery_retry_timer_count() == 0
