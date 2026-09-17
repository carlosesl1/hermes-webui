"""Proxy accepts writes forever: lease still releases only its subscription."""
import io
from urllib.parse import urlparse

import pytest

from api import config, routes, run_journal, sse_lease, streaming


class Handler:
    def __init__(self, headers=None):
        self.headers = headers or {}
        self.wfile = io.BytesIO()

    def send_response(self, _value):
        pass

    def send_header(self, *_args):
        pass

    def end_headers(self):
        pass


@pytest.mark.parametrize("value", ["nan", "inf", "0", "-5", "invalid"])
def test_invalid_lease_cannot_disable_expiry(monkeypatch, value):
    monkeypatch.setenv("HERMES_WEBUI_SSE_LEASE_SECONDS", value)
    monkeypatch.setattr(sse_lease.time, "monotonic", lambda: 10)
    assert sse_lease.SSELease().deadline == 310


def test_proxy_orphan_expires_replacement_survives_and_cursor_resumes(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_WEBUI_SSE_LEASE_SECONDS", "1")
    clock = [0.0]
    monkeypatch.setattr(sse_lease.time, "monotonic", lambda: clock[0])
    channel = config.StreamChannel()
    writer = run_journal.RunJournalWriter("s", "r", session_dir=tmp_path)
    monkeypatch.setattr(routes, "peek_stream", lambda _: channel)
    monkeypatch.setattr(routes, "_stream_id_visible_to_request_profile", lambda *_: True)
    monkeypatch.setattr(routes, "_sse_replay_run_journal_gap_checked", lambda *_a, **_k: (False, None))
    monkeypatch.setattr(streaming, "STREAM_LAST_EVENT_ID", {})
    replacement = []
    real_emit = routes._sse_with_id

    def emit(handler, event, data, cursor):
        real_emit(handler, event, data, cursor)
        # New generation attaches while the old handler is still in-flight.
        replacement.append(channel.subscribe())
        clock[0] = 2.0

    monkeypatch.setattr(routes, "_sse_with_id", emit)
    streaming._publish_stream_event(writer, channel, "r", "token", {"text": "one"})
    handler = Handler()
    routes._handle_sse_stream(handler, urlparse("/api/chat/stream?stream_id=r"))
    assert "id: r:1" in handler.wfile.getvalue().decode()
    assert channel.diagnostic_snapshot()["subscriber_count"] == 1
    assert replacement[0].get_nowait()[2] == "r:1"
    streaming._publish_stream_event(writer, channel, "r", "token", {"text": "two"})
    streaming._publish_stream_event(writer, channel, "r", "done", {"session": {"session_id": "s"}})
    replay = run_journal.read_session_run_events("s", after_event_id="r:1", session_dir=tmp_path)
    assert [e["event_id"] for e in replay["events"]] == ["r:2", "r:3"]
    assert [replacement[0].get_nowait()[0] for _ in range(2)] == ["token", "done"]
    channel.unsubscribe(replacement[0])
    assert channel.diagnostic_snapshot()["subscriber_count"] == 0
