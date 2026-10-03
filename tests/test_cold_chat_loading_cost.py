"""Cold loading cost gates: no clocks, real content and canonical semantics."""
import copy
import json
import tracemalloc
from collections import Counter

import pytest

from api import models


def test_merge_normalizes_each_prepared_row_once_per_invocation(monkeypatch):
    sidecar = [{'role': 'user' if i % 2 == 0 else 'assistant',
                'content': f'message {i} ' + 'word \t next\n' * 256,
                'timestamp': i + 1} for i in range(40)]
    state = copy.deepcopy(sidecar)
    for row in state:
        if row['role'] == 'user':
            row['content'] = '[Workspace::v1: /workspace]\n' + row['content']
    original = copy.deepcopy((sidecar, state))
    visits = Counter()
    normalizer = models._normalized_session_message_content

    def counted(message):
        visits[id(message)] += 1
        return normalizer(message)

    monkeypatch.setattr(models, '_normalized_session_message_content', counted)
    merged = models.merge_session_messages_append_only(sidecar, state)
    assert merged == sidecar
    assert all(row is original_row for row, original_row in zip(merged, sidecar, strict=True))
    assert (sidecar, state) == original
    assert max(visits.values()) == 1, visits
    # No stale cache survives a request, even for same-size/same-timestamp edits.
    sidecar[0]['content'] = 'edited question'
    state[0]['content'] = '[Workspace::v1: /workspace]\nedited question'
    visits.clear()
    second = models.merge_session_messages_append_only(sidecar, state)
    assert second[0]['content'] == 'edited question'
    assert max(visits.values()) == 1


@pytest.mark.parametrize('content', [
    None, '', 0, ['a', {'text': '中文\t🙂'}], {'a': ['b']},
    '  a\t b\n\rc\v d\f\x1ce\x85f\u00a0g\u2028h\u2029i  ',
    '[Workspace::v1: /path with spaces]\nUser\ttext',
    '[Workspace: /legacy]\nUser\ttext',
    '[Workspace::v1: /literal]\n[Workspace::v1: /nested]\nword',
])
@pytest.mark.parametrize('role', ['user', 'assistant', 'tool'])
@pytest.mark.parametrize('normalize', [False, True])
def test_comparison_keys_match_original_normalization(content, role, normalize):
    from api.streaming import _strip_workspace_prefix

    row = {'role': role, 'content': content, 'api_content': 'private provider text',
           'tool_call_id': 'call-1', 'tool_name': 'tool',
           'tool_calls': [{'id': 'call-1', 'function': {'arguments': '{"x": 1}'}}]}
    value = ' '.join(str(content or '').split())
    if role == 'user' and normalize:
        value = ' '.join(_strip_workspace_prefix(value, include_legacy=True).split())
    expected_content = models._session_message_key_with_sidecar((role, value, 'call-1', 'tool'), row)
    expected_visible = models._session_message_key_with_sidecar((role, value, json.dumps(row['tool_calls'], sort_keys=True, default=str)), row)
    assert models._session_message_content_key(row, normalize_workspace_prefix=normalize) == expected_content
    assert models._session_message_visible_key(row, normalize_workspace_prefix=normalize) == expected_visible


def test_cold_session_load_avoids_third_whole_file_buffer(tmp_path, monkeypatch):
    monkeypatch.setattr(models, 'SESSION_DIR', tmp_path)
    session = models.Session(session_id='cold-memory', title='memory',
                             messages=[{'role': 'assistant', 'content': 'x' * (8 * 1024 * 1024)}])
    session.save(skip_index=True)
    size = session.path.stat().st_size
    tracemalloc.start()
    try:
        loaded = models.Session.load(session.session_id)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert loaded.messages == session.messages
    # Allow metadata/object overhead; old bytes + decoded JSON + parsed string
    # exceeds 3x whereas releasing the bytes before parsing stays near 2x.
    assert peak < size * 2.5, {'peak_bytes': peak, 'file_bytes': size}


