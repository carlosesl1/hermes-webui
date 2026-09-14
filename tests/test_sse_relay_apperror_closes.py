"""Regression: apperror must close SSE relay drain loops.

Backend emits `apperror` as a terminal event with no trailing `stream_end`
(streaming.py returns immediately after put('apperror', ...)). Relay loops that
only treat ("stream_end", "error", "cancel") as close signals hang forever —
the phantom name `error` is never emitted. The authoritative close set is
api.run_journal.SSE_RELAY_CLOSE_EVENTS.
"""
from __future__ import annotations

import ast
from pathlib import Path
import queue
import threading

import pytest

from api.run_journal import SSE_RELAY_CLOSE_EVENTS, TERMINAL_SSE_EVENTS


def test_relay_close_events_include_apperror_not_only_phantom_error():
    assert "apperror" in SSE_RELAY_CLOSE_EVENTS
    assert "stream_end" in SSE_RELAY_CLOSE_EVENTS
    assert "cancel" in SSE_RELAY_CLOSE_EVENTS
    # `error` may remain for legacy/adapter frames, but must not be the sole
    # stand-in for apperror.
    assert "apperror" in SSE_RELAY_CLOSE_EVENTS
    # `done` must NOT close the live chat relay — title + stream_end follow it.
    assert "done" not in SSE_RELAY_CLOSE_EVENTS
    assert "done" in TERMINAL_SSE_EVENTS


def test_routes_relay_loops_import_sse_relay_close_events():
    src = Path("api/routes.py").read_text(encoding="utf-8")
    assert "SSE_RELAY_CLOSE_EVENTS" in src
    # The three historic hardcoded tuples must be gone.
    assert 'event in ("stream_end", "error", "cancel")' not in src
    assert 'event in ("stream_end", "error", "cancel")' not in src
    assert '_is_terminal = event in ("stream_end", "error", "cancel")' not in src


@pytest.mark.parametrize("cancel_requested", [False, True])
def test_worker_apperror_publishes_only_before_cancel(tmp_path, monkeypatch, cancel_requested):
    """An interrupt-induced provider error must not steal the cancel terminal."""
    from api import run_journal, streaming

    tree = ast.parse(Path(streaming.__file__).read_text(encoding="utf-8"))
    worker = next(n for n in tree.body if isinstance(n, ast.FunctionDef)
                  and n.name == "_run_agent_streaming")
    put = next(n for n in worker.body if isinstance(n, ast.FunctionDef) and n.name == "put")
    monkeypatch.setattr(streaming, "STREAM_LAST_EVENT_ID", {})
    writer = run_journal.RunJournalWriter("sid", "run", session_dir=tmp_path)
    channel, flag = queue.Queue(), threading.Event()
    if cancel_requested:
        flag.set()
    ns = dict(vars(streaming), run_journal=writer, q=channel, stream_id="run",
              cancel_event=flag, _success_writeback_committed=False)
    exec(compile(ast.Module(body=[put], type_ignores=[]), streaming.__file__, "exec"), ns)
    ns["put"]("apperror", {"message": "provider failed"})
    if cancel_requested:
        assert channel.empty()
        assert run_journal.read_run_events("sid", "run", session_dir=tmp_path)["events"] == []
        ns["put"]("cancel", {"message": "Cancelled by user"})
        ns["put"]("apperror", {"message": "late provider failure"})
    expected = "cancel" if cancel_requested else "apperror"
    rows = run_journal.read_run_events("sid", "run", session_dir=tmp_path)["events"]
    assert [row["event"] for row in rows] == [expected]
    assert channel.get_nowait()[0] == expected
    assert channel.empty()
    assert expected in SSE_RELAY_CLOSE_EVENTS


def test_apperror_terminates_relay_close_predicate():
    """Observable behavior of the shared predicate used by relay loops."""
    assert "apperror" in SSE_RELAY_CLOSE_EVENTS
    # Simulate the drain decision: apperror must stop the loop.
    events = ["token", "tool", "apperror", "token"]
    closed_at = None
    for i, event in enumerate(events):
        if event in SSE_RELAY_CLOSE_EVENTS:
            closed_at = i
            break
    assert closed_at == 2
    # done alone must not stop the relay (title / stream_end still pending).
    assert "done" not in SSE_RELAY_CLOSE_EVENTS


def test_chat_sse_stream_unsubscribes_on_apperror(monkeypatch):
    """Live /api/chat/stream must exit the drain loop when apperror arrives."""
    import io
    import queue
    from urllib.parse import urlparse

    import api.routes as routes

    class _Handler:
        def __init__(self):
            self.status = None
            self.headers = {}
            self.wfile = io.BytesIO()
            self.command = "GET"
            self.path = "/"
            self.client_address = ("127.0.0.1", 12345)

        def send_response(self, status):
            self.status = status

        def send_header(self, key, value):
            self.headers[key] = value

        def end_headers(self):
            pass

    class _FakeStream:
        def __init__(self):
            self.q = queue.Queue()
            self.q.put_nowait(("token", {"text": "hi"}, "run1:1"))
            self.q.put_nowait(("apperror", {"message": "provider failed"}, "run1:2"))
            self.unsubscribed = False

        def subscribe_with_snapshot(self):
            return self.q, {"last_event_id": None, "offline_buffered_events": 0}

        def unsubscribe(self, q):
            self.unsubscribed = q is self.q

    stream = _FakeStream()
    monkeypatch.setattr(routes, "_stream_id_visible_to_request_profile", lambda *_a, **_k: True)
    monkeypatch.setattr(routes, "STREAMS", {"run1": stream})
    import api.config as config
    monkeypatch.setattr(
        config,
        "STREAMS",
        {"run1": stream},
    )
    monkeypatch.setattr(
        routes,
        "_sse_replay_run_journal_gap_checked",
        lambda *_a, **_k: (False, None),
    )

    def _sleep(_seconds):
        raise BrokenPipeError("hung waiting after apperror")

    monkeypatch.setattr(routes.time, "sleep", _sleep)

    handler = _Handler()
    routes._handle_sse_stream(handler, urlparse("/api/chat/stream?stream_id=run1"))

    assert stream.unsubscribed is True, "handler must unsubscribe after apperror"
    body = handler.wfile.getvalue().decode("utf-8", errors="replace")
    assert "event: apperror" in body
