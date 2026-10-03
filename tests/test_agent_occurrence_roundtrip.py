"""Durable occurrence identity across the WebUI/Agent boundary (no provider)."""
import copy
import sqlite3

import pytest

from api import models, streaming


@pytest.mark.parametrize('role', ['user', 'assistant'])
def test_internal_history_keeps_occurrence_but_not_persistence_authority(role):
    original = {'role': role, 'content': 'A', 'message_uid': 'occurrence-A',
                'timestamp': 0.0, 'api_content': 'provider-A', 'id': 7,
                '_row_id': 10, '_state_db_row_id': 10, '_db_persisted': True,
                '_db_row_snapshot': 'stale', '_canonical_row': {'id': 10},
                '_source': 'webui', 'attachments': [{'name': 'private'}]}
    before = copy.deepcopy(original)
    history = streaming._sanitize_messages_for_agent([
        {'role': 'assistant', 'content': 'error', '_error': True}, original,
        {'role': 'tool', 'tool_call_id': 'orphan', 'content': 'ignored'},
    ])
    assert history == [{key: original[key] for key in
                        ('role', 'content', 'message_uid', 'timestamp', 'api_content')}]
    assert streaming._sanitize_messages_for_api(history) == [{'role': role, 'content': 'A'}]
    assert original == before


def _database(path, rows, *, uid_column=True):
    with sqlite3.connect(path) as conn:
        conn.execute('CREATE TABLE messages (id INTEGER PRIMARY KEY, session_id TEXT, '
                     'role TEXT, content TEXT, timestamp REAL, active INTEGER, api_content TEXT'
                     + (', message_uid TEXT' if uid_column else '') + ')')
        for index, row in enumerate(rows, 1):
            fields = [index, 'fixture', row['role'], row['content'], row['timestamp'], 1,
                      row.get('api_content')]
            if uid_column:
                fields.append(row.get('message_uid'))
            conn.execute('INSERT INTO messages VALUES (' + ','.join('?' for _ in fields) + ')', fields)


@pytest.mark.parametrize('uid_column', [False, True])
def test_state_projection_and_regeneration_tail_preserve_uid(tmp_path, monkeypatch, uid_column):
    rows = [{'role': role, 'content': 'A', 'timestamp': 100.0, 'message_uid': role}
            for role in ('user', 'assistant')]
    path = tmp_path / 'state.db'
    _database(path, rows, uid_column=uid_column)
    monkeypatch.setattr(models, '_active_state_db_path', lambda: path)
    full = models.get_state_db_session_messages('fixture')
    tail = models.get_state_db_regeneration_tail_snapshot('fixture', 0)['tail']
    assert full == tail
    assert [row.get('message_uid') for row in full] == (
        ['user', 'assistant'] if uid_column else [None, None])


@pytest.mark.parametrize('role', ['user', 'assistant'])
def test_uid_conflict_preserves_same_timestamp_a_b_a(role):
    first = {'role': role, 'content': 'A', 'timestamp': 100.0, 'message_uid': 'first'}
    middle = {'role': role, 'content': 'B', 'timestamp': 100.0, 'message_uid': 'middle'}
    last = {**first, 'message_uid': 'last'}
    expected = [first, middle, last]
    assert models.merge_session_messages_append_only([first, middle], [last]) == expected
    assert models.merge_session_messages_append_only([], expected) == expected
    assert streaming._deduplicate_context_messages(expected) == expected
    assert not models._cross_source_replay_match(first, last, allow_legacy=True)


@pytest.mark.parametrize('role', ['user', 'assistant'])
def test_compaction_physical_row_change_is_not_new_occurrence(role):
    original = {'role': role, 'content': 'A', 'timestamp': 100.0, 'message_uid': 'first',
                '_row_id': 1, 'api_content': 'provider-A'}
    carried = {**original, '_row_id': 9}
    assert models.merge_session_messages_append_only([original], [carried]) == [original]
    # A shared physical row id or timestamp cannot overrule a conflicting UID.
    conflicting = {**original, 'message_uid': 'other'}
    assert models.merge_session_messages_append_only([original], [conflicting]) == [original, conflicting]


def test_agent_return_merge_and_save_reload_keep_identity(tmp_path, monkeypatch):
    monkeypatch.setattr(models, 'SESSION_DIR', tmp_path)
    previous = [
        {'role': 'user', 'content': 'Earlier question', 'timestamp': 100.0, 'message_uid': 'question'},
        {'role': 'assistant', 'content': 'Earlier saved answer', 'timestamp': 110.0,
         'message_uid': 'answer', 'api_content': 'provider answer'},
    ]
    for index, row in enumerate(previous, 1):
        row['id'] = index
    session = models.Session(session_id='roundtrip', messages=copy.deepcopy(previous))
    returned = streaming._sanitize_messages_for_agent(previous) + [
        {'role': 'user', 'content': 'New question', 'timestamp': 200.0, 'message_uid': 'new-question'},
        {'role': 'assistant', 'content': 'New answer', 'timestamp': 210.0, 'message_uid': 'new-answer'},
    ]
    streaming._settle_result_messages(session, copy.deepcopy(previous), copy.deepcopy(previous),
                                      returned, 'New question', 'webui', None)
    assert [row.get('message_uid') for row in session.context_messages] == [
        'question', 'answer', 'new-question', 'new-answer']
    # Same shape as the audited compacted tail; durable row IDs change but UIDs do not.
    carried = [{'role': 'assistant', 'content': 'Earlier saved answer', 'timestamp': 110.0,
                'message_uid': 'answer', 'api_content': 'provider answer'}]
    session.messages = models.merge_session_messages_append_only(session.messages, carried)
    assert [row['content'] for row in session.messages].count('Earlier saved answer') == 1
    session.save(skip_index=True)
    loaded = models.Session.load(session.session_id)
    assert loaded.messages == session.messages
    assert loaded.context_messages == session.context_messages
    assert [row.get('message_uid') for row in loaded.messages] == [
        'question', 'answer', 'new-question', 'new-answer']


@pytest.mark.parametrize('role', ['user', 'assistant'])
def test_matching_legacy_sidecar_adopts_durable_uid(role):
    local = {'role': role, 'content': 'A', 'timestamp': 100.0}
    durable = {**local, 'message_uid': 'durable'}
    merged = models.merge_session_messages_append_only([local], [durable])
    assert len(merged) == 1
    assert merged[0].get('message_uid') == 'durable'