@pytest.mark.parametrize('encoding', ['utf-8', 'utf-8-sig', 'utf-16', 'utf-32'])
def test_cold_load_preserves_json_binary_encoding_contract(tmp_path, monkeypatch, encoding):
    monkeypatch.setattr(models, 'SESSION_DIR', tmp_path)
    data = {'session_id': 'encoding', 'messages': [{'role': 'assistant', 'content': 'ação 中文 🙂\ud800'}],
            'context_messages': [{'role': 'user', 'content': 'context'}],
            'anchor_scene_index': {}}
    path = tmp_path / 'encoding.json'
    raw = json.dumps(data).encode(encoding)
    path.write_bytes(raw)
    loaded = models.Session.load('encoding')
    assert loaded.messages == data['messages']
    assert loaded.context_messages == data['context_messages']
    assert path.read_bytes() == raw


@pytest.mark.parametrize('raw', [b'', b'{bad', b'\xff', b'\x00',
                                 b'\xef\xbb\xbf\xef\xbb\xbf{}',
                                 '{\"a\": 1} extra'.encode('utf-16'),
                                 b'{\"content\": \"\x00\"}'])
def test_cold_load_preserves_binary_json_errors(tmp_path, monkeypatch, raw):
    monkeypatch.setattr(models, 'SESSION_DIR', tmp_path)
    path = tmp_path / 'invalid.json'
    path.write_bytes(raw)
    with pytest.raises((ValueError, UnicodeError)) as original:
        json.loads(raw)
    with pytest.raises(type(original.value)) as current:
        models.Session.load('invalid')
    assert str(current.value) == str(original.value)
    assert path.read_bytes() == raw


def test_merge_matches_uncached_reference_over_adversarial_inputs(monkeypatch):
    import random
    from api.streaming import _strip_workspace_prefix

    rng = random.Random(74291)
    actual_helper = models._session_message_comparison_content

    def reference(message, *, normalize_workspace_prefix, cache=None):
        value = ' '.join(str(message.get('content') or '').split())
        if message.get('role') == 'user' and normalize_workspace_prefix:
            value = ' '.join(_strip_workspace_prefix(value, include_legacy=True).split())
        return value

    texts = ['', 'same', 'same\t text', '[Workspace::v1: /a]\nsame',
             '[Workspace: /legacy]\nliteral', [{'type': 'text', 'text': '🙂\n中文'}], None]
    for _ in range(500):
        sidecar, state = [], []
        for target in [sidecar, state]:
            for _index in range(rng.randrange(9)):
                row = {'role': rng.choice(['user', 'assistant', 'tool']),
                       'content': copy.deepcopy(rng.choice(texts))}
                if rng.choice([True, False]):
                    row['timestamp'] = rng.choice([None, 1, 1.1, 2, '3.0'])
                if rng.randrange(3) == 0:
                    row['id'] = rng.choice(['one', 'two'])
                if rng.randrange(4) == 0:
                    row['api_content'] = rng.choice(['private-one', 'private-two'])
                if rng.randrange(3) == 0:
                    row['tool_call_id'] = 'call-' + str(rng.randrange(2))
                target.append(row)
        if sidecar and rng.choice([True, False]):
            state[:0] = copy.deepcopy(sidecar[:2])
        options = dict(truncation_watermark=rng.choice([None, 0, 1, 2]),
                       truncation_boundary=rng.choice([None, 0, 1]))
        current_inputs = copy.deepcopy((sidecar, state))
        reference_inputs = copy.deepcopy((sidecar, state))
        monkeypatch.setattr(models, '_session_message_comparison_content', actual_helper)
        current = models.merge_session_messages_append_only(*current_inputs, **options)
        monkeypatch.setattr(models, '_session_message_comparison_content', reference)
        expected = models.merge_session_messages_append_only(*reference_inputs, **options)
        assert current == expected
        # Reconciler-owned api_content/metadata writeback must match too.
        assert current_inputs == reference_inputs
