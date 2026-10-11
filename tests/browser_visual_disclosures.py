"""Offline visual UX regression: real shell/CSS/JS, no boot or provider.

VISUAL_BROWSER selects an existing Chromium. VISUAL_BASELINE serves the old
HTML/CSS for RED proof. API responses are synthetic, not backend/E2E evidence.
VISUAL_EVIDENCE retains screenshots and measured results.
"""
import json
import mimetypes
import os
import re
import subprocess
import tempfile
from pathlib import Path
from urllib.parse import urlsplit

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]


def main():
    out = Path(os.environ.get('VISUAL_EVIDENCE') or tempfile.mkdtemp(prefix='visual-ux-'))
    out.mkdir(parents=True, exist_ok=True)
    baseline = os.environ.get('VISUAL_BASELINE')
    results = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(executable_path=os.environ.get('VISUAL_BROWSER'), args=['--no-sandbox'])
        for width, height in ([(1440, 900)] if os.environ.get('VISUAL_DESKTOP_ONLY') else [(1440, 900), (522, 1232), (390, 844)]):
            context = browser.new_context(viewport={'width': width, 'height': height}, service_workers='block')
            page = context.new_page()
            page.set_default_timeout(7000)
            print('viewport', width, flush=True)
            errors = []
            page.on('pageerror', lambda error: errors.append(str(error)))

            def route(request):
                path = urlsplit(request.request.url).path
                file = ROOT / ('static/index.html' if path == '/' else path.lstrip('/'))
                if file.is_file() and (path == '/' or path.startswith('/static/')):
                    body = (subprocess.check_output(['git', 'show', baseline + ':' + str(file.relative_to(ROOT))], cwd=ROOT)
                            if baseline and file.name in ('index.html', 'style.css') else file.read_bytes())
                    if path == '/':
                        text = body.decode().replace('__MAX_UPLOAD_BYTES__', '1048576').replace('__CSRF_TOKEN_JSON__', 'null')
                        text = re.sub(r'<script[^>]*src="(?:static/boot.js|https://)[^>]*></script>', '', text)
                        text = re.sub(r'<link[^>]*href="https://[^>]*>', '', text)
                        body = text.encode()
                    request.fulfill(body=body, content_type=mimetypes.guess_type(file)[0] or 'application/octet-stream')
                elif path == '/api/model/auxiliary':
                    request.fulfill(json={'tasks': [{'task': 'vision', 'label': 'Vision', 'provider': 'auto', 'model': ''}]})
                else:
                    request.fulfill(json={})

            page.route('**/*', route)
            page.goto('http://visual.test/', wait_until='domcontentloaded', timeout=15000)
            print('loaded', width, flush=True)
            page.evaluate("""async () => {
                document.documentElement.classList.add('dark');
                S.session={session_id:'visual-fixture',title:'Visual fixture',tool_calls:[]};
                S.messages=[{role:'user',content:'A synthetic visual fixture.'},{role:'assistant',content:'A readable answer with quiet actions.'}];
                renderMessages();
                $('composerMobileWorkspaceLabel').textContent='Research and documentation workspace';
                $('composerMobileModelLabel').textContent='GPT 6 Astra — extended model name';
                $('composerMobileQuotaAction').style.display='';
                $('composerMobileQuotaLabel').textContent='75%';
                $('composerMobileReasoningAction').style.display='';
                $('composerMobileReasoningLabel').textContent='Max';
                $('composerMobileContextAction').style.display='';
                $('composerMobileContextUsage').textContent='Context window: 6% used (94% left)';
                $('composerMobileContextThreshold').textContent='Auto-compress at 204k (75%)';
            }""")
            measurements = page.locator('.msg-action-btn:visible').evaluate_all('(els)=>els.map(e=>({width:e.getBoundingClientRect().width,height:e.getBoundingClientRect().height}))')
            row = {'width': width, 'hitTargets': all(r['width'] >= (44 if width <= 640 else 24) and r['height'] >= (44 if width <= 640 else 24) for r in measurements), 'actionCount': len(measurements), 'measurements': measurements}
            if width <= 640:
                page.locator('#composerMobileConfigBtn').click()
                row['labelsReadable'] = page.locator('#composerMobileModelLabel').evaluate("e=>getComputedStyle(e).whiteSpace!=='nowrap' && getComputedStyle(e.previousElementSibling).display!=='none'")
                row['modelFullRow'] = page.locator('#composerMobileModelAction').evaluate('e=>e.clientWidth>e.parentElement.clientWidth*.8')
                page.screenshot(path=str(out / f'{width}-composer.png'))
                page.locator('#composerMobileConfigBtn').click()
            page.evaluate("""async () => {
                document.querySelector('main.main').classList.add('showing-settings');
                document.querySelectorAll('.settings-pane').forEach(e=>e.classList.remove('active'));
                $('settingsPanePreferences').classList.add('active');
                $('settingsModelChip').textContent='GPT 6 Astra';
                await _loadAuxiliaryModels();
            }""")
            disclosure = page.locator('#settingsAuxiliaryAdvanced')
            row['disclosurePresent'] = disclosure.count() == 1
            if disclosure.count():
                row['initiallyCollapsed'] = not disclosure.evaluate('e=>e.open')
                page.screenshot(path=str(out / f'{width}-preferences.png'))
                disclosure.locator('summary').focus()
                page.keyboard.press('Enter')
                row['keyboardOpens'] = disclosure.evaluate('e=>e.open')
                row['auxVisible'] = page.locator('#aux-prov-vision').is_visible()
                page.locator('#aux-prov-vision').focus()
                row['nativeFocus'] = page.locator('#aux-prov-vision').evaluate("e=>getComputedStyle(e).outlineStyle!=='none'")
                row['searchable'] = page.evaluate("()=>!!$('auxModelsContainer').closest('.settings-field').querySelector('label[data-i18n]')")
                page.screenshot(path=str(out / f'{width}-advanced.png'))
            page.evaluate("""() => {
                document.querySelectorAll('.settings-pane').forEach(e=>e.classList.remove('active'));
                $('settingsPaneConversation').classList.add('active');
                $('hermesSessionMeta').textContent='Visual fixture · 2 messages';
            }""")
            export = page.locator('#conversationExportDetails')
            row['exportGrouped'] = export.count() == 1
            if export.count():
                export.locator('summary').click()
                row['formatsVisible'] = all(page.locator('#' + i).is_visible() for i in ['btnDownload', 'btnExportJSON', 'btnExportHTML'])
                row['clearSeparated'] = page.locator('#btnClearConvModal').evaluate("e=>!!e.closest('.settings-conversation-danger') && !e.closest('details')")
            page.screenshot(path=str(out / f'{width}-conversation.png'))
            row['noOverflow'] = page.evaluate('document.documentElement.scrollWidth<=innerWidth')
            row['errors'] = errors
            results.append(row)
            (out / 'results.json').write_text(json.dumps(results, indent=2))
            print(json.dumps(row), flush=True)
            context.close()
        browser.close()
    (out / 'results.json').write_text(json.dumps(results, indent=2))
    print(json.dumps(results, indent=2))
    return int(any(any(v is False for v in row.values()) or not row['actionCount'] or row['errors'] for row in results))


if __name__ == '__main__':
    raise SystemExit(main())
