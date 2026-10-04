"""Display/model separation for compaction replay; synthetic state only."""
import copy
import json
import sqlite3

import pytest

from api import models, routes, streaming
from api.compaction_provenance import is_compaction_replay

HEADER = ('[STILL IN PROGRESS — this is the active request, restated after the '
          'compaction boundary because it was not finished yet. Continue it; do not start over.]')
ORIGINAL = {'role': 'user', 'content': 'Continue the investigation', 'timestamp': 10.0,
            'message_uid': 'human-occurrence'}
FINAL = {'role': 'assistant', 'content': 'Completed result', 'timestamp': 30.0,
         'message_uid': 'final-occurrence'}


def replay(*, marked=True):
    row = {'role': 'user', 'content': HEADER + '\n[Workspace::v1: /fixture]\n' + ORIGINAL['content'],
           'message_uid': ORIGINAL['message_uid'], 'timestamp': 20.0}
    if marked:
        row['display_metadata'] = {'webui_compaction_replay': {
            'version': 1, 'source_message_uid': ORIGINAL['message_uid']}}
    return row


def test_internal_history_and_provider_wire_have_distinct_metadata_contracts():
    row = replay()
    history = streaming._sanitize_messages_for_agent([row])
    assert is_compaction_replay(history[0])
    assert history[0]['content'] == row['content']
    assert streaming._sanitize_messages_for_api(history) == [
        {'role': 'user', 'content': row['content']}]
    context = streaming._deduplicate_context_messages(history)
    assert context[0]['role'] == 'user'
    assert context[0]['content'] == row['content']
    assert is_compaction_replay(context[0])


@pytest.mark.parametrize('marked', [False, True])
def test_settlement_excludes_replay_but_retains_context_and_final(marked):
    original = copy.deepcopy(ORIGINAL)
    result = [original, replay(marked=marked), copy.deepcopy(FINAL)]
    before = copy.deepcopy(result)
    display = streaming._merge_display_messages_after_agent_result(
        [copy.deepcopy(ORIGINAL)], [copy.deepcopy(ORIGINAL)], result, ORIGINAL['content'])
    assert [row['content'] for row in display] == [ORIGINAL['content'], FINAL['content']]
    assert result == before


@pytest.mark.parametrize('marked', [False, True])
def test_compacted_result_has_only_replay_not_original_and_keeps_owned_final(marked):
    checkpoint = {**ORIGINAL, '_active_turn_token': 'current-token'}
    identity = {'token': 'current-token', 'text': ORIGINAL['content'],
                'checkpoint': copy.deepcopy(checkpoint), 'current_turn_user_idx': 1,
                'turn_id': 'current-turn', 'agent_turn_boundary_resolved': True}
    compressed = [{'role': 'user', 'content': '[CONTEXT COMPACTION — REFERENCE ONLY]',
                   '_compressed_summary': True}, replay(marked=marked), copy.deepcopy(FINAL)]
    before = copy.deepcopy(compressed)
    display = streaming._merge_display_messages_after_agent_result(
        [checkpoint], [copy.deepcopy(ORIGINAL)], compressed, ORIGINAL['content'],
        verification_nudge_provenance={'active_turn_identity': identity})
    visible = [row for row in display if not streaming._is_context_compression_marker(row)]
    assert [row['content'] for row in visible] == [ORIGINAL['content'], FINAL['content']]
    assert compressed == before


@pytest.mark.parametrize('marked', [False, True])
def test_already_imported_replay_does_not_remain_after_final_in_settled_display(marked):
    previous = [copy.deepcopy(ORIGINAL), copy.deepcopy(FINAL), replay(marked=marked)]
    before = copy.deepcopy(previous)
    display = streaming._merge_display_messages_after_agent_result(
        previous, previous, previous, ORIGINAL['content'])
    assert [row['content'] for row in display] == [ORIGINAL['content'], FINAL['content']]
    assert previous == before


