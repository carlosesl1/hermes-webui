"""CHAT-01/02: real journal + stream publication/cancellation, no inference."""
import ast
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import queue
import threading
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from api import config, models, run_journal, streaming


@pytest.fixture(autouse=True)
def isolated_state(tmp_path, monkeypatch):
    monkeypatch.setattr(models, "SESSION_DIR", tmp_path)
    monkeypatch.setattr(models, "SESSION_INDEX_FILE", tmp_path / "_index.json")
    monkeypatch.setattr(streaming, "SESSION_DIR", tmp_path)
    for name in ("STREAMS", "CANCEL_FLAGS", "AGENT_INSTANCES", "ACTIVE_RUNS",
                 "STREAM_SESSION_OWNERS", "SESSION_WRITEBACK_OWNERS",
                 "STREAM_PARTIAL_TEXT", "STREAM_LAST_EVENT_ID"):
        value = {}
        monkeypatch.setattr(config, name, value)
        if hasattr(streaming, name):
            monkeypatch.setattr(streaming, name, value)
    monkeypatch.setattr(models, "SESSIONS", {})
    monkeypatch.setattr(streaming, "get_session", lambda sid: models.SESSIONS[sid])
    monkeypatch.setattr(streaming, "_preferred_agent_display_name_for_session", lambda s: "Hermes")


def _worker_put(writer, channel, flag):
    # Execute the production closure without starting an agent/provider. All
    # journal, channel and cursor operations remain real (audit probe shape).
    tree = ast.parse(Path(streaming.__file__).read_text())
    worker = next(n for n in tree.body if isinstance(n, ast.FunctionDef)
                  and n.name == "_run_agent_streaming")
    put = next(n for n in worker.body if isinstance(n, ast.FunctionDef) and n.name == "put")
    ns = dict(vars(streaming), run_journal=writer, q=channel, cancel_event=flag,
              stream_id=writer.run_id, _success_writeback_committed=False)
    exec(compile(ast.Module(body=[put], type_ignores=[]), streaming.__file__, "exec"), ns)
    return ns["put"]


def test_writer_reservation_cannot_overtake_append(tmp_path, monkeypatch):
    writer = run_journal.RunJournalWriter("sid", "run", session_dir=tmp_path)
    real_append = run_journal.append_run_event
    paused, release = threading.Event(), threading.Event()

    def pause_token(*args, **kwargs):
        if args[2] == "token":
            paused.set()
            assert release.wait(5)
        return real_append(*args, **kwargs)

    monkeypatch.setattr(run_journal, "append_run_event", pause_token)
    with ThreadPoolExecutor(2) as pool:
        first = pool.submit(writer.append_sse_event, "token", {"text": "paid-token"})
        assert paused.wait(5)
        try:
            writer.append_sse_event("metering", {"active": 1})
        finally:
            release.set()
        first.result(5)
    writer.append_sse_event("metering", {})
    rows = run_journal.read_run_events("sid", "run")["events"]
    assert [row["seq"] for row in rows] == [1, 2, 3]
    assert run_journal.read_session_run_events("sid", after_event_id="run:1")["status"] == "ok"
    assert any(row["event"] == "token" for row in
               run_journal.read_run_events("sid", "run", after_seq=1)["events"])


def test_worker_publication_cannot_overtake_journal_cursor(tmp_path):
    writer = run_journal.RunJournalWriter("sid", "run", session_dir=tmp_path)
    paused, release, attempted = threading.Event(), threading.Event(), threading.Event()

    class PausingChannel(config.StreamChannel):
        def note_last_event_id(self, event_id):
            if event_id == "run:1":
                paused.set()
                assert release.wait(5)
            return super().note_last_event_id(event_id)

    channel = PausingChannel()
    subscriber, _ = channel.subscribe_with_snapshot()
    put = _worker_put(writer, channel, threading.Event())

    def metering():
        attempted.set()
        put("metering", {"active": 1})

    with ThreadPoolExecutor(2) as pool:
        first = pool.submit(put, "token", {"text": "first"})
        assert paused.wait(5)
        second = pool.submit(metering)
        assert attempted.wait(5)
        try:
            # No seq=2 may be visible while seq=1 publication is paused.
            with pytest.raises(queue.Empty):
                subscriber.get(timeout=0.1)
        finally:
            release.set()
        first.result(5)
        second.result(5)
    assert [subscriber.get_nowait()[2] for _ in range(2)] == ["run:1", "run:2"]
    assert config.STREAM_LAST_EVENT_ID["run"] == "run:2"


def _running_session(tmp_path):
    session = models.Session(session_id="sid", title="Cancellation", messages=[])
    session.active_stream_id = "run"
    session.pending_user_message = "Please continue"
    session.pending_started_at = 1234567890.0
    session.save()
    models.SESSIONS["sid"] = session
    channel = config.StreamChannel()
    flag = threading.Event()
    config.STREAMS["run"] = channel
    config.CANCEL_FLAGS["run"] = flag
    config.STREAM_PARTIAL_TEXT["run"] = "paid-token"
    # Accept interrupt without unwinding: models the audit's blocked I/O worker.
    config.AGENT_INSTANCES["run"] = SimpleNamespace(session_id="sid", interrupt=Mock())
    config.register_stream_owner("run", "sid")
    config.register_active_run("run", session_id="sid", phase="running")
    writer = run_journal.RunJournalWriter("sid", "run", session_dir=tmp_path)
    put = _worker_put(writer, channel, flag)
    put("token", {"text": "paid-token"})
    return session, channel, put


