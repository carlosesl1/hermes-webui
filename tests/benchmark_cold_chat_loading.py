"""Offline, opt-in cold Session/HTTP-handler benchmark; not a pytest timing gate.

Compare --repo checkouts with the SAME script and interpreter. Application caches
are cleared per trial; filesystem/page caches are not. Synthetic native sidecar +
SQLite, no provider, socket, production home, or database migration. Temporary
state is removed on exit. Imports and fixture generation are outside the timers.
"""
import argparse
import gc
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import statistics
import sys
import tempfile
import time
import tracemalloc
from types import SimpleNamespace
from unittest.mock import patch
from urllib.parse import urlparse


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--trials', type=int, default=5)
    parser.add_argument('--rows', type=int, default=3000)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--tool-history', action='store_true', help='Tool-heavy rows with occurrence/provider metadata')
    parser.add_argument('--active-tail', type=int, default=0, help='Archive older SQLite rows (0 keeps all active)')
    parser.add_argument('--handler-allocations', action='store_true',
                        help='Trace complete GET allocations separately; timings include tracing overhead')
    args = parser.parse_args()
    if not 1 <= args.trials <= 10 or not 100 <= args.rows <= 20000 or not 0 <= args.active_tail <= args.rows:
        parser.error('trials must be 1..10; rows 100..20000; active-tail 0..rows')
    with tempfile.TemporaryDirectory(prefix='cold-chat-bench-') as directory:
        trial = Path(directory)
        for key, sub in [('HOME', 'home'), ('HERMES_HOME', 'hermes'), ('HERMES_BASE_HOME', 'hermes'),
                         ('HERMES_WEBUI_STATE_DIR', 'state'), ('HERMES_WEBUI_AGENT_DIR', 'absent-core')]:
            os.environ[key] = str(trial / sub)
        os.environ['HERMES_CONFIG_PATH'] = str(trial / 'hermes/config.yaml')
        sys.path.insert(0, str(args.repo.resolve()))
        from api import models, routes

        assert models.SESSION_DIR.is_relative_to(trial), models.SESSION_DIR
        models.SESSION_DIR.mkdir(parents=True, exist_ok=True)
        db = models._active_state_db_path()
        assert db.is_relative_to(trial), db
        db.parent.mkdir(parents=True, exist_ok=True)
        rows = [{'role': 'user' if i % 2 == 0 else 'assistant',
                 'content': f'Message {i}: ' + 'payload ' * 1024,
                 'timestamp': 1700000000 + i} for i in range(args.rows)]
        if args.tool_history:
            for i, row in enumerate(rows):
                row['message_uid'] = f'occurrence-{i}'
                row['role'] = 'user' if i % 30 == 0 else ('assistant' if i % 3 == 0 else 'tool')
                if row['role'] == 'assistant':
                    row['tool_calls'] = [{'id': f'call-{i}', 'function': {'name': 'terminal', 'arguments': 'payload ' * 512}}]
                    row['api_content'] = f'provider text {i}'
                elif row['role'] == 'tool':
                    row['tool_call_id'] = f'call-{i - i % 3}'
                    row['tool_name'] = 'terminal'
        session = models.Session(session_id='cold-benchmark', title='Synthetic cold load',
                                 workspace='/synthetic', created_at=1700000000, updated_at=1700000000 + args.rows,
                                 messages=rows, context_messages=rows[-1000:],
                                 context_length=131072, source_tag='webui', model='offline-test')
        session.save(touch_updated_at=False, skip_index=True)
        path = session.path
        canonical_digest = hashlib.sha256(path.read_bytes()).hexdigest()
        with sqlite3.connect(db) as conn:
            conn.executescript('''
                CREATE TABLE sessions(id TEXT PRIMARY KEY, title TEXT, model TEXT, source TEXT,
                    started_at REAL, last_activity_at REAL, message_count INTEGER);
                CREATE TABLE messages(id INTEGER PRIMARY KEY, session_id TEXT, role TEXT,
                    content TEXT, timestamp REAL, active INTEGER, tool_calls TEXT, message_uid TEXT, api_content TEXT, tool_call_id TEXT, tool_name TEXT);
                CREATE INDEX session_order ON messages(session_id, id);
                CREATE INDEX session_timestamp ON messages(session_id, timestamp);
                CREATE INDEX session_active ON messages(session_id, active, timestamp);
            ''')
            conn.execute('INSERT INTO sessions VALUES(?,?,?,?,?,?,?)',
                         (session.session_id, session.title, session.model, 'webui',
                          session.created_at, session.updated_at, len(rows)))
            conn.executemany('INSERT INTO messages(session_id,role,content,timestamp,active,tool_calls,message_uid,api_content,tool_call_id,tool_name) VALUES(?,?,?,?,?,?,?,?,?,?)',
                             [(session.session_id, r['role'], r['content'], r['timestamp'],
                               int(not args.active_tail or i >= len(rows) - args.active_tail),
                               json.dumps(r['tool_calls']) if r.get('tool_calls') else None,
                               r.get('message_uid'), r.get('api_content'), r.get('tool_call_id'), r.get('tool_name'))
                              for i, r in enumerate(rows)])
        sid = session.session_id
        original_first_content = rows[0]['content']
        del rows, session
        results = {'rows': args.rows, 'sidecar_bytes': path.stat().st_size, 'trials': args.trials,
                   'tool_history': args.tool_history, 'active_tail': args.active_tail,
                   'repo': str(args.repo.resolve()), 'canonical_sha256': canonical_digest, 'cases': {}}

        def cold():
            models.SESSIONS.clear()
            routes._display_merge_cache.clear()
            routes._lineage_display_cache.clear()
            gc.collect()

        def measured(call, *, allocations=False):
            samples, cpu_samples, peaks, digests = [], [], [], []
            for _ in range(args.trials):
                cold()
                if allocations:
                    tracemalloc.start()
                start = time.perf_counter()
                cpu_start = time.process_time()
                data = call()
                cpu_samples.append(time.process_time() - cpu_start)
                samples.append(time.perf_counter() - start)
                if allocations:
                    peaks.append(tracemalloc.get_traced_memory()[1])
                    tracemalloc.stop()
                digests.append(hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest())
                del data
            assert len(set(digests)) == 1, digests
            return {'seconds': samples, 'median_seconds': statistics.median(samples),
                    'cpu_seconds': cpu_samples, 'median_cpu_seconds': statistics.median(cpu_samples),
                    'peak_bytes': peaks, 'median_peak_bytes': statistics.median(peaks) if peaks else None,
                    'output_sha256': digests[0]}

        def load():
            loaded = models.Session.load(sid)
            return {'messages': loaded.messages, 'context_messages': loaded.context_messages}

        capture = {}

        def respond(_handler, data, status=200, **_kwargs):
            assert status == 200, (status, data.get('error'))
            capture['data'] = data

        def detail(*, before=None):
            query = f'/api/session?session_id={sid}&messages=1&msg_limit=30&resolve_model=0'
            if before is not None:
                query += f'&msg_before={before}&msg_boundary=1'
            routes.handle_get(SimpleNamespace(_safe_webui_print=lambda *_: None), urlparse(query))
            data = capture.pop('data')['session']
            assert len(data['messages']) >= 30 if args.tool_history else len(data['messages']) == 30
            return {key: data.get(key) for key in ['messages', 'tool_calls', 'message_count',
                                                   '_messages_offset', '_messages_truncated', 'todo_state']}

        results['cases']['session_load_allocations'] = measured(load, allocations=True)
        with patch.object(routes, 'j', respond):
            for case in ['matched_prefix', 'changed_prefix']:
                with sqlite3.connect(db) as conn:
                    content = original_first_content if case == 'matched_prefix' else 'Recovered older row: ' + original_first_content
                    conn.execute('UPDATE messages SET content=? WHERE id=1', (content,))
                results['cases'][case + '_tail'] = measured(detail, allocations=args.handler_allocations)
                results['cases'][case + '_older_page'] = measured(
                    lambda: detail(before=args.rows - 30), allocations=args.handler_allocations)
        results['canonical_unchanged'] = canonical_digest == hashlib.sha256(path.read_bytes()).hexdigest()
        assert results['canonical_unchanged']
        encoded = json.dumps(results, indent=2) + '\n'
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(encoded)
        print(encoded)


if __name__ == '__main__':
    main()