def test_projection_keeps_raw_indices_and_requires_uid_witness():
    from api.compaction_provenance import project_compaction_replays
    legacy = replay(marked=False)
    quote = {**legacy, 'message_uid': 'real-human-quotation', 'timestamp': 40.0}
    repeat = {**ORIGINAL, 'message_uid': 'real-human-repeat', 'timestamp': 50.0}
    rows = [copy.deepcopy(ORIGINAL), copy.deepcopy(FINAL), legacy, quote, repeat]
    before = copy.deepcopy(rows)
    projected = project_compaction_replays(rows)
    assert len(projected) == len(rows)
    assert projected[0] is rows[0] and projected[1] is rows[1]
    assert is_compaction_replay(projected[2])
    assert not is_compaction_replay(projected[3])
    assert not is_compaction_replay(projected[4])
    assert rows == before
    assert not is_compaction_replay(project_compaction_replays([legacy])[0])


@pytest.mark.parametrize('suffix', ['', '\n\n[Your active task list was preserved across context compression]\n- item',
                                    '\n\n[Skills pruned during compression — reload before acting on these tasks]\ncontext'])
def test_legacy_notice_suffixes(suffix):
    from api.compaction_provenance import project_compaction_replays
    row = replay(marked=False)
    row['content'] += suffix
    assert is_compaction_replay(project_compaction_replays([row], witnesses=[ORIGINAL])[0])


@pytest.mark.parametrize('change', [
    {'message_uid': None}, {'message_uid': 'other'}, {'role': 'assistant'},
    {'content': HEADER + '\nAn unrelated request'}, {'display_kind': 'some-other-contract'},
    {'content': HEADER + '\n' + ORIGINAL['content'] + ' and a real added instruction'},
])
def test_ambiguous_legacy_rows_are_not_hidden(change):
    from api.compaction_provenance import project_compaction_replays
    row = {**replay(marked=False), **change}
    assert not is_compaction_replay(project_compaction_replays([row], witnesses=[ORIGINAL])[0])


def test_conflicting_uid_witness_does_not_authorize_legacy_classification():
    from api.compaction_provenance import project_compaction_replays
    conflicting = {**ORIGINAL, 'content': 'A different original'}
    assert not is_compaction_replay(project_compaction_replays(
        [replay(marked=False)], witnesses=[ORIGINAL, conflicting])[0])


def test_database_projection_keeps_display_provenance_and_raw_revision(tmp_path, monkeypatch):
    path = tmp_path / 'state.db'
    rows = [ORIGINAL, replay(), FINAL]
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE messages (id INTEGER PRIMARY KEY, session_id TEXT, role TEXT, '
                   'content TEXT, timestamp REAL, active INTEGER, message_uid TEXT, '
                   'display_kind TEXT, display_metadata TEXT)')
        for i, row in enumerate(rows, 1):
            db.execute('INSERT INTO messages VALUES (?,?,?,?,?,?,?,?,?)',
                       (i, 'fixture', row['role'], row['content'], row['timestamp'], 1,
                        row['message_uid'], row.get('display_kind'),
                        json.dumps(row['display_metadata']) if 'display_metadata' in row else None))
    monkeypatch.setattr(models, '_active_state_db_path', lambda: path)
    snapshot = models.get_state_db_session_messages('fixture', with_revision=True)
    tail = models.get_state_db_regeneration_tail_snapshot('fixture', 0)['tail']
    assert is_compaction_replay(snapshot.messages[1])
    assert snapshot.messages == tail
    assert snapshot.revision['active_message_count'] == 3
    assert snapshot.revision['max_active_message_id'] == 3


def test_context_reconciliation_and_save_reload_preserve_replay(tmp_path, monkeypatch):
    monkeypatch.setattr(models, 'SESSION_DIR', tmp_path)
    context = [replay(), copy.deepcopy(FINAL)]
    session = models.Session(session_id='replay-roundtrip', messages=[ORIGINAL, FINAL])
    session.context_messages = context
    session.save(skip_index=True)
    loaded = models.Session.load(session.session_id)
    assert is_compaction_replay(loaded.context_messages[0])
    output = models.reconciled_state_db_messages_for_session(
        loaded, prefer_context=True, state_messages=context)
    assert any(is_compaction_replay(row) for row in output)


def test_tail_window_does_not_let_internal_rows_displace_final():
    rows = [ORIGINAL, FINAL] + [replay() for _ in range(40)]
    window, offset = routes._message_window_for_display(rows, msg_limit=1)
    assert offset == 1
    assert window == [FINAL]
    assert rows[1] == FINAL and len(rows) == 42


