"""Render-only budget regressions; synthetic payloads, no live state."""
import copy

from api.render_payload import (
    FIELD_CHARS, PAGE_CHARS, ROW_CHARS, bounded_render_messages,
)

NOTICE = '[Content truncated in paginated preview; open the full transcript to inspect the complete content.]'


def payload_chars(value, active=False, key=''):
    from api.render_payload import IDENTITY_KEYS, TEXT_KEYS
    if isinstance(value, str):
        return len(value) if active and key not in IDENTITY_KEYS else 0
    if isinstance(value, list):
        return sum(payload_chars(v, active) for v in value)
    if isinstance(value, dict):
        return sum(payload_chars(v, active or k in TEXT_KEYS, k) for k, v in value.items())
    return 0


def test_early_tools_and_scenes_do_not_starve_late_conversation():
    rows = []
    for i in range(80):
        rows.append({'id': str(i), 'role': 'tool', 'source': 'terminal',
                     'content': 'T' * 50000,
                     '_anchor_activity_scene': {'id': 'scene', 'x': 14, 'items': [
                         {'type': 'tool', 'output': {'nested': ['S' * 50000]}}]}})
    rows += [{'id': 'user', 'role': 'user', 'source': 'telegram', 'content': 'Please explain the result.'},
             {'id': 'answer', 'role': 'assistant', 'content': 'The operation succeeded.'}]
    original = copy.deepcopy(rows)
    budget = [PAGE_CHARS]
    result = bounded_render_messages(rows, page_budget=budget)
    assert result[-2:] == original[-2:]
    assert [r['id'] for r in result] == [r['id'] for r in original]
    assert result[0]['source'] == 'terminal'
    assert result[0]['_anchor_activity_scene']['items'][0]['type'] == 'tool'
    assert result[0]['_anchor_activity_scene']['x'] == 14
    assert rows == original
    assert payload_chars(result) == PAGE_CHARS - budget[0]
    assert 0 <= budget[0] <= PAGE_CHARS
    assert all(payload_chars(row) <= ROW_CHARS for row in result)
    result[0]['_anchor_activity_scene']['items'][0]['output']['nested'].append('changed')
    assert rows == original


def test_short_prose_gets_fair_share_after_many_huge_assistant_messages():
    rows = [{'role': 'assistant', 'content': 'A' * 100000} for _ in range(80)]
    rows += [{'role': 'user', 'content': NOTICE}, {'role': 'assistant', 'content': 'Done.'}]
    result = bounded_render_messages(rows)
    assert result[-2:] == rows[-2:]
    assert all(r['content'] and len(r['content']) <= FIELD_CHARS for r in result)
    assert all(r['_preview_content_truncated'] is True for r in result[:-2])
    assert payload_chars(result) <= PAGE_CHARS


def test_content_blocks_prioritized_over_reasoning_tools_and_scene_regardless_of_key_order():
    row = {'role': 'assistant', 'reasoning': 'R' * 100000,
           '_anchor_activity_scene': {'items': [{'output': 'S' * 100000} for _ in range(20)]},
           'content': [{'type': 'tool_use', 'id': 'c1', 'input': {'nested': 'I' * 100000}},
                       {'type': 'text', 'text': 'Readable answer.'},
                       {'type': 'image_url', 'image_url': {'url': 'https://example.test/img'}}]}
    original = copy.deepcopy(row)
    result = bounded_render_messages([row], page_budget=[40])[0]
    assert result['content'][1] == row['content'][1]
    assert result['content'][0]['id'] == 'c1'
    assert result['content'][2] == row['content'][2]
    assert result['_content_truncated'] is True
    assert result['_preview_content_truncated'] is False
    assert payload_chars(result) <= 40
    assert row == original


def test_exhausted_budget_metadata_and_no_injected_instructions():
    rows = [{'role': 'user', 'content': 'Hello'},
            {'role': 'assistant', 'content': [{'type': 'text', 'text': 'Answer'}]},
            {'role': 'assistant', 'content': '', 'reasoning': 'hidden'},
            {'role': 'tool', 'content': 'tool output'}]
    result = bounded_render_messages(rows, page_budget=[0])
    assert result[0]['content'] == ''
    assert result[1]['content'][0]['text'] == ''
    assert result[0]['_content_original_chars'] == 5
    assert [r['_preview_content_truncated'] for r in result] == [True, True, False, False]
    assert all(r['_content_truncated'] is True for r in result)
    assert payload_chars(result) == 0


def test_content_block_fairness_row_cap_and_shared_legacy_budget():
    rows = [{'role': 'assistant', 'content': [{'type': 'text', 'text': 'X' * 50000} for _ in range(20)] +
             [{'type': 'text', 'text': 'Short ending.'}]}]
    budget = [ROW_CHARS + 50]
    result = bounded_render_messages(rows, page_budget=budget)
    assert result[0]['content'][-1]['text'] == 'Short ending.'
    assert payload_chars(result) == ROW_CHARS
    tools = bounded_render_messages([{'snippet': 'Z' * 1000}], page_budget=budget)
    assert payload_chars(tools) == 50
    assert budget == [0]
