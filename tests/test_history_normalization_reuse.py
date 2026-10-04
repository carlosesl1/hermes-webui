"""Request-local text reuse must save work, never merge occurrence identities."""
import copy
import hashlib
import random
import sqlite3
from collections import Counter, OrderedDict
from types import SimpleNamespace
from urllib.parse import urlparse

import pytest

from api import models, routes


def _uncached_content(message, *, normalize_workspace_prefix, cache=None):
    from api.streaming import _strip_workspace_prefix

    value = ' '.join(str(message.get('content') or '').split())
    if normalize_workspace_prefix and str(message.get('role') or '') == 'user':
        value = ' '.join(_strip_workspace_prefix(value, include_legacy=True).split())
    return value


def test_merge_normalizes_equal_text_once_without_collapsing_occurrences(monkeypatch):
    sidecar = [dict(role='user' if i % 2 == 0 else 'assistant',
                    content='repeated\t text ' * 256, timestamp=i + 1,
                    message_uid=f'local-{i}') for i in range(40)]
    state = copy.deepcopy(sidecar)
    visits = Counter()
    normalize = models._normalized_session_message_content

    def counted(row):
        visits[str(row.get('content') or '')] += 1
        return normalize(row)

    monkeypatch.setattr(models, '_normalized_session_message_content', counted)
    actual = models.merge_session_messages_append_only(sidecar, state)
    assert actual == sidecar
    assert len(actual) == 40
    assert max(visits.values()) == 1, visits
    # Same objects, same length/timestamps, different content on a later call.
    for row in sidecar + state:
        row['content'] = row['content'].replace('repeated', 'modified')
    visits.clear()
    actual = models.merge_session_messages_append_only(sidecar, state)
    assert all(row['content'].startswith('modified') for row in actual)
    assert max(visits.values()) == 1


@pytest.mark.parametrize('before,changed_prefix,cache_hit', [
    (None, False, False), (330, False, False),
    (None, True, False), (330, True, False), (None, False, True),
])
def test_get_reuses_text_between_prefix_proof_and_merge(tmp_path, monkeypatch, before, changed_prefix, cache_hit):
    from api import config

    session_dir = tmp_path / 'sessions'
    session_dir.mkdir()
    for module in (models, routes, config):
        monkeypatch.setattr(module, 'SESSION_DIR', session_dir)
    db = tmp_path / 'state.db'
    monkeypatch.setattr(models, '_active_state_db_path', lambda: db)
    monkeypatch.setattr(models, 'SESSIONS', OrderedDict())
    monkeypatch.setattr(routes, '_display_merge_cache', OrderedDict())
    monkeypatch.setattr(routes, '_lineage_display_cache', OrderedDict())
    rows = [dict(role='user' if i % 2 == 0 else 'assistant',
                 content=f'Message {i}: ' + 'payload\t ' * 128,
                 timestamp=1700000000 + i) for i in range(360)]
    session = models.Session(session_id='reuse-history', messages=rows,
                             model='offline-test', context_length=131072,
                             source_tag='webui', created_at=1700000000,
                             updated_at=1700000359)
    session.save(touch_updated_at=False, skip_index=True)
    digest = hashlib.sha256(session.path.read_bytes()).hexdigest()
    with sqlite3.connect(db) as conn:
        conn.executescript('''
            CREATE TABLE sessions(id TEXT PRIMARY KEY, title TEXT, model TEXT,
                source TEXT, started_at REAL, last_activity_at REAL, message_count INTEGER);
            CREATE TABLE messages(id INTEGER PRIMARY KEY, session_id TEXT,
                role TEXT, content TEXT, timestamp REAL, active INTEGER, tool_calls TEXT);
        ''')
        conn.execute('INSERT INTO sessions VALUES(?,?,?,?,?,?,?)',
                     (session.session_id, 'Synthetic', 'offline-test', 'webui',
                      1700000000, 1700000359, len(rows)))
        conn.executemany('INSERT INTO messages(session_id,role,content,timestamp,active) VALUES(?,?,?,?,1)',
                         [(session.session_id, r['role'], r['content'], r['timestamp']) for r in rows])
        if changed_prefix:
            conn.execute("UPDATE messages SET content='recovered older row' WHERE id=1")
    visits = Counter()
    normalize = models._normalized_session_message_content

    def counted(row):
        visits[str(row.get('content') or '')] += 1
        return normalize(row)

    monkeypatch.setattr(models, '_normalized_session_message_content', counted)
    captured = {}
    memos = []
    prove_prefix = routes._state_db_since_timestamp_for_limited_display
    window = routes._message_window_for_display

    def capture_memo(*args, **kwargs):
        result = prove_prefix(*args, **kwargs)
        memo = kwargs.get('_comparison_cache')
        if before is None:
            assert memo, 'initial-tail fixture must populate the prefix memo'
        memos.append(memo)
        return result

    def checked_window(*args, **kwargs):
        assert memos and all(not memo for memo in memos), 'release memo before response projection'
        return window(*args, **kwargs)

    monkeypatch.setattr(routes, '_state_db_since_timestamp_for_limited_display', capture_memo)
    monkeypatch.setattr(routes, '_message_window_for_display', checked_window)
    if cache_hit:
        monkeypatch.setattr(routes, '_display_merge_cached_messages', lambda *args, **kwargs: rows)

    def respond(_handler, data, status=200, **kwargs):
        assert status == 200, data
        captured.update(data)

    monkeypatch.setattr(routes, 'j', respond)
    query = '/api/session?session_id=reuse-history&messages=1&msg_limit=30&resolve_model=0'
    if before is not None:
        query += f'&msg_before={before}&msg_boundary=1'
    routes.handle_get(SimpleNamespace(_safe_webui_print=lambda *_: None), urlparse(query))
    payload = captured['session']
    assert len(payload['messages']) == 30
    assert payload['_messages_offset'] == (330 + int(changed_prefix) if before is None else 300)
    assert payload['message_count'] == 360 + int(changed_prefix)
    assert hashlib.sha256(session.path.read_bytes()).hexdigest() == digest
    assert max(visits.values()) == 1, visits


