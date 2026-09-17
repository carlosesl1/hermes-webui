"""Real handler frames and journal cursors, with no agent/network calls."""
import io
import queue
from types import SimpleNamespace
from urllib.parse import urlparse

import pytest
from api import config, models, routes, run_journal, streaming


class Handler:
    def __init__(self, headers=None):
        self.headers = headers or {}
        self.wfile = io.BytesIO()

    def send_response(self, *_):
        pass

    def send_header(self, *_):
        pass

    def end_headers(self):
        pass


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(models, "SESSION_DIR", tmp_path)
    monkeypatch.setattr(routes, "_stream_id_visible_to_request_profile", lambda *_: True)
    monkeypatch.setattr(routes, "_session_id_visible_to_request_profile", lambda *_: True)
    monkeypatch.setattr(routes, "get_session", lambda *_a, **_k: SimpleNamespace())
    monkeypatch.setattr(routes, "_active_run_stream_for_session", lambda *_: "r")
    cursors = {}
    monkeypatch.setattr(routes, "STREAM_LAST_EVENT_ID", cursors)
    monkeypatch.setattr(streaming, "STREAM_LAST_EVENT_ID", cursors)
    return run_journal.RunJournalWriter("s", "r", session_dir=tmp_path)


def test_metering_only_creates_no_journal_file(tmp_path, isolated):
    event = isolated.append_sse_event("metering", {"active": 1})
    assert event["event_id"] is None and "seq" not in event
    assert not list(tmp_path.rglob("*.jsonl"))
    assert isolated.append_sse_event("token", {})["event_id"] == "r:1"


@pytest.mark.parametrize("session_route", [False, True])
@pytest.mark.parametrize("legacy_tuple", [False, True])
def test_live_metering_and_null_cursor_reach_both_handlers(isolated, monkeypatch, session_route, legacy_tuple):
    monkeypatch.setattr(routes, "_sse_replay_run_journal_gap_checked", lambda *_a, **_k: (False, None))

    class LiveChannel(config.StreamChannel):
        def subscribe_with_snapshot(self):
            result = super().subscribe_with_snapshot()
            streaming._publish_stream_event(isolated, self, "r", "token", {"text": "one"})
            if legacy_tuple:
                self.put_nowait(("metering", {"active": 1}))
            else:
                streaming._publish_stream_event(isolated, self, "r", "metering", {"active": 1})
            self.put_nowait(("context_status", {"live": True}, None))
            streaming._publish_stream_event(isolated, self, "r", "done", {})
            streaming._publish_stream_event(isolated, self, "r", "stream_end", {})
            return result

    channel = LiveChannel()
    monkeypatch.setattr(routes, "peek_stream", lambda *_: channel)
    handler = Handler()
    if session_route:
        routes._handle_session_run_journal_stream_for_session(handler, urlparse("/api/sessions/s/events"), "s")
    else:
        routes._handle_sse_stream(handler, urlparse("/api/chat/stream?stream_id=r"))
    body = handler.wfile.getvalue().decode()
    frames = body.split("\n\n")
    for event in ("metering", "context_status"):
        frame = next(frame for frame in frames if f"event: {event}" in frame)
        assert "id:" not in frame
    assert "id: r:1" in frames[0] and "id: r:2" in body
    assert channel.diagnostic_snapshot()["subscriber_count"] == 0


def test_session_replay_filters_legacy_metering_but_validates_cursor(isolated, tmp_path, monkeypatch):
    for event in ("token", "metering", "token", "done"):
        run_journal.append_run_event("s", "r", event, {}, session_dir=tmp_path)
    monkeypatch.setattr(routes, "_active_run_stream_for_session", lambda *_: None)
    monkeypatch.setattr(routes, "SSELease", lambda: SimpleNamespace(active=lambda: False))
    handler = Handler({"Last-Event-ID": "r:1"})
    routes._handle_session_run_journal_stream_for_session(handler, urlparse("/api/sessions/s/events"), "s")
    body = handler.wfile.getvalue().decode()
    assert "event: metering" not in body
    assert "id: r:3" in body and "id: r:4" in body
    assert run_journal.read_session_run_events("s", after_event_id="r:2", session_dir=tmp_path)["status"] == "ok"
    assert run_journal.read_session_run_events("s", after_event_id="r:99", session_dir=tmp_path)["status"] != "ok"


