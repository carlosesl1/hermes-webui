from pathlib import Path

from api.streaming import _compact_for_echo_compare


def test_streaming_initializes_one_run_journal_writer_per_stream():
    src = Path("api/streaming.py").read_text(encoding="utf-8")
    register_idx = src.index("register_active_run(")
    writer_idx = src.index("RunJournalWriter(session_id, stream_id)", register_idx)
    cancel_idx = src.index("cancel_event = threading.Event()", writer_idx)

    assert "from api.run_journal import RunJournalWriter" in src
    assert register_idx < writer_idx < cancel_idx


def test_streaming_journals_sse_events_before_queue_delivery(tmp_path):
    from api.config import StreamChannel
    from api.run_journal import RunJournalWriter, read_run_events
    from api.streaming import _publish_stream_event

    class CheckingChannel(StreamChannel):
        def put_nowait(self, item):
            rows = read_run_events("session", "run", session_dir=tmp_path)["events"]
            assert rows[-1]["event_id"] == item[2]
            assert rows[-1]["payload"] == item[1]
            super().put_nowait(item)

    channel = CheckingChannel()
    subscriber, _ = channel.subscribe_with_snapshot()
    writer = RunJournalWriter("session", "run", session_dir=tmp_path)
    _publish_stream_event(writer, channel, "run", "token", {"text": "hello"})
    assert subscriber.get_nowait() == ("token", {"text": "hello"}, "run:1")



def test_streaming_compacts_all_successful_agent_result_writebacks():
    src = Path("api/streaming.py").read_text(encoding="utf-8")
    run_src = src[src.index("def _run_agent_streaming("):]
    settle_src = src[src.index("def _settle_result_messages("):]
    settle_src = settle_src[: settle_src.index("\n\ndef _current_turn_already_has_visible_assistant_answer(")]

    # Normal completion plus both credential self-heal retry-success paths now
    # route through one shared settlement helper, which owns the compaction.
    assert "def _settle_result_messages(" in src
    assert "_compact_session_image_parts_for_persistence(session)" in settle_src
    assert run_src.count("_settle_result_messages(") == 3


def test_visible_process_echo_compare_ignores_all_whitespace():
    token_text = "先把 issue 4249 拉下来\n\n先看正文和评论"
    interim_text = "先把 issue 4249 拉下来先看正文和评论"

    assert _compact_for_echo_compare(token_text) == _compact_for_echo_compare(interim_text)
