"""Offline native-Chromium compaction visibility RED/GREEN gate.

Run with a Playwright-equipped Python. Backend payload generation uses repo .venv.
COMPACTION_EVIDENCE selects an artifact directory. COMPACTION_BASELINE=fefaaf89
serves that revision's ui.js and the SAME legacy merge output without provenance;
identical assertions must then exit nonzero. No production state/server/provider.
Boot and CDN are omitted; APIs intercepted at compaction.test. Real merge,
provenance, window, renderer, loadSession, paging and native EventSource are used.
No answer-directed scrolling. Reload explicitly calls loadSession (boot omitted).
"""
import copy
import hashlib
import json
import mimetypes
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
from urllib.parse import parse_qs, urlsplit

ROOT = Path(__file__).resolve().parents[1]
SID = 'synthetic-compaction-visibility'
STREAM = 'synthetic-compaction-stream'
HEADER = ('[STILL IN PROGRESS — this is the active request, restated after the '
          'compaction boundary because it was not finished yet. Continue it; do not start over.]')
FINAL = 'FINAL ANSWER SENTINEL: The completed offline result is retained exactly once.'
ORIGINAL = 'SYNTHETIC ORIGINAL REQUEST: investigate compaction replay visibility.\n\n' + '\n\n'.join(
    'Synthetic scope: preserve the completed result, retain raw history coordinates, and distinguish an internal replay from a literal human quotation.'
    for _ in range(16))
REPLAY = HEADER + '\n' + ORIGINAL


def payloads(out):
    """Import real backend only after fully isolating all state and core discovery."""
    with tempfile.TemporaryDirectory(prefix='compaction-payload-') as temp:
        isolated = Path(temp)
        for key, sub in [('HOME', 'home'), ('HERMES_HOME', 'hermes'),
                         ('HERMES_BASE_HOME', 'hermes'), ('HERMES_WEBUI_STATE_DIR', 'state'),
                         ('HERMES_WEBUI_AGENT_DIR', 'stub-agent'),
                         ('HERMES_WEBUI_DEFAULT_WORKSPACE', 'workspace')]:
            os.environ[key] = str(isolated / sub)
            (isolated / sub).mkdir(parents=True, exist_ok=True)
        os.environ['HERMES_CONFIG_PATH'] = str(isolated / 'hermes/config.yaml')
        (isolated / 'hermes/config.yaml').write_text('{}\n')
        (isolated / 'stub-agent/run_agent.py').write_text('# Deliberately inert fixture core.\n')
        sys.path.insert(0, str(ROOT))
        from api import models, routes
        from api.compaction_provenance import project_compaction_replays, is_compaction_replay
        assert models.SESSION_DIR.is_relative_to(isolated)
        rows = [{'id': f'h{i}', 'message_uid': f'human-or-assistant-{i}',
                 'role': 'user' if i % 2 == 0 else 'assistant',
                 'content': f'Synthetic historical message {i}.', 'timestamp': 1700000000 + i}
                for i in range(800)]
        rows[10]['content'] = ORIGINAL
        rows[798].update(content=REPLAY, message_uid='distinct-literal-human-quote')
        rows[799].update(content=FINAL, message_uid='unique-final')
        replay = {'role': 'user', 'content': REPLAY, 'message_uid': rows[10]['message_uid'],
                  'timestamp': 1700000798.5, 'id': 'legacy-replay'}
        before = copy.deepcopy(rows)
        raw = models.merge_session_messages_append_only(copy.deepcopy(rows), [copy.deepcopy(replay)])
        assert len(raw) == 801 and raw[-1]['content'] == REPLAY
        assert raw[-2]['content'] == FINAL and raw[-1]['message_uid'] == rows[10]['message_uid']
        projected = project_compaction_replays(raw, witnesses=rows)
        assert len(projected) == len(raw) and rows == before
        assert is_compaction_replay(projected[-1])
        assert not is_compaction_replay(projected[798])
        assert not is_compaction_replay(project_compaction_replays(raw[-30:])[-1])
        assert raw[-1].get('display_kind') is None

        def session(source, limit=30, before=None, boundary=False):
            window, offset = routes._message_window_for_display(source, msg_limit=limit, msg_before=before)
            if boundary and before is not None:
                window = source[offset:before]
            result = {'session_id': SID, 'title': 'Synthetic compaction visibility', 'source_tag': 'webui',
                      'model': 'offline-fixture', 'context_length': 131072, 'message_count': len(source),
                      '_messages_offset': offset, '_messages_truncated': offset > 0, '_msg_limit_max': 500,
                      'messages': window, 'tool_calls': [], 'active_stream_id': None, 'updated_at': 1700000801}
            if boundary:
                result['_messages_boundary'] = source[before] if before < len(source) else None
            return result

        output = {'sid': SID, 'stream': STREAM, 'final': FINAL, 'replay': REPLAY,
                  'raw_count': len(raw), 'original_index': 10, 'quote_index': 798, 'final_index': 799,
                  'raw': raw, 'projected': projected, 'done': session(rows), 'variants': {},
                  'backend_sha256': {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
                                     for p in [ROOT/'api/models.py', ROOT/'api/routes.py', ROOT/'api/compaction_provenance.py']}}
        output['done']['_settlement_window'] = 'tail_v1'
        for name, source in [('fixed', projected), ('baseline', raw)]:
            tail = session(source)
            older = session(source, before=tail['_messages_offset'], boundary=True)
            # Raw replay in a done payload directly covers the browser provenance
            # gate as well as the HTTP window gate (which trims the hidden suffix).
            reconnect = session(source, limit=None)
            reconnect['messages'] = source[770:]
            reconnect['_messages_offset'] = 770
            reconnect['_messages_truncated'] = True
            reconnect['_settlement_window'] = 'tail_v1'
            output['variants'][name] = {'tail': tail, 'older': older, 'reconnect': reconnect}
        assert output['variants']['fixed']['tail']['_messages_offset'] == 770
        assert output['variants']['baseline']['tail']['_messages_offset'] == 771
        out.write_text(json.dumps(output, indent=2))


