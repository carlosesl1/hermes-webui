"""Keyboard regression gate using the real WebUI and deterministic Gateway (no model)."""
import json
import os
import sys
from pathlib import Path
import tempfile

from playwright.sync_api import sync_playwright
import browser_conversation_lifecycle as life

REPO = Path(__file__).resolve().parents[1]
OUT = Path(os.environ.get('CHAT_A11Y_ARTIFACT_DIR', tempfile.mkdtemp(prefix='chat-a11y-')))
OUT.mkdir(parents=True, exist_ok=True)


def safe_env(state, gateway):
    env = {k: os.environ[k] for k in ('PATH', 'LANG', 'PLAYWRIGHT_BROWSERS_PATH') if k in os.environ}
    for name in ('home', 'hermes', 'workspace', 'no-agent'):
        (state / name).mkdir()
    (state / 'no-agent/run_agent.py').write_text('"""Agent-free fixture."""\n')
    env.update(HOME=str(state / 'home'), HERMES_HOME=str(state / 'hermes'),
               HERMES_BASE_HOME=str(state / 'hermes'), HERMES_CONFIG_PATH=str(state / 'hermes/config.yaml'),
               HERMES_WEBUI_STATE_DIR=str(state / 'webui'), HERMES_WEBUI_DEFAULT_WORKSPACE=str(state / 'workspace'),
               HERMES_WEBUI_AGENT_DIR=str(state / 'no-agent'), HERMES_WEBUI_SKIP_ONBOARDING='1',
               HERMES_WEBUI_HOST='127.0.0.1', HERMES_WEBUI_CHAT_BACKEND='gateway',
               HERMES_WEBUI_GATEWAY_BASE_URL=gateway.base_url, HERMES_WEBUI_GATEWAY_USE_RUNS_API='1',
               NO_PROXY='127.0.0.1,localhost')
    return env


def reasoning(page, width):
    if width < 640:
        page.locator('#composerMobileConfigBtn').click()
    trigger = '#composerReasoningChip' if width > 640 else '#composerMobileReasoningAction'
    page.locator(trigger).focus()
    page.keyboard.press('Enter')
    page.locator('#composerReasoningDropdown.open').wait_for(state='visible')
    page.screenshot(path=str(OUT / f'{width}-reasoning.png'))
    assert page.evaluate("() => document.activeElement.dataset.effort") == 'medium', 'opening must focus selected effort'
    assert page.locator('#composerReasoningDropdown').get_attribute('role') == 'listbox'
    for key, expected in [('ArrowDown', 'high'), ('Home', ''), ('ArrowDown', 'none'), ('ArrowDown', 'low'), ('End', 'high'), ('ArrowUp', 'medium')]:
        page.keyboard.press(key)
        assert page.evaluate("() => document.activeElement.dataset.effort") == expected, key
    page.keyboard.press('Home')
    with page.expect_request(lambda r: '/api/reasoning' in r.url and r.method == 'POST') as request:
        page.keyboard.press('Enter')
    assert request.value.post_data_json['effort'] == ''
    assert page.evaluate('() => document.activeElement.id') == trigger[1:]
    page.keyboard.press('ArrowDown')
    page.locator('#composerReasoningDropdown.open').wait_for(state='visible')
    page.keyboard.press('Escape')
    assert not page.locator('#composerReasoningDropdown').is_visible()
    assert page.evaluate('() => document.activeElement.id') == trigger[1:]
    page.keyboard.press('Enter')
    page.keyboard.press('Tab')
    assert not page.locator('#composerReasoningDropdown').is_visible()
    assert not page.evaluate("() => !!document.activeElement.closest('.rightpanel')")


def panel(page, width, height):
    # Initial closed panel must reject even programmatic focus.
    page.locator('#msg').focus()
    page.locator('#btnUploadWorkspace').evaluate('e => e.focus()')
    assert page.evaluate('() => document.activeElement.id') == 'msg', 'closed panel accepted focus'
    assert page.locator('.rightpanel').evaluate('e => e.inert')
    page.locator('#btnWorkspacePanelToggle').focus()
    page.keyboard.press('Enter')
    assert page.locator('.rightpanel').evaluate('e => !e.inert')
    assert page.evaluate("() => !!document.activeElement.closest('.rightpanel')"), 'open should focus panel'
    page.screenshot(path=str(OUT / f'{width}-panel-open.png'))
    page.keyboard.press('Escape')
    assert page.locator('.rightpanel').evaluate('e => e.inert')
    assert page.evaluate('() => document.activeElement.id') == 'btnWorkspacePanelToggle'
    page.keyboard.press('Enter')
    page.locator('#btnClearPreview').click()
    assert page.evaluate('() => document.activeElement.id') == 'btnWorkspacePanelToggle'
    # Resize both closed and open; tablet CSS may hide the pane regardless of mode.
    for target in (1440, 800, 522, 390, width):
        page.set_viewport_size({'width': target, 'height': height})
        page.wait_for_timeout(120)
        assert page.locator('.rightpanel').evaluate('e => e.inert')
    page.locator('#btnWorkspacePanelToggle').click()
    page.locator('#btnUploadWorkspace').focus()
    page.set_viewport_size({'width': 800, 'height': height})
    page.wait_for_timeout(150)
    assert page.locator('.rightpanel').evaluate('e => e.inert')
    assert not page.evaluate("() => !!document.activeElement.closest('.rightpanel')"), 'resize left hidden focus'
    page.set_viewport_size({'width': width, 'height': height})
    page.wait_for_timeout(150)
    # On narrow screens the intentionally overlaid pane covers the composer.
    page.locator('#btnClearPreview').click()
    assert page.locator('.rightpanel').evaluate('e => e.inert')
    page.locator('#msg').click()


