"""Real-server browser regression for compact background continuations.

Imports an isolated synthetic transcript through the actual API, exercises
native disclosure controls, reload and session switching. No provider calls.
Run with Playwright installed; artifacts go to BACKGROUND_ACTIVITY_ARTIFACT_DIR.
"""
from pathlib import Path
import json
import os
import sys
import tempfile

from browser_conversation_lifecycle import _start_webui_server, _terminate_process

ROOT = Path(__file__).resolve().parents[1]


def messages():
    rows = [{'role': 'user', 'content': 'Revise o projeto e entregue o resultado.'},
            {'role': 'assistant', 'content': 'Revisão concluída. O relatório principal permanece aqui.'}]
    for index, code in enumerate([0, 143, 0]):
        rows.extend([
            {'role': 'user', '_source': 'process_wakeup',
             'content': f'[IMPORTANT: Background process proc_fixture_{index} completed (exit_code={code}).\nCommand: run-check-{index}\nOutput:\n' + 'technical output\n' * 50 + ']',
             '_wakeup_meta': {'type': 'completion', 'task_id': f'proc_fixture_{index}', 'command': f'run-check-{index}', 'exit_code': code}},
            {'role': 'assistant', 'content': f'Atualização de background {index}: resultado preservado.'},
        ])
    rows.extend([
        {'role': 'user', '_source': 'process_wakeup', 'content': '[ASYNC DELEGATION BATCH COMPLETE — deleg_fixture]\nTwo tasks finished.'},
        {'role': 'assistant', 'content': 'Conclusão complementar dos subagentes.'},
        {'role': 'user', 'content': 'Agora uma nova pergunta humana.'},
        {'role': 'assistant', 'content': 'Esta resposta pertence à nova pergunta.'},
    ])
    return rows


def main():
    from playwright.sync_api import sync_playwright
    output = Path(os.environ.get('BACKGROUND_ACTIVITY_ARTIFACT_DIR', tempfile.mkdtemp(prefix='webui-background-artifacts-')))
    output.mkdir(parents=True, exist_ok=True)
    results = []
    proc = log = None
    with tempfile.TemporaryDirectory(prefix='webui-background-browser-') as temp:
        state = Path(temp)
        for name in ('home', 'agent', 'workspace'):
            (state / name).mkdir()
        (state / 'agent/run_agent.py').write_text('"""No-inference test stub."""\n')
        env = {k: os.environ[k] for k in ('PATH', 'LANG', 'PLAYWRIGHT_BROWSERS_PATH', 'HERMES_WEBUI_PYTHON') if k in os.environ}
        env.update(HOME=str(state/'home'), HERMES_HOME=str(state/'hermes'), HERMES_BASE_HOME=str(state/'hermes'), HERMES_CONFIG_PATH=str(state/'hermes/config.yaml'), HERMES_WEBUI_STATE_DIR=str(state/'webui'), HERMES_WEBUI_AGENT_DIR=str(state/'agent'), HERMES_WEBUI_DEFAULT_WORKSPACE=str(state/'workspace'), HERMES_WEBUI_SKIP_ONBOARDING='1', HERMES_WEBUI_HOST='127.0.0.1', NO_PROXY='127.0.0.1,localhost')
        try:
            proc, log, _, base = _start_webui_server(ROOT, env, output)
            with sync_playwright() as pw:
                browser = pw.chromium.launch(headless=True, args=['--no-sandbox', '--disable-dev-shm-usage'])
                try:
                    for width, height in [(1440, 900), (522, 1232), (390, 844)]:
                        context = browser.new_context(base_url=base, viewport={'width': width, 'height': height})
                        page = context.new_page()
                        errors = []
                        page.on('pageerror', lambda error, sink=errors: sink.append(str(error)))
                        page.goto('/', wait_until='domcontentloaded')
                        page.wait_for_function("() => typeof loadSession==='function'")
                        sid = page.evaluate("""async messages => {
                          const r=await fetch('/api/session/import',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({title:'Background activity regression',messages})});
                          if(!r.ok) throw new Error('Import failed '+r.status);
                          const d=await r.json();await loadSession(d.session.session_id);return d.session.session_id;
                        }""", messages())
                        try:
                            page.wait_for_selector('.background-activity-group', timeout=10000)
                        except Exception:
                            page.screenshot(path=str(output/f'{width}-before-or-failure.png'), full_page=True)
                            raise
                        assert page.locator('.background-activity-group').count() == 1
                        assert page.locator('.background-activity-group[open]').count() == 0
                        assert page.locator('#msgInner > [data-role="user"]').count() == 2
                        assert page.get_by_text('Revisão concluída. O relatório principal permanece aqui.', exact=True).is_visible()
                        assert page.get_by_text('Esta resposta pertence à nova pergunta.', exact=True).is_visible()
                        assert not page.get_by_text('Conclusão complementar dos subagentes.', exact=True).is_visible()
                        assert page.locator('.background-activity-group.has-failure').count() == 1
                        page.screenshot(path=str(output/f'{width}-collapsed.png'), full_page=True)
                        page.locator('.background-activity-summary').focus()
                        page.keyboard.press('Enter')
                        page.wait_for_selector('.background-activity-group[open]')
                        assert page.get_by_text('Conclusão complementar dos subagentes.', exact=True).is_visible()
                        page.evaluate('renderMessages({preserveScroll:true})')
                        assert page.locator('.background-activity-group[open]').count() == 1
                        page.screenshot(path=str(output/f'{width}-expanded.png'), full_page=True)
                        page.locator('.background-activity-summary').click()
                        page.reload(wait_until='domcontentloaded')
                        page.wait_for_selector('.background-activity-group')
                        assert page.locator('.background-activity-group[open]').count() == 0
                        assert page.get_by_text('Revisão concluída. O relatório principal permanece aqui.', exact=True).is_visible()
                        # Persistence read-back is independent of the display projection.
                        stored = context.request.get(f'/api/session?session_id={sid}&messages=1').json()
                        actual = stored.get('session', stored).get('messages', [])
                        assert any('Conclusão complementar' in str(m.get('content')) for m in actual)
                        geometry = page.evaluate('({width:innerWidth,scrollWidth:document.documentElement.scrollWidth,groups:document.querySelectorAll(".background-activity-group").length})')
                        assert geometry['scrollWidth'] <= width + (16 if width == 1440 else 0)
                        assert not errors, errors
                        results.append({'viewport': [width, height], 'session': sid, 'geometry': geometry, 'errors': errors, 'passed': True})
                        context.close()
                finally:
                    browser.close()
        finally:
            _terminate_process(proc)
            if log:
                log.close()
            (output/'results.json').write_text(json.dumps(results, indent=2))
    print(json.dumps(results, indent=2))
    return 0


if __name__ == '__main__':
    sys.exit(main())