@pytest.mark.parametrize("cursor", ["after_seq=1", "header"])
def test_chat_reconnect_after_lease_uses_real_replay(isolated, monkeypatch, cursor):
    channel = config.StreamChannel()
    monkeypatch.setattr(routes, "peek_stream", lambda *_: channel)
    for event in ("token", "token", "done", "stream_end"):
        streaming._publish_stream_event(isolated, channel, "r", event, {})
    handler = Handler({"Last-Event-ID": "r:1"} if cursor == "header" else {})
    suffix = "" if cursor == "header" else "&" + cursor
    routes._handle_sse_stream(handler, urlparse("/api/chat/stream?stream_id=r" + suffix))
    body = handler.wfile.getvalue().decode()
    assert "id: r:1\n" not in body
    assert body.count("id: r:2\n") == 1 and body.count("id: r:3\n") == 1
    assert channel.diagnostic_snapshot()["subscriber_count"] == 0


def test_chat_header_disconnect_releases_subscription(isolated, monkeypatch):
    channel = config.StreamChannel()
    monkeypatch.setattr(routes, "peek_stream", lambda *_: channel)
    handler = Handler()
    def fail(*_):
        raise BrokenPipeError()
    handler.end_headers = fail
    routes._handle_sse_stream(handler, urlparse("/api/chat/stream?stream_id=r"))
    assert channel.diagnostic_snapshot()["subscriber_count"] == 0


@pytest.mark.parametrize("kind,exit_kind", [
    ("gateway", "lease"), ("gateway", "disconnect"), ("gateway", "shutdown"),
    ("global", "lease"), ("global", "disconnect"),
    ("persistent", "lease"), ("persistent", "disconnect"), ("persistent", "shutdown"),
])
def test_auxiliary_stream_cleanup(monkeypatch, kind, exit_kind):
    from api import background_process, gateway_watcher
    q = queue.Queue()
    q.put(None if exit_kind == "shutdown" else {"type": "sessions_changed"})
    removed = []
    channel = SimpleNamespace(subscribe=lambda: q, unsubscribe=removed.append, is_alive=lambda: True)
    monkeypatch.setattr(routes, "SSELease", lambda: SimpleNamespace(active=lambda: exit_kind != "lease"))
    monkeypatch.setattr(routes, "load_settings", lambda: {"show_cli_sessions": True})
    monkeypatch.setattr(gateway_watcher, "get_watcher", lambda: channel)
    monkeypatch.setattr(models, "get_cli_sessions", lambda: [])
    monkeypatch.setattr(routes, "subscribe_session_events", lambda: q)
    monkeypatch.setattr(routes, "unsubscribe_session_events", removed.append)
    monkeypatch.setattr(background_process, "subscribe_to_session_channel", lambda *_a, **_k: (channel, q))
    monkeypatch.setattr(background_process, "active_stream_id_for_session", lambda *_: None)
    handler = Handler()
    if exit_kind == "disconnect":
        class BrokenWriter:
            def write(self, *_):
                raise BrokenPipeError()
        handler.wfile = BrokenWriter()
    if kind == "gateway":
        routes._handle_gateway_sse_stream(handler, urlparse("/api/gateway/events"))
    elif kind == "global":
        routes._handle_session_events_stream(handler)
    else:
        routes._handle_session_sse_stream(handler, urlparse("/api/session/stream?session_id=s"))
    assert removed == [q]


def test_session_lease_releases_only_observer(isolated, monkeypatch):
    channel = config.StreamChannel()
    worker = object()
    runs = {"r": worker}
    monkeypatch.setattr(config, "ACTIVE_RUNS", runs)
    monkeypatch.setattr(routes, "peek_stream", lambda *_: channel)
    monkeypatch.setattr(routes, "SSELease", lambda: SimpleNamespace(active=lambda: False))
    routes._handle_session_run_journal_stream_for_session(Handler(), urlparse("/api/sessions/s/events"), "s")
    assert channel.diagnostic_snapshot()["subscriber_count"] == 0
    assert runs == {"r": worker}
    # Expiring the observer does not fence publication or settle its journal.
    assert isolated.append_sse_event("token", {})["event_id"] == "r:1"


def test_lease_maximum_is_finite(monkeypatch):
    from api import sse_lease
    monkeypatch.setenv("HERMES_WEBUI_SSE_LEASE_SECONDS", "999999999")
    monkeypatch.setattr(sse_lease.time, "monotonic", lambda: 0)
    assert sse_lease.SSELease().deadline == 3600
