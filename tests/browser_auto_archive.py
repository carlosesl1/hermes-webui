"""Real Settings UI regression: isolated server, no credentials or model calls.

Run with Playwright installed. AUTO_ARCHIVE_QA_DIR selects screenshot output.
Set AUTO_ARCHIVE_MOCK_SETTINGS=1 only when testing before backend integration;
that mode explicitly reports mocked settings persistence, not backend coverage.
"""
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile

from playwright.sync_api import sync_playwright, expect
import browser_smoke as smoke

ROOT = Path(__file__).resolve().parents[1]
OUT = Path(os.environ.get('AUTO_ARCHIVE_QA_DIR', tempfile.mkdtemp(prefix='archive-qa-')))
OUT.mkdir(parents=True, exist_ok=True)
MOCK = os.environ.get('AUTO_ARCHIVE_MOCK_SETTINGS') == '1'


def main():
    with tempfile.TemporaryDirectory(prefix='archive-browser-') as tmp:
        state = Path(tmp)
        env = {k: os.environ[k] for k in ('PATH', 'LANG', 'PLAYWRIGHT_BROWSERS_PATH') if k in os.environ}
        for name in ('home', 'hermes', 'workspace', 'no-agent'):
            (state / name).mkdir()
        (state / 'no-agent/run_agent.py').write_text('"""Agent-free fixture."""\n')
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            port = sock.getsockname()[1]
        smoke.BASE = f'http://127.0.0.1:{port}'
        env.update(HOME=str(state / 'home'), HERMES_HOME=str(state / 'hermes'),
                   HERMES_BASE_HOME=str(state / 'hermes'), HERMES_CONFIG_PATH=str(state / 'hermes/config.yaml'),
                   HERMES_WEBUI_STATE_DIR=str(state / 'webui'), HERMES_WEBUI_DEFAULT_WORKSPACE=str(state / 'workspace'),
                   HERMES_WEBUI_AGENT_DIR=str(state / 'no-agent'), HERMES_WEBUI_SKIP_ONBOARDING='1',
                   HERMES_WEBUI_HOST='127.0.0.1', HERMES_WEBUI_PORT=str(port), NO_PROXY='127.0.0.1,localhost')
        with (OUT / 'server.log').open('w') as log:
            proc = subprocess.Popen([str(ROOT / '.venv/bin/python'), str(ROOT / 'server.py')],
                                    cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT)
            try:
                assert smoke._wait_for_health(), 'isolated server failed: see server.log'
                with sync_playwright() as pw:
                    browser = pw.chromium.launch(headless=True, args=['--no-sandbox', '--disable-dev-shm-usage'])
                    results = []
                    for width, height in ((1440, 900), (522, 1232)):
                        context = browser.new_context(viewport={'width': width, 'height': height}, base_url=smoke.BASE)
                        page = context.new_page()
                        page_errors = []
                        page.on('pageerror', lambda e: page_errors.append(str(e)))
                        policy = {'days': 0, 'fail': False, 'posts': []}

                        def settings_route(route):
                            request = route.request
                            if request.method == 'POST' and 'auto_archive_days' in (request.post_data_json or {}):
                                policy['posts'].append(request.post_data_json)
                                if policy['fail']:
                                    route.fulfill(status=503, json={'error': 'Injected QA save failure'})
                                    return
                                if MOCK:
                                    policy['days'] = request.post_data_json['auto_archive_days']
                                    route.fulfill(json={'ok': True})
                                    return
                            if MOCK and request.method == 'GET':
                                response = route.fetch()
                                data = response.json()
                                data['auto_archive_days'] = policy['days']
                                route.fulfill(response=response, json=data)
                            else:
                                route.continue_()

                        page.route('**/api/settings', settings_route)

                        def open_preferences():
                            page.goto('/', wait_until='domcontentloaded')
                            if width < 768:
                                page.locator('#btnHamburger').click()
                            page.locator('[data-panel="settings"]:visible').first.click()
                            if not page.locator('[data-settings-section="preferences"]').is_visible():
                                page.locator('#btnHamburger').click()
                            page.locator('[data-settings-section="preferences"]').click()
                            expect(page.locator('#settingsAutoArchive')).to_be_enabled()
                            page.locator('#settingsAutoArchiveField').scroll_into_view_if_needed()

                        def capture(name):
                            page.locator('#settingsAutoArchiveField').scroll_into_view_if_needed()
                            page.screenshot(path=str(OUT / f'{width}-{name}.png'))
                            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'), 'body overflow'
                            for selector in ('#settingsAutoArchive', '#settingsAutoArchiveSave'):
                                box = page.locator(selector).bounding_box()
                                assert box and box['x'] >= 0 and box['x'] + box['width'] <= width + 1, selector

                        def save(value):
                            page.locator('#settingsAutoArchive').select_option(value)
                            before = len(policy['posts'])
                            page.wait_for_timeout(350)
                            assert len(policy['posts']) == before, 'draft autosaved'
                            page.locator('#settingsAutoArchiveSave').click()
                            expect(page.locator('#settingsAutoArchiveStatus')).to_have_text('Auto-archive setting saved.')
                            assert policy['posts'][-1] == {'auto_archive_days': int(value)}

                        open_preferences()
                        expect(page.locator('#settingsAutoArchive')).to_have_value('0')
                        capture('disabled')
                        save('30')
                        capture('saved-30')
                        open_preferences()
                        expect(page.locator('#settingsAutoArchive')).to_have_value('30')
                        page.locator('#settingsAutoArchive').select_option('custom')
                        for invalid in ('', '0', '-1', '1.5', '3651'):
                            page.locator('#settingsAutoArchiveDays').fill(invalid)
                            before = len(policy['posts'])
                            page.locator('#settingsAutoArchiveSave').click()
                            expect(page.locator('#settingsAutoArchiveDays')).to_have_attribute('aria-invalid', 'true')
                            assert len(policy['posts']) == before
                        capture('validation')
                        page.locator('#settingsAutoArchiveDays').fill('45')
                        page.locator('#settingsAutoArchiveSave').focus()
                        page.keyboard.press('Enter')
                        expect(page.locator('#settingsAutoArchiveStatus')).to_have_text('Auto-archive setting saved.')
                        open_preferences()
                        expect(page.locator('#settingsAutoArchive')).to_have_value('custom')
                        expect(page.locator('#settingsAutoArchiveDays')).to_have_value('45')
                        save('0')
                        open_preferences()
                        expect(page.locator('#settingsAutoArchive')).to_have_value('0')
                        policy['fail'] = True
                        page.locator('#settingsAutoArchive').select_option('7')
                        page.locator('#settingsAutoArchiveSave').click()
                        expect(page.locator('#settingsAutoArchiveStatus')).to_contain_text('Could not save or verify')
                        expect(page.locator('#settingsAutoArchiveSave')).to_be_enabled()
                        capture('api-error')
                        policy['fail'] = False
                        open_preferences()
                        expect(page.locator('#settingsAutoArchive')).to_have_value('0')
                        page.evaluate("setLocale('pt'); applyLocaleToDOM()")
                        expect(page.locator('label[for="settingsAutoArchive"]')).to_have_text('Arquivar conversas inativas automaticamente')
                        capture('pt-BR')
                        assert not page_errors, page_errors
                        results.append({'viewport': [width, height], 'passed': True,
                                        'policy_posts': policy['posts'], 'uncaught_errors': page_errors})
                        context.close()
                    browser.close()
                    report = {'settings_backend': 'MOCKED' if MOCK else 'real isolated API',
                              'error_case': 'injected HTTP 503', 'results': results}
                    (OUT / 'report.json').write_text(json.dumps(report, indent=2))
                    print(json.dumps(report))
            finally:
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait()


if __name__ == '__main__':
    main()
