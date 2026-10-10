"""Synthetic, read-only query-plan regression; never opens an Agent database."""
import json
from pathlib import Path
import sqlite3
import time

import pytest

from api.agent_sessions import active_message_table, message_stats_table


def _schema(conn, *, legacy=False, index='idx_messages_session_active'):
    conn.execute('CREATE TABLE messages (id INTEGER PRIMARY KEY, session_id TEXT, '
                 'role TEXT, content TEXT, timestamp REAL'
                 + (')' if legacy else ', active INTEGER)'))
    conn.execute('CREATE INDEX idx_messages_session_id ON messages(session_id, id)')
    if not legacy and index:
        quoted = '"' + index.replace('"', '""') + '"'
        conn.execute(f'CREATE INDEX {quoted} ON messages(session_id, active, timestamp)')


def _query(table, *, sessions=('target',), since=None, limit=None, active=True):
    params = list(sessions)
    sql = f'SELECT * FROM {table} WHERE session_id IN ({",".join("?" for _ in sessions)})'
    if active:
        sql += ' AND (active IS NULL OR active != 0)'
    if since is not None:
        sql += ' AND (timestamp IS NULL OR timestamp >= ?)'
        params.append(since)
    if limit is not None:
        sql += ' ORDER BY id DESC LIMIT ?'
        params.append(limit)
        sql = f'SELECT * FROM ({sql}) ORDER BY id ASC'
    else:
        sql += ' ORDER BY id ASC'
    return sql, params


def _plan(conn, sql, params):
    return [r[3] for r in conn.execute('EXPLAIN QUERY PLAN ' + sql, params)]


def _io():
    # Linux-only diagnostic, not the portable regression assertion. rchar counts
    # SQLite pread work even when the kernel page cache is warm.
    try:
        return dict((k, int(v)) for k, v in
                    (line.split(':') for line in Path('/proc/self/io').read_text().splitlines()))
    except OSError:
        return {}


def _measure(conn, sql, params):
    ticks = 0

    def progress():
        nonlocal ticks
        ticks += 1
        return 0

    before = _io()
    conn.set_progress_handler(progress, 100)
    start = time.perf_counter()
    try:
        rows = conn.execute(sql, params).fetchall()
    finally:
        conn.set_progress_handler(None, 0)
    elapsed = time.perf_counter() - start
    after = _io()
    return rows, {'seconds': elapsed, 'vm_steps_approx': ticks * 100,
                  'read_chars': after.get('rchar', 0) - before.get('rchar', 0),
                  'read_syscalls': after.get('syscr', 0) - before.get('syscr', 0)}


def test_payload_heavy_active_read_plan(tmp_path):
    path = tmp_path / 'synthetic.db'
    with sqlite3.connect(path) as writer:
        _schema(writer)
        # active follows overflow-heavy content, like compacted Agent rows.
        payload = 'synthetic archived payload ' + 'x' * 35500
        writer.executemany('INSERT INTO messages VALUES (?, ?, ?, ?, ?, ?)',
                           ((i, 'target', 'assistant', payload, float(i),
                             1 if i % 100 == 0 else 0) for i in range(1, 2001)))
    reports = {}
    results = {}
    for label in ('baseline', 'optimized'):
        with sqlite3.connect(path.as_uri() + '?mode=ro', uri=True) as conn:
            conn.execute('PRAGMA cache_size=-256')
            table = 'messages' if label == 'baseline' else active_message_table(conn)
            sql, params = _query(table)
            plan = _plan(conn, sql, params)
            results[label], reports[label] = _measure(conn, sql, params)
            reports[label]['plan'] = plan
            if label == 'optimized':
                assert any('idx_messages_session_active' in p for p in plan), plan
                # Prove the active test's Column op reads index cursor 2 rather
                # than the payload table: resolve cursor from its root page.
                root = conn.execute("SELECT rootpage FROM sqlite_master WHERE "
                                    "name='idx_messages_session_active'").fetchone()[0]
                ops = conn.execute('EXPLAIN ' + sql, params).fetchall()
                cursors = {op[2] for op in ops if op[1] == 'OpenRead' and op[3] == root}
                assert any(op[1] == 'Column' and op[2] in cursors and op[3] == 1
                           for op in ops)
    assert results['baseline'] == results['optimized']
    assert len(results['optimized']) == 20
    print('\nactive-read benchmark: ' + json.dumps(reports, sort_keys=True))


@pytest.mark.parametrize('sessions', [('target',), ('target', 'parent')])
@pytest.mark.parametrize('since', [None, 3.0])
@pytest.mark.parametrize('limit', [None, 1, 4])
def test_preserves_values_append_order_floor_and_tail(sessions, since, limit):
    with sqlite3.connect(':memory:') as conn:
        _schema(conn)
        conn.executemany('INSERT INTO messages VALUES (?, ?, ?, ?, ?, ?)', [
            (i, sid, 'assistant', str(i), timestamp, active)
            for i, (sid, timestamp, active) in enumerate([
                ('target', 9, 0), ('target', 8, None), ('target', 7, -1),
                ('target', 6, 2), ('target', None, 1), ('target', 2, 1),
                ('parent', 1, -3), ('other', 99, 1), ('target', 0, 0),
            ], 1)
        ])
        options = dict(sessions=sessions, since=since, limit=limit)
        expected = conn.execute(*_query('messages', **options)).fetchall()
        actual = conn.execute(*_query(active_message_table(conn), **options)).fetchall()
        assert actual == expected
        if since is None and limit is None and sessions == ('target',):
            assert [row[-1] for row in actual] == [None, -1, 2, 1, 1]