MEASURE = r'''({final,replay})=>{
 const norm=t=>String(t||'').replace(/\s+/g,' ').trim();
 const bodies=[...document.querySelectorAll('#msgInner .msg-body')];
 const finalNodes=bodies.filter(n=>norm(n.textContent)===norm(final));
 const headerNodes=bodies.filter(n=>norm(n.textContent).includes(norm(replay.slice(0,100))));
 const rect=r=>({top:r.top,bottom:r.bottom,left:r.left,right:r.right,height:r.height});
 const measure=n=>{if(!n)return null;const r=n.getBoundingClientRect();
   let l=Math.max(0,r.left),t=Math.max(0,r.top),rr=Math.min(innerWidth,r.right),b=Math.min(innerHeight,r.bottom);
   const clips=[];for(let p=n.parentElement;p;p=p.parentElement){const s=getComputedStyle(p);
     if(/hidden|auto|scroll|clip/.test(s.overflowY)){const q=p.getBoundingClientRect();t=Math.max(t,q.top);b=Math.min(b,q.bottom);
       clips.push({id:p.id,top:q.top,bottom:q.bottom,scrollTop:p.scrollTop,scrollHeight:p.scrollHeight,clientHeight:p.clientHeight});}}
   return {rect:rect(r),visiblePixelsY:Math.max(0,b-t),intersectsViewport:rr>l&&b>t,clips};};
 const quote=S.messages.find(m=>m.message_uid==='distinct-literal-human-quote');
 const replayRows=S.messages.filter(m=>m.id==='legacy-replay');
 return {loaded:S.messages.length,count:S.session.message_count,offset:_oldestIdx,history:_messagesTruncated,
   finalInMessages:S.messages.filter(m=>m.content===final).length,finalAbsoluteIndex:_oldestIdx+S.messages.findIndex(m=>m.content===final),
   finalDomCount:finalNodes.length,headerDomCount:headerNodes.length,final:measure(finalNodes[0]),
   quoteRenderable:!!quote&&_messageIsRenderable(quote),replayInMessages:replayRows.length,
   replayRenderable:replayRows.filter(m=>_messageIsRenderable(m)).length,
   native:window.nativeTransport,events:window.seen||[],innerWidth,innerHeight,visualWidth:visualViewport.width,
   bodyOverflow:document.documentElement.scrollWidth-innerWidth};
}'''

OBSERVER = '''()=>{
 window._virtualizeTranscript=true;window._chatActivityDisplayMode='compact_worklog';
 const Native=window.EventSource;window.seen=[];window.nativeTransport=false;
 window.EventSource=class extends Native{constructor(url,opts){super(url,opts);window.nativeTransport=this instanceof Native;
 for(const n of ['token','done','stream_end'])this.addEventListener(n,()=>window.seen.push(n));}};
}'''


