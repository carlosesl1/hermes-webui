"""Occurrence identity is not message text (#7587 / #6310)."""
import copy
import pytest


@pytest.mark.parametrize('metadata', [({'id': 1}, {'id': 3}), ({'timestamp': 1000}, {'timestamp': 2000}), ({}, {})])
def test_repeated_continue_survives_context_display_and_replay(metadata):
    from api.streaming import _deduplicate_context_messages, _strip_replayed_prefix
    from api.models import merge_session_messages_append_only, state_db_delta_after_context
    first = {'role': 'user', 'content': 'continue', **metadata[0]}
    answer = {'role': 'assistant', 'content': 'working', 'id': 2}
    second = {'role': 'user', 'content': 'continue', **metadata[1]}
    history = [first, answer, second]
    before = copy.deepcopy(history)
    assert _deduplicate_context_messages(history) == history
    assert _strip_replayed_prefix([first], [second]) == [second]
    assert merge_session_messages_append_only([first, answer], [second]) == history
    assert state_db_delta_after_context([first, answer], [second, dict(answer)]) == [second, answer]
    assert history == before


def test_same_authoritative_record_deduplicates():
    from api.streaming import _deduplicate_context_messages, _strip_replayed_prefix
    from api.models import merge_session_messages_append_only
    row = {'id': 5, 'role': 'user', 'content': 'continue'}
    assert _deduplicate_context_messages([row, dict(row)]) == [row]
    assert _strip_replayed_prefix([row], [dict(row)]) == []
    assert merge_session_messages_append_only([row], [dict(row)]) == [row]


@pytest.mark.parametrize('result,expected', [
    ({'error': {'code': 'permission_denied'}}, True),
    ('{"success":false,"message":"denied"}', True),
    ({'status': 'cancelled'}, True),
    ({'exit_code': 1, 'output': ''}, True),
    ({'success': True, 'output': 'the word error is present'}, False),
    ('{"success":true,"output":"error"}', False),
    ('Error is a word in this successful output', False),
])
def test_tool_result_classification(result, expected):
    from api.tool_outcomes import tool_result_is_error
    assert tool_result_is_error('terminal', result) is expected


def test_visible_duplicate_does_not_discard_distinct_api_sidecars():
    from api.models import _session_message_visible_key, _matching_visible_duplicate
    first = {'id': 1, 'role': 'user', 'content': 'continue', 'api_content': 'original'}
    second = {**first, 'content': 'continue now', 'api_content': 'different'}
    assert _matching_visible_duplicate(_session_message_visible_key(second),
                                       {_session_message_visible_key(first)}) is None


def test_gateway_cancelled_tool_is_complete_and_failed():
    from api.gateway_chat import _gateway_tool_progress_event
    event, payload = _gateway_tool_progress_event({'name': 'terminal', 'status': 'cancelled', 'id': 'call-1'})
    assert event == 'tool_complete'
    assert payload['is_error'] is True


@pytest.mark.parametrize('result,expected', [
    ('{"success":false,"message":"denied"}', True),
    ('{"success":true,"output":"error"}', False),
])
def test_real_callback_live_snapshot_and_journal(monkeypatch, tmp_path, result, expected):
    import json
    import queue
    from collections import OrderedDict
    import api.config as config
    import api.models as models
    import api.profiles as profiles
    import api.streaming as streaming

    session_dir = tmp_path / 'sessions'
    session_dir.mkdir()
    for module in (models, config, streaming):
        monkeypatch.setattr(module, 'SESSION_DIR', session_dir)
    monkeypatch.setattr(models, 'SESSION_INDEX_FILE', session_dir / '_index.json')
    monkeypatch.setattr(models, 'SESSIONS', OrderedDict())
    monkeypatch.setattr(models, '_active_state_db_path', lambda: tmp_path / 'state.db')
    monkeypatch.setattr(profiles, 'get_active_hermes_home', lambda: tmp_path)
    for name in ('STREAMS', 'CANCEL_FLAGS', 'AGENT_INSTANCES', 'SESSION_AGENT_LOCKS', 'STREAM_LIVE_TOOL_CALLS'):
        monkeypatch.setattr(config, name, {})
        if hasattr(streaming, name):
            monkeypatch.setattr(streaming, name, getattr(config, name))
    sid, stream_id = 'occurrence-callback', 'occurrence-stream'
    session = models.Session(session_id=sid, workspace=str(tmp_path), model='test-model')
    session.active_stream_id = stream_id
    session.pending_user_message = 'run'
    session.save(touch_updated_at=False)
    models.SESSIONS[sid] = session
    captured = {}

    # A real class is required: turn_owned_agent_class reads __module__/__name__
    # and constructor signature; a callable Mock bypasses that runtime contract.
    class FakeAgent:
        def __init__(self, tool_start_callback=None, tool_complete_callback=None, **kwargs):
            self.start = tool_start_callback
            self.complete = tool_complete_callback
            self.context_compressor = None
            self.ephemeral_system_prompt = None
            self.session_id = sid

        def run_conversation(self, **kwargs):
            self.start('call-1', 'terminal', {'command': 'fixture'})
            self.complete('call-1', 'terminal', {}, result)
            captured['live'] = copy.deepcopy(config.STREAM_LIVE_TOOL_CALLS[stream_id])
            return {'completed': True, 'final_response': 'ok', 'messages': [
                {'role': 'user', 'content': 'run'}, {'role': 'assistant', 'content': 'ok'}]}

    monkeypatch.setattr(streaming, '_get_ai_agent', lambda: FakeAgent)
    monkeypatch.setattr(streaming, 'resolve_model_provider', lambda *a, **kw: ('test-model', None, None))
    monkeypatch.setattr(streaming, 'get_config', lambda: {})
    monkeypatch.setattr(config, 'get_config', lambda: {})
    monkeypatch.setattr(config, '_resolve_cli_toolsets', lambda *a, **kw: [])
    config.STREAMS[stream_id] = queue.Queue()
    streaming._run_agent_streaming(session_id=sid, msg_text='run', model='test-model',
                                   workspace=str(tmp_path), stream_id=stream_id, attachments=[])
    assert captured['live'][0]['done'] is True
    assert captured['live'][0]['is_error'] is expected
    journals = list(session_dir.rglob('*.jsonl'))
    events = [json.loads(line) for path in journals for line in path.read_text().splitlines()]
    completed = [e for e in events if e.get('event') == 'tool_complete']
    assert len(completed) == 1
    assert completed[0]['payload']['is_error'] is expected
