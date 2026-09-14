"""Durable /background reads must not consume another tab's/sibling's results."""
import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def load_tracker():
    spec = importlib.util.spec_from_file_location("background_fixture", ROOT / "api/background.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def tracker(tmp_path, monkeypatch):
    from api import config
    monkeypatch.setattr(config, "STATE_DIR", tmp_path)
    return load_tracker()


def test_sibling_results_are_repeatable_and_parent_scoped(tracker):
    for task in ("one", "two"):
        tracker.track_background("parent", "child-" + task, "stream-" + task, task, task)
        tracker.complete_background("parent", task, "answer-" + task)
    expected = tracker.get_results("parent")
    assert {r["task_id"] for r in expected} == {"one", "two"}
    assert tracker.get_results("parent") == expected  # second tab / lost HTTP response
    assert tracker.get_results("other") == []
    assert len(tracker.get_background_tasks("parent")) == 2


def test_restart_keeps_results_and_marks_unfinished_interrupted(tracker):
    tracker.track_background("parent", "child1", "stream1", "done", "finished task")
    tracker.complete_background("parent", "done", "full output\n" * 1000)
    tracker.track_background("parent", "child2", "stream2", "running", "unfinished task")
    restarted = load_tracker()
    tasks = {r["task_id"]: r for r in restarted.get_background_tasks("parent")}
    assert tasks["done"]["answer"] == "full output\n" * 1000
    assert tasks["done"]["status"] == "done"
    assert tasks["running"]["status"] == "interrupted"
    assert "not resumed" in tasks["running"]["error"].lower()
    assert load_tracker().get_background_tasks("parent") == list(tasks.values())


def test_reads_return_detached_snapshots(tracker):
    tracker.track_background("parent", "child", "stream", "task", "name")
    snapshot = tracker.get_background_tasks("parent")
    snapshot[0]["status"] = "done"
    tracker.complete_background("parent", "task", "real answer")
    assert tracker.get_results("parent")[0]["answer"] == "real answer"


def test_completion_is_idempotent(tracker):
    tracker.track_background("parent", "child", "stream", "task", "name")
    tracker.complete_background("parent", "task", "first answer")
    tracker.complete_background("parent", "task", "late duplicate")
    assert tracker.get_results("parent")[0]["answer"] == "first answer"


def test_late_completion_after_interruption_does_not_authorize_cleanup(tracker):
    tracker.track_background("parent", "child", "stream", "task", "name")
    restarted = load_tracker()
    assert restarted.get_results("parent")[0]["status"] == "interrupted"
    assert not tracker.complete_background("parent", "task", "unsaved late output")


def test_completion_rejects_wrong_parent_and_rolls_back_failure(tracker, monkeypatch):
    tracker.track_background("parent", "child", "stream", "task", "name")
    assert not tracker.complete_background("other", "task", "wrong")
    with tracker._store() as db:
        db.execute("CREATE TRIGGER reject_result BEFORE UPDATE ON background_tasks BEGIN SELECT RAISE(ABORT, 'disk failure'); END")
    with pytest.raises(Exception, match="disk failure"):
        tracker.complete_background("parent", "task", "lost")
    assert tracker.get_background_tasks("parent")[0]["status"] == "running"


@pytest.fixture
def handler_harness(tracker, monkeypatch, tmp_path):
    # Execute the actual route function, replacing only its dependencies/runner.
    # No server, Agent checkout, provider or real session state is used.
    import ast
    import logging
    import sys
    import threading
    import types
    import uuid
    from unittest.mock import Mock
    module = ast.parse((ROOT / "api/routes.py").read_text())
    function = next(n for n in module.body if isinstance(n, ast.FunctionDef) and n.name == "_handle_background")
    parent = types.SimpleNamespace(session_id="canonical-parent", model="initial", workspace="work", profile="default", model_provider="provider")
    child_path = tmp_path / "child.json"
    child_path.write_text("preserved output")
    child = types.SimpleNamespace(session_id="child", model="initial", workspace="work", save=Mock())
    settled = types.SimpleNamespace(messages=[{"role": "assistant", "content": "result"}])
    models = types.ModuleType("api.models")
    models.new_session = lambda **kw: child
    models.Session = types.SimpleNamespace(load=lambda sid: settled)
    monkeypatch.setitem(sys.modules, "api.models", models)
    monkeypatch.setitem(sys.modules, "api.background", tracker)
    workers = []
    class Thread:
        def __init__(self, *, target, daemon):
            self.target = target
        def start(self):
            workers.append(self.target)
    namespace = dict(require=lambda *a: None, _agent_runtime_barrier_response=lambda **kw: None,
        get_session=lambda sid: parent, bad=lambda h, error, status=400: {"error": error, "status": status},
        j=lambda h, data: data, uuid=uuid, logger=logging.getLogger(__name__),
        register_session_writeback_owner=Mock(), clear_session_writeback_owner_if_owned=Mock(),
        register_stream_owner=Mock(), unregister_stream_owner=Mock(),
        create_stream_channel=lambda: {}, STREAMS={}, STREAMS_LOCK=threading.Lock(),
        threading=types.SimpleNamespace(Thread=Thread), SESSION_DIR=tmp_path,
        _run_agent_streaming=Mock())
    from api import config
    monkeypatch.setattr(config, "clear_session_writeback_owner_if_owned", namespace["clear_session_writeback_owner_if_owned"])
    exec(compile(ast.Module(body=[function], type_ignores=[]), str(ROOT / "api/routes.py"), "exec"), namespace)
    return types.SimpleNamespace(ns=namespace, run=lambda: namespace["_handle_background"](None, {"session_id":"alias", "prompt":"work"}),
        parent=parent, child=child, settled=settled, path=child_path, workers=workers, tracker=tracker)


def test_worker_captures_identity_and_does_not_save_parent(handler_harness):
    h = handler_harness
    response = h.run()
    h.parent.model = "changed-during-run"
    h.workers[0]()
    assert response["parent_session_id"] == "canonical-parent"
    assert h.ns["_run_agent_streaming"].call_args.args[2] == "initial"
    assert h.parent.model == "changed-during-run"
    assert h.tracker.get_results("canonical-parent")[0]["answer"] == "result"
    assert not h.path.exists()


def test_worker_keeps_child_when_result_persistence_fails(handler_harness, monkeypatch):
    h = handler_harness
    def fail(*args, **kwargs):
        raise OSError("disk full")
    monkeypatch.setattr(h.tracker, "complete_background", fail)
    h.run()
    h.workers[0]()
    assert h.path.exists()
    assert h.tracker.get_background_tasks("canonical-parent")[0]["status"] == "running"


@pytest.mark.parametrize("messages, expected", [
    ([], "no_response"),
    ([{"role":"assistant", "content":"progress", "tool_calls":[{}]}], "no_response"),
    ([{"role":"assistant", "content":"failed", "_error":True}], "error"),
    ([{"role":"assistant", "content":"cancelled", "_error":True, "provider_details_label":"Cancellation details"}], "cancelled"),
])
def test_worker_terminal_outcomes(handler_harness, messages, expected):
    h = handler_harness
    h.settled.messages = messages
    h.run()
    h.workers[0]()
    assert h.tracker.get_results("canonical-parent")[0]["status"] == expected


def test_worker_setup_failure_is_not_left_running(handler_harness):
    h = handler_harness
    h.child.save.side_effect = [None, OSError("cannot save active stream")]
    response = h.run()
    assert response["status"] == 500
    assert not h.workers
    assert h.path.exists()
    assert h.tracker.get_results("canonical-parent")[0]["status"] == "error"
    h.ns["clear_session_writeback_owner_if_owned"].assert_called_once()


def test_concurrent_sibling_completion_and_reads_are_lossless(tracker):
    from concurrent.futures import ThreadPoolExecutor
    for i in range(12):
        tracker.track_background("parent", f"child{i}", f"stream{i}", str(i), "work")
    def settle(i):
        assert tracker.complete_background("parent", str(i), f"answer{i}")
        tracker.get_results("parent")
    with ThreadPoolExecutor(max_workers=6) as pool:
        list(pool.map(settle, range(12)))
    assert {r["answer"] for r in tracker.get_results("parent")} == {f"answer{i}" for i in range(12)}


def test_failed_launch_persistence_never_starts_worker(handler_harness, monkeypatch):
    h = handler_harness
    def fail(*args, **kwargs):
        raise OSError("disk full")
    monkeypatch.setattr(h.tracker, "track_background", fail)
    assert h.run()["status"] == 500
    assert not h.workers
    assert h.path.exists()


def test_thread_start_and_failure_persistence_release_owners(handler_harness, monkeypatch):
    h = handler_harness
    def fail(*args, **kwargs):
        raise OSError("cannot start")
    monkeypatch.setattr(h.ns["threading"].Thread, "start", fail)
    monkeypatch.setattr(h.tracker, "complete_background", fail)
    assert h.run()["status"] == 500
    assert h.ns["STREAMS"] == {}
    h.ns["unregister_stream_owner"].assert_called_once()
    h.ns["clear_session_writeback_owner_if_owned"].assert_called_once()
    assert h.path.exists()