@pytest.mark.parametrize('ddl', [
    '',
    'CREATE INDEX partial ON messages(session_id, active, timestamp) WHERE active=1',
    'CREATE INDEX expression ON messages(session_id, (active+0), timestamp)',
    'CREATE INDEX wrong_leader ON messages(active, session_id, timestamp)',
    'CREATE INDEX collated ON messages(session_id COLLATE NOCASE, active, timestamp)',
    'CREATE INDEX incomplete ON messages(session_id, active)',
])
def test_unsafe_or_incomplete_indexes_fall_back(ddl):
    with sqlite3.connect(':memory:') as conn:
        _schema(conn, index=None)
        if ddl:
            conn.execute(ddl)
        assert active_message_table(conn) == 'messages'


def test_legacy_and_inspection_failure_fall_back():
    with sqlite3.connect(':memory:') as conn:
        _schema(conn, legacy=True)
        assert active_message_table(conn) == 'messages'
        conn.set_authorizer(lambda *args: sqlite3.SQLITE_DENY)
        assert active_message_table(conn) == 'messages'


def test_quoted_index_and_schema_changes_are_not_cached():
    with sqlite3.connect(':memory:') as conn:
        _schema(conn, index='odd " index')
        table = active_message_table(conn)
        assert conn.execute(*_query(table)).fetchall() == []
        assert 'INDEXED BY' in table
        assert message_stats_table(conn, has_timestamp=True) == table
        conn.execute('DROP INDEX "odd "" index"')
        assert active_message_table(conn) == 'messages'


@pytest.mark.parametrize('limit', [None, 2])
@pytest.mark.parametrize('include_inactive', [False, True])
def test_real_reader_uses_hint_only_for_active_projection(tmp_path, monkeypatch, limit, include_inactive):
    from api import models

    path = tmp_path / 'reader.db'
    with sqlite3.connect(path) as writer:
        _schema(writer)
        writer.executemany('INSERT INTO messages VALUES (?, ?, ?, ?, ?, ?)',
                           [(i, 'target', 'assistant', str(i), float(10-i), active)
                            for i, active in enumerate([0, 1, None, -1, 2], 1)])
    monkeypatch.setattr(models, '_active_state_db_path', lambda: path)
    statements = []
    def connect(db_path):
        conn = sqlite3.connect(db_path.as_uri() + '?mode=ro', uri=True)
        conn.set_trace_callback(statements.append)
        return conn
    monkeypatch.setattr(models, 'open_state_db_readonly', connect)
    result = models.get_state_db_session_messages('target', limit=limit,
                                                  include_inactive=include_inactive)
    ids = [1, 2, 3, 4, 5] if include_inactive else [2, 3, 4, 5]
    assert [int(row['content']) for row in result] == (ids[-limit:] if limit else ids)
    selected = [q for q in statements if 'SELECT' in q and 'session_id IN' in q]
    assert len(selected) == 1
    assert ('INDEXED BY' in selected[0]) is not include_inactive


@pytest.mark.parametrize('limit', [None, 1])
@pytest.mark.parametrize('activities', [(1, 2), (2, 1)])
def test_reader_keeps_legacy_timestamp_tie_order(tmp_path, monkeypatch, limit, activities):
    from api import models

    path = tmp_path / 'legacy.db'
    with sqlite3.connect(path) as writer:
        writer.executescript('CREATE TABLE messages(session_id, role, content, active, timestamp);'
                             'CREATE INDEX session_time ON messages(session_id, timestamp);'
                             'CREATE INDEX session_active ON messages(session_id, active, timestamp);')
        writer.executemany('INSERT INTO messages VALUES (?, ?, ?, ?, ?)',
                           [('s', 'user', 'question', activities[0], 10),
                            ('s', 'assistant', 'answer', activities[1], 10)])
        suffix = " WHERE session_id='s' AND (active IS NULL OR active != 0)"
        sql = 'SELECT content, timestamp FROM messages' + suffix
        sql = (f'SELECT * FROM ({sql} ORDER BY timestamp DESC LIMIT 1) ORDER BY timestamp ASC'
               if limit else sql + ' ORDER BY timestamp ASC')
        expected = [r[0] for r in writer.execute(sql)]
    monkeypatch.setattr(models, '_active_state_db_path', lambda: path)
    actual = models.get_state_db_session_messages('s', limit=limit)
    assert [r['content'] for r in actual] == expected


def test_no_id_column_required():
    with sqlite3.connect(':memory:') as conn:
        conn.executescript('CREATE TABLE messages(session_id, content, active, timestamp);'
                           'CREATE INDEX full_active ON messages(session_id, active, timestamp);'
                           "INSERT INTO messages VALUES ('s', 'keep', -1, 2),"
                           "('s', 'archive', 0, 1), ('s', 'null', NULL, NULL);")
        suffix = " WHERE session_id='s' AND (active IS NULL OR active != 0) ORDER BY timestamp"
        assert conn.execute('SELECT * FROM ' + active_message_table(conn) + suffix).fetchall() == \
            conn.execute('SELECT * FROM messages' + suffix).fetchall()