def busy(page, gateway, width):
    page.locator('#msg').fill('Check the deterministic fixture.')
    page.locator('#btnSend').click()
    assert gateway.activity_ready.wait(12)
    page.wait_for_function("() => S.busy && S.activeStreamId")
    assert page.locator('#composerSendDescription').inner_text() == 'Stops the current response without sending a message.'
    for text, fragment, action in [('A correction', 'Guides the current response', 'steer'), ('/queue Next turn', 'after the current work finishes', 'queue'), ('/interrupt Replace turn', 'Stops the current response, then sends', 'interrupt')]:
        page.locator('#msg').fill(text)
        assert page.locator('#btnSend').get_attribute('data-action') == action
        assert fragment in page.locator('#composerSendDescription').inner_text()
        assert 'composerSendDescription' in page.locator('#msg').get_attribute('aria-describedby')
        assert 'composerSendDescription' in page.locator('#btnSend').get_attribute('aria-describedby')
    page.screenshot(path=str(OUT / f'{width}-busy.png'))
    gateway.release_settle.set()
    gateway.release_terminal.set()
    page.wait_for_function('() => !S.busy && !S.activeStreamId')
    assert page.locator('#composerSendDescription').inner_text() == ''
    assert 'composerSendDescription' not in (page.locator('#msg').get_attribute('aria-describedby') or '')


def main():
    results = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True, args=['--no-sandbox', '--disable-dev-shm-usage'])
        for width, height in [(1440, 900), (522, 1232), (390, 844)]:
            gateway = life.DeterministicGateway('normal')
            gateway.start()
            proc = log = None
            with tempfile.TemporaryDirectory(prefix='chat-a11y-state-') as tmp:
                try:
                    proc, log, _, base = life._start_webui_server(REPO, safe_env(Path(tmp), gateway), OUT)
                    for name, fn in [('reasoning', lambda p, w=width: reasoning(p, w)), ('panel', lambda p, w=width, h=height: panel(p, w, h)), ('busy', lambda p, g=gateway, w=width: busy(p, g, w))]:
                        ctx = browser.new_context(base_url=base, viewport={'width': width, 'height': height})
                        page = ctx.new_page()
                        page.set_default_timeout(6000)
                        # Cold static assets can be slow under concurrent test load;
                        # keep interaction deadlines strict but separate navigation.
                        page.set_default_navigation_timeout(25000)
                        errors = []
                        page.on('pageerror', lambda e, sink=errors: sink.append(str(e)))
                        page.on('console', lambda m, sink=errors: sink.append(m.text) if m.type == 'error' else None)
                        def capabilities(route):
                            payload = route.request.post_data_json if route.request.method == 'POST' else None
                            route.fulfill(json={'reasoning_effort': payload['effort'] if payload else 'medium', 'supported_efforts': ['low', 'medium', 'high'], 'supports_thinking_toggle': True})
                        page.route('**/api/reasoning*', capabilities)
                        # The optional core skill loader is absent in this agent-free server.
                        page.route('**/api/skills*', lambda route: route.fulfill(json={'skills': []}))
                        row = {'case': f'{width}-{name}', 'passed': False}
                        try:
                            page.goto('/', wait_until='domcontentloaded')
                            page.locator('#msg').wait_for(state='visible')
                            page.wait_for_function("() => typeof S !== 'undefined' && !!S._profileDefaultWorkspace")
                            page.screenshot(path=str(OUT / f'{width}-{name}-before.png'))
                            fn(page)
                            assert not errors, errors
                            row['passed'] = True
                            row['geometry'] = page.evaluate('() => ({width: innerWidth, visualWidth: visualViewport.width, scrollWidth: document.documentElement.scrollWidth})')
                            assert row['geometry']['scrollWidth'] <= width, row['geometry']
                            page.screenshot(path=str(OUT / f'{width}-{name}-after.png'))
                        except Exception as exc:
                            row['passed'] = False
                            row['error'] = str(exc)
                            page.screenshot(path=str(OUT / f'{width}-{name}-failure.png'))
                        row['errors'] = errors
                        results.append(row)
                        ctx.close()
                finally:
                    life._terminate_process(proc)
                    if log:
                        log.close()
                    gateway.close()
        browser.close()
    (OUT / 'results.json').write_text(json.dumps(results, indent=2))
    print(json.dumps(results, indent=2))
    return 0 if len(results) == 9 and all(row['passed'] for row in results) else 1


if __name__ == '__main__':
    sys.exit(main())
