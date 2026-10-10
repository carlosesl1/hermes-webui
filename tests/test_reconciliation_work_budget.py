"""Bound repeated pure work without weakening occurrence reconciliation."""
import copy
from collections import Counter

import pytest

from api import models


def test_empty_state_does_not_prepare_history_keys(monkeypatch):
    rows = [{'role': 'assistant', 'content': 'history', 'timestamp': i} for i in range(50)]
    def unused(*args, **kwargs):
        pytest.fail('empty state must not build comparison keys')
    monkeypatch.setattr(models, '_session_message_merge_key', unused)
    assert models.merge_session_messages_append_only(rows, []) == rows


def test_resolved_provider_rows_do_not_build_unused_fallback_maps(monkeypatch):
    rows = [{'role': 'user', 'content': f'text {i}', 'message_uid': f'uid-{i}',
             'id': f'id-{i}', '_row_id': i, 'timestamp': i} for i in range(80)]
    state = [dict(rows[-1], api_content='wire')]
    original = models._state_db_row_identity_details
    calls = Counter()
    def counted(row):
        calls[row['id']] += 1
        return original(row)
    monkeypatch.setattr(models, '_state_db_row_identity_details', counted)
    models._reconcile_api_content_sidecars(rows, state)
    assert rows[-1]['api_content'] == 'wire'
    assert calls['id-0'] == 1, calls


@pytest.mark.parametrize('identified', [False, True])
@pytest.mark.parametrize('timestamp', [0, None, 1.25, '1.25'])
def test_tool_key_serialized_once_per_row(monkeypatch, identified, timestamp):
    rows = [{'role': 'assistant', 'content': f'call {i}', 'timestamp': timestamp,
             'message_uid': f'uid-{i}', 'api_content': f'wire-{i}',
             'tool_calls': [{'id': f'call-{i}', 'function': {'name': 'terminal', 'arguments': '{}'}}]}
            for i in range(8)]
    if identified:
        for i, row in enumerate(rows):
            row['id'] = f'row-{i}'
    state = copy.deepcopy(rows)
    original = models.json.dumps
    calls = Counter()
    def counted(value, *args, **kwargs):
        if isinstance(value, list) and value and isinstance(value[0], dict) and 'function' in value[0]:
            calls[id(value)] += 1
        return original(value, *args, **kwargs)
    monkeypatch.setattr(models.json, 'dumps', counted)
    expected = copy.deepcopy(rows)
    actual = models.merge_session_messages_append_only(rows, state)
    assert actual == expected
    assert rows == expected
    assert calls and max(calls.values()) == 1, calls
