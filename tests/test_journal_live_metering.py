import io
from types import SimpleNamespace
from urllib.parse import urlparse
from api import config, run_journal, streaming



def test_live_metering_has_no_cursor_or_offline_replay(tmp_path, monkeypatch):
    monkeypatch.setattr(streaming, "STREAM_LAST_EVENT_ID", {})
    writer = run_journal.RunJournalWriter("s", "r", session_dir=tmp_path)
    channel = config.StreamChannel()
    q = channel.subscribe()
    for event in ("token", "metering", "token"):
        streaming._publish_stream_event(writer, channel, "r", event, {"text": "x"})
    frames = [q.get_nowait() for _ in range(3)]
    assert [f[2] for f in frames] == ["r:1", None, "r:2"]
    rows = run_journal.read_run_events("s", "r", session_dir=tmp_path)["events"]
    assert [r["event"] for r in rows] == ["token", "token"]
    assert run_journal.read_session_run_events("s", after_event_id="r:1", session_dir=tmp_path)["events"] == rows[1:]
    channel.unsubscribe(q)
    streaming._publish_stream_event(writer, channel, "r", "metering", {})
    replacement, snapshot = channel.subscribe_with_snapshot()
    assert replacement.empty()
    assert snapshot["last_event_id"] == "r:2"
    channel.unsubscribe(replacement)


def test_legacy_metering_rows_validate_but_do_not_replay(tmp_path, monkeypatch):
    from api import models, routes
    monkeypatch.setattr(models, "SESSION_DIR", tmp_path)
    for event in ("token", "metering", "token", "done"):
        run_journal.append_run_event("s", "r", event, {}, session_dir=tmp_path)
    replay = run_journal.read_session_run_events("s", after_event_id="r:2", session_dir=tmp_path)
    assert replay["status"] == "ok"
    assert [e["event_id"] for e in replay["events"]] == ["r:3", "r:4"]
    handler = SimpleNamespace(wfile=io.BytesIO())
    assert routes._replay_run_journal(handler, "r", 0, include_stale=False)
    body = handler.wfile.getvalue().decode()
    assert "event: metering" not in body
    assert "id: r:3" in body and "event: done" in body

