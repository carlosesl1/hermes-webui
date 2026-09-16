"""Isolated real-server worker/API proof, synthetic data, no model calls.

Run with the repository Python. Waits for one real scheduled maintenance tick.
Pass AUTO_ARCHIVE_WORKER_REPORT=/path/report.json to retain measured results.
"""
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request

ROOT = Path(__file__).resolve().parents[1]


def main():
    report_path = os.environ.get('AUTO_ARCHIVE_WORKER_REPORT')
    with tempfile.TemporaryDirectory(prefix='archive-worker-proof-') as tmp:
        state = Path(tmp)
        env = {key: os.environ[key] for key in ('PATH', 'LANG') if key in os.environ}
        for name in ('home', 'hermes', 'workspace', 'no-agent'):
            (state / name).mkdir()
        (state / 'no-agent/run_agent.py').write_text('"""No agent in this proof."""\n')
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            port = sock.getsockname()[1]
        env.update(HOME=str(state / 'home'), HERMES_HOME=str(state / 'hermes'),
                   HERMES_BASE_HOME=str(state / 'hermes'), HERMES_CONFIG_PATH=str(state / 'hermes/config.yaml'),
                   HERMES_WEBUI_STATE_DIR=str(state / 'webui'), HERMES_WEBUI_DEFAULT_WORKSPACE=str(state / 'workspace'),
                   HERMES_WEBUI_AGENT_DIR=str(state / 'no-agent'), HERMES_WEBUI_SKIP_ONBOARDING='1',
                   HERMES_WEBUI_HOST='127.0.0.1', HERMES_WEBUI_PORT=str(port),
                   PYTHONDONTWRITEBYTECODE='1', NO_PROXY='127.0.0.1,localhost')
        os.environ.clear()
        os.environ.update(env)
        sys.path.insert(0, str(ROOT))
        from api import config, models
        assert models.SESSION_DIR.is_relative_to(state)
        models.SESSION_DIR.mkdir(parents=True, exist_ok=True)
        original = {}
        now = time.time()
        ids = [f'archive-proof-old-{i}' for i in range(8)] + ['archive-proof-recent', 'archive-proof-pinned']
        for sid in ids:
            stamp = now if sid.endswith('recent') else now - 40 * 86400
            session = models.Session(session_id=sid, title=sid, profile='default', workspace=str(state / 'workspace'),
                                     source_tag='webui', pinned=sid.endswith('pinned'),
                                     created_at=stamp, updated_at=stamp,
                                     messages=[{'role': 'user', 'content': 'Synthetic preserved transcript ' * 20}],
                                     context_messages=[{'role': 'assistant', 'content': 'Exact context'}])
            session.save(touch_updated_at=False)
            raw = session.path.read_bytes()
            original[sid] = hashlib.sha256(raw[raw.index(b'  "messages":'):]).hexdigest()
        base = f'http://127.0.0.1:{port}'

        def api(path, data=None):
            request = urllib.request.Request(base + path,
                data=None if data is None else json.dumps(data).encode(), headers={'Content-Type': 'application/json'})
            started = time.perf_counter()
            with urllib.request.urlopen(request, timeout=10) as response:
                raw = response.read()
            return json.loads(raw), {'bytes': len(raw), 'seconds': round(time.perf_counter() - started, 6)}

        with (state / 'server.log').open('w') as log:
            proc = subprocess.Popen([sys.executable, str(ROOT / 'server.py')], cwd=ROOT, env=env,
                                    stdout=log, stderr=subprocess.STDOUT)
            try:
                deadline = time.monotonic() + 25
                while True:
                    try:
                        api('/health')
                        break
                    except OSError:
                        assert time.monotonic() < deadline, (state / 'server.log').read_text()[-4000:]
                        time.sleep(.1)
                assert api('/api/settings')[0]['auto_archive_days'] == 0
                before, before_measure = api('/api/sessions')
                before_ids = {row['session_id'] for row in before['sessions']}
                assert before_ids == set(ids), before_ids
                api('/api/settings', {'auto_archive_days': 30})
                assert api('/api/settings')[0]['auto_archive_days'] == 30
                deadline = time.monotonic() + 100
                while True:
                    rows = [json.loads((config.SESSION_DIR / (sid + '.json')).read_text()) for sid in ids]
                    if sum(bool(row['archived']) for row in rows) == 8:
                        break
                    assert time.monotonic() < deadline, (state / 'server.log').read_text()[-4000:]
                    time.sleep(.2)
                after, after_measure = api('/api/sessions')
                assert {row['session_id'] for row in after['sessions']} == {'archive-proof-recent', 'archive-proof-pinned'}
                all_rows, _ = api('/api/sessions?include_archived=1')
                assert {row['session_id'] for row in all_rows['sessions']} == set(ids)
                for sid in ids:
                    raw = (config.SESSION_DIR / (sid + '.json')).read_bytes()
                    assert hashlib.sha256(raw[raw.index(b'  "messages":'):]).hexdigest() == original[sid]
                sid = ids[0]
                api('/api/session/archive', {'session_id': sid, 'archived': False})
                restored, _ = api('/api/session?session_id=' + sid + '&messages=0')
                row = json.loads((config.SESSION_DIR / (sid + '.json')).read_text())
                assert not row['archived'] and row['auto_archive_restored_at'] >= now
                assert not restored.get('session', restored).get('archived')
                api('/api/settings', {'auto_archive_days': 0})
                assert api('/api/settings')[0]['auto_archive_days'] == 0
                report = {'status': 'PASS', 'data': 'isolated synthetic sessions, real API/server/scheduler',
                          'before': {'visible': len(before['sessions']), **before_measure},
                          'after': {'visible': len(after['sessions']), **after_measure},
                          'archived': 8, 'all_preserved': len(all_rows['sessions']),
                          'suffix_hashes_unchanged': len(original), 'restore_grace': True,
                          'worker_real_tick': True, 'disabled_readback': True,
                          'latency_scope': 'single local request, not a production speed claim'}
                if report_path:
                    Path(report_path).write_text(json.dumps(report, indent=2) + '\n')
                print(json.dumps(report))
            finally:
                proc.terminate()
                try:
                    proc.wait(timeout=8)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=5)


if __name__ == '__main__':
    main()
