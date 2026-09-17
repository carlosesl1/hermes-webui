"""Credential-free Chromium rendering matrix. No application/backend state writes.
Run with a Playwright-equipped Python; SCROLL_EVIDENCE selects artifact directory.
"""
import json
import mimetypes
import os
import re
import subprocess
from pathlib import Path
from urllib.parse import urlsplit

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
OUT = Path(os.environ.get('SCROLL_EVIDENCE', '/tmp/hermes-scroll-evidence'))
OUT.mkdir(parents=True, exist_ok=True)
BASELINE = os.environ.get('SCROLL_BASELINE')
BASE_FILES = {name: subprocess.check_output(['git', 'show', BASELINE + ':static/' + name], cwd=ROOT) for name in ['ui.js', 'sessions.js']} if BASELINE else {}


def main():
    results = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True, args=['--no-sandbox'])
        for width, height in [(1440, 900), (522, 1232)]:
            for mode in ['compact_worklog', 'transparent_stream', 'hide_all_activity']:
                for virtual in [False, True]:
                    page = browser.new_page(viewport={'width': width, 'height': height})
                    def route(request):
                        path = urlsplit(request.request.url).path
                        if path == '/':
                            html = (ROOT / 'static/index.html').read_text()
                            html = html.replace('__MAX_UPLOAD_BYTES__', '1048576').replace('__CSRF_TOKEN_JSON__', 'null')
                            html = re.sub(r'<script[^>]*src="static/boot.js[^>]*></script>', '', html)
                            request.fulfill(body=html, content_type='text/html')
                        elif path.startswith('/static/') and (ROOT / path.lstrip('/')).is_file():
                            file = ROOT / path.lstrip('/')
                            request.fulfill(body=BASE_FILES.get(file.name, file.read_bytes()), content_type=mimetypes.guess_type(file)[0] or 'application/octet-stream')
                        elif path.startswith('/api/'):
                            request.fulfill(json={})
                        else:
                            request.fulfill(body='', content_type='text/javascript')
                    page.route('**/*', route)
                    page.goto('http://scroll.test/', wait_until='load')
                    page.evaluate(r"""({mode, virtual}) => {
                        window._chatActivityDisplayMode=mode;
                        window._virtualizeTranscript=virtual;
                        window._autoScrollFollow=true;
                        window.renderMarkdown = window.renderMarkdown || (s=>String(s).replace(/\n/g,'<br>'));
                        S.session={session_id:'scroll-A',tool_calls:[]}; S.busy=false; _oldestIdx=40;
                        window.semantic=()=>{const e=$('messages'),c=e.getBoundingClientRect();const row=Array.from(e.querySelectorAll('[data-message-anchor-key]')).find(r=>{const b=r.getBoundingClientRect();return b.height>0&&b.bottom>c.top+1&&b.top<c.bottom-1;});return row?{key:row.dataset.messageAnchorKey,topOffset:row.getBoundingClientRect().top-c.top,rawIdx:Number(row.dataset.msgIdx)}:null;};
                        window.semanticDelta=a=>{if(!a)return null;const e=$('messages');const r=Array.from(e.querySelectorAll('[data-message-anchor-key]')).find(r=>r.dataset.messageAnchorKey===a.key);return r?r.getBoundingClientRect().top-e.getBoundingClientRect().top-a.topOffset:null;};
                        S.messages=Array.from({length:180},(_,i)=>({role:i%2?'assistant':'user',id:'m'+i,timestamp:i,content:'Turn '+i+' — '+('A stable transcript line. '.repeat(14))}));
                        renderMessages();
                    }""", {'mode': mode, 'virtual': virtual})
                    page.wait_for_timeout(350)
                    page.evaluate("""() => { _cancelBottomSettle(); const e=$('messages'); e.scrollTop=e.scrollHeight*.45; _messageUserUnpinned=true; _scrollPinned=false; }""")
                    page.wait_for_timeout(250)
                    page.screenshot(path=str(OUT / f'{width}-{mode}-v{int(virtual)}-before.png'))
                    result = page.evaluate("""() => {
                        const e=$('messages'); const before=e.scrollTop; const renderAnchor=semantic();
                        renderMessages({preserveScroll:true});
                        const renderDelta=e.scrollTop-before; const renderSemanticDelta=semanticDelta(renderAnchor); const renderAfter=semantic();
                        const snapshot=_captureMessageScrollSnapshot();
                        S.session={session_id:'scroll-B'};
                        e.scrollTop=before+100;
                        const target=e.scrollTop;
                        _restoreMessageScrollSnapshotSameFrame(snapshot);
                        const staleSessionDelta=e.scrollTop-target; S.session={session_id:'scroll-A',tool_calls:[]};e.scrollTop=before+renderDelta;
                        return {renderDelta,renderSemanticDelta,renderAnchor,renderAfter, staleSessionDelta, virtualSpacers:document.querySelectorAll('[data-virtual-spacer]').length, messages:S.messages.length};
                    }""")
                    # Exercise actual history loader and renderer, not replacement scroll stubs.
                    flow = page.evaluate(r"""async () => {
                        S.session={session_id:'scroll-A',tool_calls:[]};
                        const e=$('messages'); _cancelBottomSettle();
                        _messageUserUnpinned=true; _scrollPinned=false;
                        renderMessages({preserveScroll:true});
                        await new Promise(r=>setTimeout(r,120));
                        const snap=()=>semantic();
                        const delta=a=>{
                            if(!a) return null;
                            const row=Array.from(e.querySelectorAll('[data-message-anchor-key]')).find(r=>r.dataset.messageAnchorKey===a.key);
                            return row ? row.getBoundingClientRect().top-e.getBoundingClientRect().top-a.topOffset : null;
                        };
                        const savedApi=api; const prepends=[];
                        for(let n=0;n<2;n++){
                            const a=snap(); _messagesTruncated=true;
                            api=async()=>({session:{session_id:'scroll-A',_messages_offset:20-n*20,_messages_truncated:n===0,tool_calls:[],messages:Array.from({length:20},(_,i)=>({role:i%2?'assistant':'user',id:'older'+n+'-'+i,content:'Older turn '+n+'-'+i+' '+('history '.repeat(35))}))}});
                            try { await _loadOlderMessages(); } finally {api=savedApi;}
                            await new Promise(r=>setTimeout(r,180));
                            prepends.push({before:a,after:snap(),delta:delta(a)});
                        }
                        const prependDelta=prepends.some(p=>p.delta===null)?null:Math.max(...prepends.map(p=>Math.abs(p.delta)));
                        const lateAnchor=snap();
                        const above=Math.max(0,lateAnchor.rawIdx-1);
                        S.messages[above].content+='\n'+('Late decoded row height. '.repeat(100));
                        renderMessages({preserveScroll:true});
                        await new Promise(r=>setTimeout(r,180));
                        const lateHeightDelta=delta(lateAnchor);
                        const streamAnchor=snap(); S.busy=true;
                        S.messages.push({role:'user',id:'new-user',content:'Continue the answer'}, {role:'assistant',id:'new-answer',content:'Streaming answer'});
                        let streamDelta=0;
                        for(let i=0;i<4;i++){
                            S.messages[S.messages.length-1].content+='\n'+('New streamed paragraph. '.repeat(25));
                            renderMessages({preserveScroll:true});
                            await new Promise(r=>setTimeout(r,100));
                            const d=delta(streamAnchor); streamDelta=d===null?null:Math.max(streamDelta,Math.abs(d));
                        }
                        S.busy=false; renderMessages({preserveScroll:true});
                        await new Promise(r=>setTimeout(r,150));
                        const settleDelta=delta(streamAnchor);
                        const readingOlder=_messageUserUnpinned && !_scrollPinned && e.scrollHeight-e.clientHeight-e.scrollTop>500;
                        // Explicit user navigation is still allowed to reach the tail.
                        scrollToBottom(true);
                        await new Promise(r=>setTimeout(r,250));
                        const tailDistance=e.scrollHeight-e.clientHeight-e.scrollTop;
                        return {prepends,prependDelta,lateHeightDelta,streamDelta,settleDelta,readingOlder,tailDistance,loadedCount:S.messages.length,
                            bodyOverflow:document.documentElement.scrollWidth-innerWidth,
                            paneOverflow:e.scrollWidth-e.clientWidth, viewportWidth:innerWidth};
                    }""")
                    result.update(flow)
                    # Exercise actual post-process wrapper with deterministic above-anchor layout growth.
                    post = page.evaluate("""async () => {
                        S.session={session_id:'scroll-A'}; _cancelBottomSettle();
                        const e=$('messages'); e.scrollTop=e.scrollHeight*.45;
                        _messageUserUnpinned=true;_scrollPinned=false;
                        _scheduleMessageVirtualizedRender(true);
                        await new Promise(r=>setTimeout(r,200));
                        const snapshot=_captureMessageScrollSnapshot();
                        if(!snapshot.anchor) throw new Error('Postprocess reader anchor not mounted');
                        const old=postProcessRenderedMessages;
                        postProcessRenderedMessages=()=>{const row=$('msgInner').querySelector('[data-msg-idx]'); row.style.paddingTop='180px';};
                        _postProcessWithAnchorSuppression($('msgInner'));
                        postProcessRenderedMessages=old;
                        const row=Array.from(e.querySelectorAll('[data-message-anchor-key]')).find(r=>r.dataset.messageAnchorKey===snapshot.anchor.key);
                        return row ? row.getBoundingClientRect().top-e.getBoundingClientRect().top-snapshot.anchor.topOffset : null;
                    }""")
                    result.update(width=width,height=height,mode=mode,virtual=virtual,postProcessDelta=post)
                    page.screenshot(path=str(OUT / f'{width}-{mode}-v{int(virtual)}.png'))
                    results.append(result)
                    (OUT/'results.json').write_text(json.dumps(results, indent=2))
                    page.close()
        browser.close()
    (OUT/'results.json').write_text(json.dumps(results, indent=2))
    failures=[r for r in results if
        any(r[k] is None or abs(r[k])>2 for k in ['renderSemanticDelta','staleSessionDelta','postProcessDelta','lateHeightDelta','prependDelta','streamDelta','settleDelta'])
        or bool(r['virtualSpacers']) != r['virtual'] or not r['readingOlder']
        or r['tailDistance']>2 or r['loadedCount'] != 222 or r['bodyOverflow']>2 or r['paneOverflow']>2]
    print(json.dumps({'cases':len(results),'passed':len(results)-len(failures),'failed':len(failures),'results':results}))
    return bool(failures)


if __name__ == '__main__':
    raise SystemExit(main())