@pytest.mark.parametrize('legacy', [False, True])
@pytest.mark.parametrize('limit', [None, 1, 30])
def test_real_get_handler_marks_replay_before_window_without_rewriting_sidecar(tmp_path, monkeypatch, legacy, limit):
    from urllib.parse import urlparse
    from tests.test_webui_state_db_reconciliation import _GetHandler, _install_test_session
    rows = [{'role': 'assistant', 'content': f'History {i}', 'message_uid': f'h-{i}',
             'timestamp': float(i + 40)} for i in range(800)]
    rows[0] = copy.deepcopy(ORIGINAL)
    rows[-1] = {**FINAL, 'timestamp': 1000.0}
    rows.append({**replay(marked=not legacy), 'timestamp': 999.0})
    sid = 'compaction-get-fixture'
    _install_test_session(monkeypatch, tmp_path, sid, rows)
    sidecar = tmp_path / 'sessions' / (sid + '.json')
    before = sidecar.read_bytes()
    url = f'/api/session?session_id={sid}&messages=1&resolve_model=0'
    if limit is not None:
        url += f'&msg_limit={limit}'
    handler = _GetHandler(url)
    routes.handle_get(handler, urlparse(url))
    assert handler.status == 200
    data = handler.response_json['session']
    assert data['message_count'] == 801
    if limit is None:
        assert is_compaction_replay(data['messages'][-1])
        assert data['messages'][-2]['content'] == FINAL['content']
    else:
        assert data['messages'][-1]['content'] == FINAL['content']
        assert data['_messages_offset'] == 800 - limit
    assert sidecar.read_bytes() == before


def test_duplicate_replay_enrichment_does_not_discard_producer_provenance():
    incoming = replay()
    existing = replay(marked=False)
    output = models.merge_session_messages_append_only([existing], [incoming])
    assert len(output) == 1 and is_compaction_replay(output[0])
    assert not is_compaction_replay(models.merge_session_messages_append_only([ORIGINAL], [incoming])[0])


@pytest.mark.parametrize('ephemeral', [False, True])
def test_streaming_installs_producer_hook_before_first_conversation(tmp_path, monkeypatch, ephemeral):
    from collections import OrderedDict
    from api import config
    from tests.test_compression_snapshot_revision import _install_streaming_session
    from tests.test_compaction_provenance import Compressor
    sid, stream_id = 'hook-integration', 'hook-stream'
    _install_streaming_session(monkeypatch, tmp_path, sid=sid, stream_id=stream_id,
                               messages=[], context_messages=[])
    monkeypatch.setattr(config, 'SESSION_AGENT_CACHE', OrderedDict())
    captured = []

    class FakeAgent:
        def __init__(self, session_id=None, **kwargs):
            self.session_id = session_id
            self.context_compressor = Compressor()
            self.ephemeral_system_prompt = None
            self._last_error = None

        def run_conversation(self, **kwargs):
            result = self.context_compressor._reappend_inflight_user_task(
                [{'role': 'assistant', 'content': 'summary'}], copy.deepcopy(ORIGINAL))
            captured.append(is_compaction_replay(result[-1]))
            return {'completed': True, 'final_response': 'ok', 'messages': [
                {'role': 'user', 'content': kwargs.get('persist_user_message') or ORIGINAL['content']},
                {'role': 'assistant', 'content': 'ok'}]}

    monkeypatch.setattr(streaming, '_get_ai_agent', lambda: FakeAgent)
    streaming._run_agent_streaming(session_id=sid, msg_text=ORIGINAL['content'],
                                   model='test-model', workspace=str(tmp_path),
                                   stream_id=stream_id, ephemeral=ephemeral)
    assert captured == [True]


def test_real_user_header_quote_remains_visible():
    quote = {**replay(marked=False), 'message_uid': 'literal-human'}
    assert routes._message_counts_as_renderable_for_window(quote)
    display = streaming._merge_display_messages_after_agent_result(
        [ORIGINAL, FINAL], [ORIGINAL, FINAL], [ORIGINAL, FINAL, quote], quote['content'])
    assert display[-1]['content'] == quote['content']