@pytest.mark.parametrize("detached", [False, True])
def test_cancel_durable_before_worker_unwinds_and_idempotent(tmp_path, detached):
    from api.background_process import _session_has_active_turn

    _, channel, put = _running_session(tmp_path)
    subscriber, _ = channel.subscribe_with_snapshot()
    while not subscriber.empty():
        subscriber.get_nowait()
    if detached:
        config.STREAMS.pop("run")
    assert streaming.cancel_stream("run") is True
    saved = json.loads((tmp_path / "sid.json").read_text())
    assert saved["active_stream_id"] is None
    assert any(m.get("_partial") and m["content"] == "paid-token" for m in saved["messages"])
    summary = run_journal.latest_run_summary("sid", "run")
    assert summary["terminal_state"] == "interrupted-by-user"
    assert summary["last_event_id"] == "run:2"
    assert config.ACTIVE_RUNS["run"]["phase"] == "cancelling"
    assert _session_has_active_turn("sid") is True  # no premature admission
    if not detached:
        item = subscriber.get_nowait()
        assert item[0] == "cancel" and item[2] == "run:2"
    assert streaming.cancel_stream("run") is True
    # Worker callbacks racing/finishing after the HTTP cancellation must not
    # create another terminal or rewrite cancellation into a provider error.
    put("cancel", {"message": "Cancelled by user"})
    put("apperror", {"type": "provider_error"})
    put("metering", {})
    assert [row["event"] for row in run_journal.read_run_events("sid", "run")["events"]] == ["token", "cancel"]
    replay = run_journal.read_run_events("sid", "run", after_seq=1)["events"]
    assert len(replay) == 1 and replay[0]["terminal_state"] == "interrupted-by-user"


def test_cancel_cannot_replace_successful_terminal(tmp_path):
    _, channel, _ = _running_session(tmp_path)
    writer = run_journal.RunJournalWriter("sid", "run", session_dir=tmp_path)
    writer.append_sse_event("done", {"session": {}})
    writer.append_sse_event("title", {"title": "Finished"})
    writer.append_sse_event("stream_end", {})
    writer.append_sse_event("cancel", {"message": "late stop"})
    assert run_journal.latest_run_summary("sid", "run")["terminal_state"] == "completed"


def test_interrupt_provider_error_cannot_beat_durable_cancel(tmp_path):
    _, _, put = _running_session(tmp_path)
    config.AGENT_INSTANCES["run"].interrupt.side_effect = lambda _message: put(
        "apperror", {"type": "provider_error", "message": "socket closed"},
    )
    assert streaming.cancel_stream("run") is True
    rows = run_journal.read_run_events("sid", "run")["events"]
    assert [row["event"] for row in rows] == ["token", "cancel"]
    assert rows[-1]["terminal_state"] == "interrupted-by-user"


def test_cancel_is_fsynced_before_publication(tmp_path, monkeypatch):
    writer = run_journal.RunJournalWriter("sid", "run", session_dir=tmp_path)
    writer.append_sse_event("token", {"text": "partial"})
    calls = []
    real_fsync = run_journal.os.fsync

    def fsync(fd):
        real_fsync(fd)
        calls.append("fsync")

    monkeypatch.setattr(run_journal.os, "fsync", fsync)
    writer.append_sse_event("cancel", {}, publish=lambda event: calls.append(event["event"]))
    assert calls == ["fsync", "cancel"]


def test_provider_error_rechecks_cancel_after_waiting_for_journal(tmp_path, monkeypatch):
    _, channel, _ = _running_session(tmp_path)
    flag = config.CANCEL_FLAGS["run"]
    writer = run_journal.RunJournalWriter("sid", "run", session_dir=tmp_path)
    real_append = writer.append_sse_event

    def cancelled_before_lock(*args, **kwargs):
        flag.set()  # Stop races after put's fast-path guard, before append.
        return real_append(*args, **kwargs)

    monkeypatch.setattr(writer, "append_sse_event", cancelled_before_lock)
    put = _worker_put(writer, channel, flag)
    put("apperror", {"type": "provider_error"})
    assert streaming.cancel_stream("run") is True
    assert [row["event"] for row in run_journal.read_run_events("sid", "run")["events"]] == ["token", "cancel"]


def test_live_summary_cache_matches_cold_terminal_reconciliation(tmp_path):
    writer = run_journal.RunJournalWriter("sid", "run", session_dir=tmp_path)
    writer.append_sse_event("stream_end", {})
    writer.append_sse_event("stream_end", {"terminal_state": "tool_limit_reached"})
    warm = run_journal.latest_run_summary("sid", "run")
    run_journal._discard_cached_summary(writer._path)
    assert warm == run_journal.latest_run_summary("sid", "run")


def test_live_cancellation_fence_does_not_reparse_each_token(tmp_path, monkeypatch):
    writer = run_journal.RunJournalWriter("sid", "run", session_dir=tmp_path)
    writer.append_sse_event("token", {"text": "first"})

    def unexpected_read(_path):
        pytest.fail("live cancellation fencing reparsed the journal")

    monkeypatch.setattr(run_journal, "_read_jsonl", unexpected_read)
    for _ in range(100):
        writer.append_sse_event("token", {"text": "next"})
    writer.append_sse_event("cancel", {})
    assert writer.append_sse_event("apperror", {}) is None
    assert run_journal.latest_run_summary("sid", "run")["event_count"] == 102


def test_broken_live_queue_does_not_abort_durable_cancel(tmp_path, monkeypatch):
    _, channel, _ = _running_session(tmp_path)

    def broken(_item):
        raise RuntimeError("subscriber closed")

    monkeypatch.setattr(channel, "put_nowait", broken)
    assert streaming.cancel_stream("run") is True
    assert run_journal.latest_run_summary("sid", "run")["terminal_state"] == "interrupted-by-user"
