"""Behavioral cost regressions: no timing thresholds or real agent state."""
import copy
import json

import api.routes as routes
from tests.test_session_tail_payload import _FakeSession, _invoke


class CountedList(list):
    visited = 0

    def __iter__(self):
        for value in super().__iter__():
            self.visited += 1
            yield value

    def __getitem__(self, key):
        self.visited += len(range(*key.indices(len(self)))) if isinstance(key, slice) else 1
        return super().__getitem__(key)


def test_thirty_row_selector_does_not_copy_or_visit_full_prefix():
    rows = CountedList({"role": "assistant", "content": str(i)} for i in range(50_000))
    window, offset = routes._message_window_for_display(rows, msg_limit=30)
    assert offset == 49_970
    assert len(window) == 30
    assert rows.visited < 100


def test_trailing_tool_must_belong_to_selected_window_not_old_prefix():
    rows = [
        {"role": "assistant", "content": "old", "tool_calls": [{"id": "old"}]},
        {"role": "user", "content": "new"},
        {"role": "assistant", "content": "answer"},
        {"role": "tool", "tool_call_id": "old", "content": "orphan in this window"},
    ]
    window, offset = routes._message_window_for_display(rows, msg_limit=2)
    assert offset == 1
    assert window == rows[1:3]


def test_cursor_page_keeps_hidden_gap_for_absolute_indices():
    rows = [{"role": "assistant", "content": "old"},
            {"role": "tool", "content": "orphan"},
            {"role": "assistant", "content": "new"}]
    session = _FakeSession(rows)
    page = _invoke(session, "session_id=tail_payload_001&messages=1&resolve_model=0&msg_limit=1&msg_before=2&msg_boundary=1")
    assert page["messages"] == rows[:2]
    assert page["_messages_offset"] + len(page["messages"]) == 2


def test_render_budget_does_not_clip_attachment_urls_or_string_timestamps():
    row = {"role": "assistant", "content": "x" * 50_000,
           "attachments": [{"url": "data:image/png;base64," + "a" * 40_000}],
           "timestamp": "123.5",
           "_anchor_activity_scene": {"content": [{"type": "image_url", "image_url": {"url": "data:image/png;base64," + "b" * 40_000}}]}}
    projected = routes._messages_for_limited_payload([row])[0]
    assert projected["attachments"] == row["attachments"]
    assert projected["timestamp"] == row["timestamp"]
    assert projected["_anchor_activity_scene"] == row["_anchor_activity_scene"]


def test_visible_and_nested_render_content_bounded_without_mutating_canonical():
    rows = [
        {"role": "user", "content": "u" * (12 * 1024 * 1024)},
        {"role": "assistant", "content": [{"type": "text", "text": "a" * 200_000}],
         "reasoning": "r" * 200_000,
         "_anchor_activity_scene": {"version": 1, "stream_id": "stream-1", "segments": [
             {"seq": 7, "type": "tool", "tool_call_id": "call-1", "text": "s" * 200_000}]}},
        {"role": "tool", "tool_call_id": "call-1", "content": "t" * 200_000},
    ]
    original = copy.deepcopy(rows)
    projected = routes._messages_for_limited_payload(rows)
    assert len(json.dumps(projected).encode()) < 150_000
    assert rows == original
    assert all(row['_content_truncated'] for row in projected)
    assert projected[1]['_anchor_activity_scene']['stream_id'] == 'stream-1'
    assert projected[1]['_anchor_activity_scene']['segments'][0]['seq'] == 7
    assert projected[2]['tool_call_id'] == 'call-1'


def test_limited_route_has_honest_full_content_link_and_full_load_remains_lossless():
    session = _FakeSession([{"role": "assistant", "content": "z" * 200_000}])
    limited = _invoke(session)
    row = limited['messages'][0]
    assert row['_content_truncated']
    assert limited['_full_content_url'].startswith('/api/session?')
    full = _invoke(session, limited['_full_content_url'].split('?', 1)[1])
    assert full['messages'][0]['content'] == session.messages[0]['content']
    assert not full['messages'][0].get('_content_truncated')
