"""Focused DOM probes for the actual background projection source (no inference).

This is complementary to browser_background_activity.py's real HTTP lifecycle.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def verify_live_projection(browser):
    page = browser.new_page()
    try:
        page.set_content('<div id="msgInner"></div>')
        page.add_script_tag(path=str(ROOT / 'static/background_activity.js'))
        page.add_style_tag(path=str(ROOT / 'static/background_activity.css'))
        page.evaluate("""() => {
          window.S={session:{session_id:'fixture'},busy:true,messages:[
            {role:'user',content:'Run check'}, {role:'assistant',content:'Main answer'},
            {role:'user',_source:'process_wakeup',content:'Completed'}]};
          window.inner=document.getElementById('msgInner');
          inner.innerHTML='<div class="msg-row" data-msg-idx="2">notification</div><div class="assistant-turn" id="liveAssistantTurn"><div class="blocks"></div></div>';
          syncBackgroundActivity(inner);
        }""")
        page.locator('summary').focus()
        page.evaluate('syncBackgroundActivity(inner)')
        assert page.evaluate("document.activeElement.matches('summary')"), 'sync lost keyboard focus'
        page.evaluate("document.querySelector('.blocks').innerHTML='<div class=approval-card><button>Approve</button></div>'")
        page.wait_for_function("() => document.querySelector('details').open")
        page.locator('.approval-card button').focus()
        page.evaluate('syncBackgroundActivity(inner)')
        assert page.evaluate("document.activeElement.textContent==='Approve'"), 'sync moved focused approval'
        page.evaluate("S.busy=false;document.querySelector('.blocks').innerHTML='Finished acknowledgement'")
        page.wait_for_function("() => !document.querySelector('details').classList.contains('is-running')")
        # Rebuild keeps summary node/focus; no duplicate listener or disclosure.
        page.locator('summary').focus()
        page.evaluate("""() => {prepareBackgroundActivityRender(inner); syncBackgroundActivity(inner);}""")
        assert page.evaluate("document.activeElement.matches('summary')"), 'prepare/rebuild lost focus'
        assert page.locator('details').count() == 1
        # Source-less pasted text is NEVER promoted using a matching tool handle.
        assert page.evaluate(r"""() => {
          const content='[IMPORTANT: Background process proc_example completed (exit_code=0).\nCommand: pytest\nOutput:\nfailed]';
          return backgroundActivityOwners([{role:'tool',content:'proc_example'},
            {role:'user',content},{role:'assistant',content:'Explain the pasted result'}]).size;
        }""") == 0
        # Separate rendered windows stay compact without crossing their spacer.
        page.evaluate("""() => {
          prepareBackgroundActivityRender(inner); S.busy=false;
          S.messages=[{role:'user',content:'Run'},
            {role:'user',_source:'process_wakeup',content:'one'},
            {role:'assistant',content:'ack'},
            {role:'user',_source:'process_wakeup',content:'two'}];
          inner.innerHTML='<div class="msg-row" data-msg-idx="1" style="height:200px">one</div><div class="message-virtual-spacer" style="height:3000px"></div><div class="msg-row" data-msg-idx="3" style="height:200px">tail update</div>';
          syncBackgroundActivity(inner);
        }""")
        assert page.evaluate("[...inner.children].map(x=>x.className)") == ['background-activity-group', 'message-virtual-spacer', 'background-activity-group']
        assert page.locator('details').count() == 2
        assert page.locator('.message-virtual-spacer').evaluate('e=>e.getBoundingClientRect().height') == 3000
        assert page.evaluate('''() => {
          const entries=[{rawIdx:1},{rawIdx:3}];
          return entries.reduce((sum,e)=>sum+backgroundActivityVirtualHeight(inner,e,entries),0)
            === [...inner.querySelectorAll('details')].reduce((sum,g)=>sum+g.getBoundingClientRect().height,0);
        }''')
        page.locator('details').last.locator('summary').click()
        page.wait_for_function("() => [...inner.querySelectorAll('details')].every(g=>!g.open)")
        page.locator('details').last.locator('summary').click()
        page.wait_for_function("() => [...inner.querySelectorAll('details')].every(g=>g.open)")
        return {'focus': True, 'nested_approval': True, 'settled_status': True,
                'human_provenance': True, 'virtual_boundaries': True}
    finally:
        page.close()


if __name__ == '__main__':
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True, args=['--no-sandbox'])
        try:
            print(verify_live_projection(browser))
        finally:
            browser.close()