@pytest.mark.parametrize('lineage', [False, True])
def test_differential_identity_tools_repeats_and_lineage(monkeypatch, lineage):
    rng = random.Random(84205)
    original = models._session_message_comparison_content
    texts = ['same', 'same\t text', 'same\ntext', '[Workspace::v1: /a]\nsame',
             '[Workspace: /old]\nsame', '🙂\u00a0中文', '', None,
             [{'type': 'text', 'text': 'native'}, {'type': 'image_url', 'image_url': {'url': 'data:image/png;base64,AA=='}}]]
    for _ in range(250):
        left, right = [], []
        for target in (left, right):
            for i in range(rng.randrange(1, 12)):
                row = dict(role=rng.choice(['user', 'assistant', 'tool']),
                           content=copy.deepcopy(rng.choice(texts)),
                           timestamp=rng.choice([None, 1, 1.1, 2, '3.0']))
                for key, choices in [('message_uid', ['one', 'two', 1, '']),
                                     ('id', ['a', 'b']), ('api_content', ['wire-a', 'wire-b']),
                                     ('_source', ['background', 'user']),
                                     ('_active_turn_token', ['turn-a', 'turn-b']),
                                     ('tool_call_id', ['call-a', 'call-b'])]:
                    if rng.randrange(3) == 0:
                        row[key] = rng.choice(choices)
                if rng.randrange(3) == 0:
                    row['tool_calls'] = [{'id': rng.choice(['call-a', 'call-b']),
                                          'function': {'name': 'terminal', 'arguments': '{"x":1}'}}]
                target.append(row)
        right[:0] = copy.deepcopy(left[:2])
        options = dict(truncation_watermark=rng.choice([None, 0, 1, 2]),
                       truncation_boundary=rng.choice([None, 0, 1]))

        def run(helper, left=left, right=right, options=options):
            monkeypatch.setattr(models, '_session_message_comparison_content', helper)
            a, b = copy.deepcopy((left, right))
            if lineage:
                parent = SimpleNamespace(session_id='parent', messages=a)
                tip = SimpleNamespace(session_id='tip', parent_session_id='parent',
                                      session_source='webui', messages=b, **options)
                monkeypatch.setattr(routes, 'get_session', lambda *args, **kwargs: parent)
                result = routes._merged_webui_lineage_messages_for_display(tip, b)
            else:
                result = models.merge_session_messages_append_only(a, b, **options)
            return result, a, b

        assert run(original) == run(_uncached_content)
