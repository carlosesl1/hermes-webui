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
        assert page.locator('#liveAssistantTurn').evaluate("e=>e.parentElement.id==='msgInner'"), 'live synthesis hidden by notification'
        page.locator('summary').focus()
        page.evaluate('syncBackgroundActivity(inner)')
        assert page.evaluate("document.activeElement.matches('summary')"), 'sync lost keyboard focus'
        page.evaluate("document.querySelector('.blocks').innerHTML='<div class=approval-card><button>Approve</button></div>'")
        assert page.locator('.approval-card button').is_visible()
        assert not page.locator('details').evaluate('e=>e.open'), 'principal approval must not require opening activity'
        page.locator('.approval-card button').focus()
        page.evaluate('syncBackgroundActivity(inner)')
        assert page.evaluate("document.activeElement.textContent==='Approve'"), 'sync moved focused approval'
        page.evaluate("S.busy=false;document.querySelector('.blocks').innerHTML='Principal final report'")
        assert page.get_by_text('Principal final report', exact=True).is_visible()
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
        page.wait_for_function("() => [...inner.querySelectorAll('details')].every(g=>g.open)")
        page.locator('details').last.locator('summary').click()
        page.wait_for_function("() => [...inner.querySelectorAll('details')].every(g=>!g.open)")
        # Visible assistant rows are semantic boundaries even without a spacer.
        page.evaluate("""() => {
          prepareBackgroundActivityRender(inner);
          S.session.session_id='semantic';
          S.messages=[{role:'user',content:'Run'},
            {role:'user',_source:'process_wakeup',content:'one'},
            {role:'assistant',content:'Final deliverable'},
            {role:'user',_source:'process_wakeup',content:'two',_wakeup_meta:{exit_code:1}},
            {role:'assistant',content:'Done'}];
          inner.innerHTML='<div class="msg-row" data-msg-idx="1">one</div><div class="assistant-turn" data-msg-idx="2"><a href="#artifact">Final deliverable</a></div><div class="msg-row" data-msg-idx="3">two</div><div class="msg-row" data-role="assistant" data-msg-idx="4">Done</div>';
          syncBackgroundActivity(inner);
        }""")
        assert page.locator('details').count() == 2
        assert page.evaluate("[...inner.children].map(e=>e.dataset.msgIdx||e.dataset.backgroundStart)") == ['1','2','3','4']
        assert page.get_by_text('Final deliverable', exact=True).is_visible()
        assert page.get_by_text('Done', exact=True).is_visible()
        assert page.locator('details.has-failure').count() == 1
        assert page.evaluate("backgroundActivityVirtualHeight(inner,{rawIdx:2},[{rawIdx:1},{rawIdx:2},{rawIdx:3}])") is None
        page.locator('a').focus()
        page.evaluate('syncBackgroundActivity(inner)')
        assert page.evaluate("document.activeElement.matches('a')")
        # Nested controls in a notification retain the existing safety expansion.
        page.evaluate("inner.querySelector('[data-msg-idx=\"3\"]').innerHTML='<div class=clarify-card><button>Choose</button></div>'")
        page.wait_for_function("() => [...inner.querySelectorAll('details')].every(g=>g.open)")
        assert page.get_by_text('Choose', exact=True).is_visible()
        page.get_by_text('Choose', exact=True).focus()
        page.evaluate('syncBackgroundActivity(inner)')
        assert page.evaluate("document.activeElement.textContent==='Choose'")
        page.evaluate('prepareBackgroundActivityRender(inner);syncBackgroundActivity(inner)')
        assert page.evaluate("[...inner.children].map(e=>e.dataset.msgIdx||e.dataset.backgroundStart)") == ['1','2','3','4']
        return {'principal_visible': True, 'semantic_boundaries': True,
                'focus': True, 'nested_approval': True, 'settled_status': True,
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