def main():
    from playwright.sync_api import sync_playwright
    out = Path(os.environ.get('COMPACTION_EVIDENCE') or tempfile.mkdtemp(prefix='compaction-browser-'))
    out.mkdir(parents=True, exist_ok=True)
    subprocess.run([str(ROOT/'.venv/bin/python'), str(Path(__file__).resolve()), '--payload', str(out/'payload.json')],
                   check=True, cwd=ROOT, env={**os.environ, 'PYTHONDONTWRITEBYTECODE': '1'}, timeout=45)
    data = json.loads((out/'payload.json').read_text())
    baseline = os.environ.get('COMPACTION_BASELINE')
    variant = data['variants']['baseline' if baseline else 'fixed']
    override = subprocess.check_output(['git', 'show', f'{baseline}:static/ui.js'], cwd=ROOT) if baseline else None
    report = {'baseline': baseline, 'sourceHashes': {}, 'backendHashes': data['backend_sha256'],
              'payloadSha256': hashlib.sha256((out/'payload.json').read_bytes()).hexdigest(),
              'scope': 'Offline APIs; boot/CDN omitted; native EventSource; real loadSession and paging; no answer scrolling. Real HTTP handler/server, persistence, provider and network reconnect timing NOT covered.',
              'backend': {'rawRows': len(data['raw']), 'projectedRows': len(data['projected']),
                          'originalOutsideTail': data['original_index'] < variant['tail']['_messages_offset'],
                          'onlyMergeAppendedReplay': len(data['raw']) == 801}, 'results': []}
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True, args=['--no-sandbox', '--disable-dev-shm-usage'])
        report['chromium'] = browser.version
        for width, height in [(1440,900),(522,1232)]:
            context = browser.new_context(viewport={'width':width,'height':height}, service_workers='block', reduced_motion='reduce')
            page = context.new_page()
            errors, console_errors, requests, external = [], [], [], []
            page.on('pageerror', lambda e, errors=errors: errors.append(str(e)))
            page.on('console', lambda m, console_errors=console_errors: console_errors.append(m.text) if m.type == 'error' else None)
            state = {'stream_calls':0}

            def route(rt, req, external=external, requests=requests, state=state):
                u = urlsplit(req.url); path = u.path; query = parse_qs(u.query)
                if u.hostname != 'compaction.test':
                    external.append(req.url); rt.fulfill(body='', content_type='text/plain'); return
                if req.is_navigation_request():
                    raw = (ROOT/'static/index.html').read_bytes()
                    report['sourceHashes']['static/index.html'] = hashlib.sha256(raw).hexdigest()
                    html = raw.decode().replace('__MAX_UPLOAD_BYTES__','1048576').replace('__CSRF_TOKEN_JSON__','null')
                    html = re.sub(r'<script[^>]*src="static/boot.js[^>]*></script>', '', html)
                    html = re.sub(r'<script[^>]*src="https://[^>]*></script>', '', html)
                    html = re.sub(r'<link[^>]*href="https://[^>]*>', '', html)
                    report['servedHtmlSha256'] = hashlib.sha256(html.encode()).hexdigest()
                    rt.fulfill(body=html, content_type='text/html'); return
                if path.startswith('/static/') and (ROOT/path.lstrip('/')).is_file():
                    file = ROOT/path.lstrip('/')
                    raw = override if file.name == 'ui.js' and override is not None else file.read_bytes()
                    report['sourceHashes'][str(file.relative_to(ROOT))] = hashlib.sha256(raw).hexdigest()
                    rt.fulfill(body=raw, content_type=mimetypes.guess_type(file)[0] or 'application/octet-stream'); return
                requests.append({'method':req.method,'url':req.url})
                if path == '/api/chat/stream':
                    state['stream_calls'] += 1
                    first = state['stream_calls'] == 1
                    events = [('token', {'text':FINAL})] if first else []
                    events += [('done', {'status':'completed','session':data['done'] if first else variant['reconnect']}), ('stream_end',{})]
                    rt.fulfill(body=''.join(f'event: {name}\ndata: {json.dumps(body)}\n\n' for name,body in events), content_type='text/event-stream')
                elif path == '/api/session':
                    response = copy.deepcopy(variant['older'] if 'msg_before' in query else variant['tail'])
                    if 'msg_before' in query:
                        assert int(query['msg_before'][0]) == variant['tail']['_messages_offset']
                    if query.get('messages') == ['0']:
                        response.pop('messages')
                    rt.fulfill(json={'session':response})
                elif 'text/event-stream' in req.headers.get('accept',''):
                    rt.fulfill(status=204,body='')
                else:
                    rt.fulfill(json={'ok':True,'sessions':[],'files':[],'models':[],'tools':[],'todos':[],'tasks':[]})

            page.route('**/*', route)
            result = {'width':width,'height':height,'phases':{},'checks':{},'failures':[]}

            def capture(phase, expected, page=page, result=result, width=width, height=height):
                page.wait_for_timeout(400)
                measured = page.evaluate(MEASURE, {'final':FINAL,'replay':REPLAY})
                result['phases'][phase] = measured
                page.screenshot(path=str(out/f'{width}-{phase}.png'))
                checks = {
                    'unique_final': measured['finalInMessages'] == measured['finalDomCount'] == 1,
                    'final_in_viewport': bool(measured['final'] and measured['final']['intersectsViewport']),
                    'literal_human_quote_preserved': measured['quoteRenderable'] and measured['headerDomCount'] == 1,
                    'replay_not_human_bubble': measured['replayRenderable'] == 0,
                    'raw_final_coordinate': measured['finalAbsoluteIndex'] == 799,
                    'raw_window_and_count': (measured['offset'], measured['loaded'], measured['count']) == expected,
                    'history_retained': measured['history'],
                    'exact_viewport': (measured['innerWidth'],measured['innerHeight'],measured['visualWidth']) == (width,height,width),
                    'no_body_overflow': measured['bodyOverflow'] <= 2,
                }
                result['checks'][phase] = checks
                result['failures'] += [f'{phase}: {name}' for name, passed in checks.items() if not passed]

            try:
                page.goto('http://compaction.test/', wait_until='load')
                page.evaluate(OBSERVER)
                page.evaluate('''({initial,sid,stream})=>{
                    S.session={session_id:sid,message_count:799,source_tag:'webui',model:'offline-fixture'};
                    S.messages=initial;S.toolCalls=[];S.busy=true;S.activeStreamId=stream;
                    _oldestIdx=770;_messagesTruncated=true;renderMessages();attachLiveStream(sid,stream);
                }''', {'initial':data['done']['messages'][:-1], 'sid':SID, 'stream':STREAM})
                page.wait_for_function('()=>!S.busy&&S.activeStreamId===null',timeout=10000)
                capture('done',(770,30,800))
                page.evaluate('(sid)=>loadSession(sid,{force:true,keepStaleUntilLoaded:true})',SID)
                page.wait_for_function('()=>S.session.message_count===801',timeout=10000)
                tail = variant['tail']
                capture('refresh',(tail['_messages_offset'],len(tail['messages']),801))
                page.evaluate('()=>_loadOlderMessages()')
                older = variant['older']
                capture('older',(older['_messages_offset'],len(older['messages'])+len(tail['messages']),801))
                page.reload(wait_until='load')
                page.wait_for_function("()=>typeof loadSession==='function'",timeout=10000)
                page.evaluate(OBSERVER)
                page.evaluate('(sid)=>loadSession(sid,{force:true})',SID)
                page.wait_for_function('()=>S.messages.length>=30',timeout=10000)
                capture('reload',(tail['_messages_offset'],len(tail['messages']),801))
                page.evaluate('''({sid,stream})=>{S.busy=true;S.activeStreamId=stream;attachLiveStream(sid,stream);}''',{'sid':SID,'stream':STREAM})
                page.wait_for_function('()=>!S.busy&&S.activeStreamId===null',timeout=10000)
                capture('reconnect',(770,31,801))
                checks = {'native_done_and_reconnect': state['stream_calls']==2 and result['phases']['done']['native'] and result['phases']['reconnect']['native'],
                          'native_events_observed': all('done' in result['phases'][p]['events'] and 'stream_end' in result['phases'][p]['events'] for p in ['done','reconnect']),
                          'reconnect_raw_replay_retained': result['phases']['reconnect']['replayInMessages']==1,
                          'metadata_and_tail_requests': any('messages=0' in r['url'] for r in requests) and any('messages=1' in r['url'] for r in requests),
                          'real_cursor_paging': any('msg_before=' in r['url'] and 'msg_boundary=1' in r['url'] for r in requests)}
                result['checks']['lifecycle'] = checks
                result['failures'] += [name for name,passed in checks.items() if not passed]
            except Exception as exc:
                result['failures'].append(f'{type(exc).__name__}: {exc}')
                page.screenshot(path=str(out/f'{width}-harness-failure.png'))
            result.update(jsErrors=errors,consoleErrors=console_errors,requests=requests,blockedExternal=external)
            if errors or console_errors: result['failures'].append('JavaScript errors')
            if external: result['failures'].append('external request intercepted')
            report['results'].append(result)
            context.close()
        browser.close()
    report['harnessSha256'] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    report['failureCount'] = sum(len(r['failures']) for r in report['results'])
    (out/'results.json').write_text(json.dumps(report,indent=2))
    print(json.dumps({'baseline':baseline,'results': [{'width':r['width'],'phases':list(r['phases']),'failures':r['failures']} for r in report['results']],
                      'failureCount':report['failureCount'],'artifacts':str(out)},indent=2))
    return int(bool(report['failureCount']))


if __name__ == '__main__':
    if len(sys.argv) == 3 and sys.argv[1] == '--payload':
        payloads(Path(sys.argv[2]))
    else:
        raise SystemExit(main())
