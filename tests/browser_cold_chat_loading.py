"""Real isolated WebUI + Chromium: cold long-chat open, older page, A-B-A, reload.

Synthetic persisted sessions; no provider calls, fake HTTP transcript routes or
frontend-state injection. Reuses the lifecycle gate's readiness/teardown helpers.
"""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

from playwright.sync_api import sync_playwright
from browser_conversation_lifecycle import _start_webui_server, _terminate_process

ROOT = Path(__file__).resolve().parents[1]
FINAL = 'FINAL COLD CHAT ANSWER: the complete saved answer is visible.'
SEED = '''
import hashlib, json
from api.models import Session, SESSION_DIR
SESSION_DIR.mkdir(parents=True, exist_ok=True)
rows = [{'role': 'user' if i % 2 == 0 else 'assistant', 'timestamp': 1700000000+i,
         'content': f'Historical message {i}: ' + 'fixture text ' * 512} for i in range(3000)]
rows[-1]['content'] = __FINAL_LITERAL__
session = Session(session_id='cold-browser', title='Long conversation QA', messages=rows,
                  context_messages=rows[-100:], source_tag='webui', context_length=131072,
                  model='offline-test')
session.save()
other = Session(session_id='other-browser', title='Other conversation QA',
                messages=[{'role':'assistant','content':'Other conversation answer','timestamp':1700005000}],
                source_tag='webui',context_length=131072,model='offline-test')
other.save()
print(json.dumps({'path':str(session.path),'sha256':hashlib.sha256(session.path.read_bytes()).hexdigest(),
                  'bytes':session.path.stat().st_size}))
'''.replace('__FINAL_LITERAL__', repr(FINAL))


def main():
    out = Path(os.environ.get('COLD_BROWSER_EVIDENCE') or tempfile.mkdtemp(prefix='cold-browser-evidence-'))
    out.mkdir(parents=True, exist_ok=True)
    reports = []
    with tempfile.TemporaryDirectory(prefix='cold-browser-state-') as tmp:
        base = Path(tmp)
        env = {key: value for key, value in os.environ.items()
               if not key.endswith('_API_KEY') and key not in {
                   'PYTHONPATH', 'API_SERVER_KEY', 'HERMES_WEBUI_PASSWORD',
                   'HERMES_WEBUI_EXTENSION_DIR', 'HERMES_WEBUI_EXTENSION_MANIFEST'}}
        env.update(HOME=str(base/'home'), HERMES_HOME=str(base/'hermes'),
                   HERMES_BASE_HOME=str(base/'hermes'), HERMES_CONFIG_PATH=str(base/'hermes/config.yaml'),
                   HERMES_WEBUI_STATE_DIR=str(base/'state'), HERMES_WEBUI_AGENT_DIR=str(base/'absent-core'),
                   HERMES_WEBUI_HOST='127.0.0.1', HERMES_WEBUI_SKIP_ONBOARDING='1',
                   HERMES_WEBUI_DEFAULT_WORKSPACE=str(base/'workspace'),
                   HERMES_WEBUI_CHAT_BACKEND='gateway', HERMES_WEBUI_GATEWAY_BASE_URL='http://127.0.0.1:1',
                   NO_PROXY='127.0.0.1,localhost', no_proxy='127.0.0.1,localhost')
        (base/'workspace').mkdir()
        (base/'absent-core').mkdir()
        (base/'absent-core/run_agent.py').write_text(
            '"""No provider runtime: persisted-conversation browser gate."""\n', encoding='utf-8')
        seeded = subprocess.run([sys.executable, '-c', SEED], cwd=ROOT, env=env,
                                capture_output=True, text=True, timeout=45)
        if seeded.returncode:
            raise RuntimeError(f'Fixture setup failed: {seeded.stderr}')
        fixture = json.loads(seeded.stdout.strip().splitlines()[-1])
        # Restart the test server for each size, so neither uses an in-memory Session.
        for width, height in [(1440, 900), (522, 1232)]:
            proc = log = None
            errors = []
            try:
                proc, log, _, url = _start_webui_server(ROOT, env, out)
                with sync_playwright() as pw:
                    browser = pw.chromium.launch(headless=True, args=['--no-sandbox', '--disable-dev-shm-usage'])
                    context = browser.new_context(viewport={'width': width, 'height': height})
                    page = context.new_page()
                    page.on('pageerror', lambda error, errors=errors: errors.append(str(error)))
                    page.goto(url + '/session/cold-browser', wait_until='domcontentloaded')
                    page.wait_for_function('text => typeof S!=="undefined" && S.session?.session_id==="cold-browser" && $("msgInner").innerText.includes(text)', arg=FINAL, timeout=30000)
                    initial = page.evaluate('() => ({loaded:S.messages.length,total:S.session.message_count,offset:_oldestIdx})')
                    assert initial == {'loaded': 30, 'total': 3000, 'offset': 2970}, initial
                    page.screenshot(path=str(out/f'{width}-opened.png'))
                    page.locator('#loadOlderIndicator').click()
                    page.wait_for_function('() => !_loadingOlder && _oldestIdx<2970', timeout=30000)
                    older = page.evaluate('() => ({loaded:S.messages.length,offset:_oldestIdx})')
                    assert older['loaded'] >= 60 and older['offset'] + older['loaded'] == 3000, older
                    page.screenshot(path=str(out/f'{width}-older.png'))
                    page.goto(url + '/session/other-browser', wait_until='domcontentloaded')
                    page.wait_for_function('() => typeof S!=="undefined" && S.session?.session_id==="other-browser" && $("msgInner").innerText.includes("Other conversation answer")')
                    page.go_back(wait_until='domcontentloaded')
                    page.wait_for_function('text => typeof S!=="undefined" && S.session?.session_id==="cold-browser" && $("msgInner").innerText.includes(text)', arg=FINAL, timeout=30000)
                    page.reload(wait_until='domcontentloaded')
                    page.wait_for_function('text => typeof S!=="undefined" && $("msgInner").innerText.includes(text)', arg=FINAL, timeout=30000)
                    overflow = page.evaluate('() => document.documentElement.scrollWidth-innerWidth')
                    assert overflow == 0 and not errors, (overflow, errors)
                    reports.append({'width': width, 'height': height, 'initial': initial,
                                    'older': older, 'navigation_and_reload': True, 'overflow': overflow, 'js_errors': errors})
                    context.close()
                    browser.close()
            finally:
                _terminate_process(proc)
                if log:
                    log.close()
        assert hashlib.sha256(Path(fixture['path']).read_bytes()).hexdigest() == fixture['sha256']
    result = {'fixture_bytes': fixture['bytes'], 'canonical_unchanged': True, 'results': reports}
    (out/'results.json').write_text(json.dumps(result, indent=2))
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
