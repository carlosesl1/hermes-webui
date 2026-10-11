#!/usr/bin/env python3
"""Offline integrated UX gate: real index/boot/server + Chromium, no inference.

Run with the repository venv and --chromium /path/to/chrome --output /evidence.
The launcher re-execs with a minimal environment before importing application or
Playwright code. Synthetic sessions/settings are persisted in private scratch.
Read-only runtime/catalog API fixtures are listed in report.json; session reads,
search and JSON export use the actual backend. Busy state is explicitly injected
through the real frontend state/functions, never represented as live inference.
Failures preserve DOM/screenshots and yield a nonzero exit; cases are independent.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import tempfile
import traceback
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
SCRATCH = Path(os.environ.get('TMPDIR', tempfile.gettempdir()))
MARKER = 'ORQUIDEA-ANTIGA-UX-731'
FINAL = 'Resposta final da conversa sintética: todos os dados são locais.'
SEED = r'''
import json, os
from pathlib import Path
from api.models import Session, SESSION_DIR
SESSION_DIR.mkdir(parents=True, exist_ok=True)
rows = [{'role':'user' if i % 2 == 0 else 'assistant',
         'timestamp':1700000000+i, 'message_uid':f'ux-long-{i}',
         'content':f'Mensagem histórica {i}: conteúdo sintético de auditoria.'}
        for i in range(90)]
rows[8]['content'] = '__MARKER__: mensagem antiga fora das cinco primeiras e da cauda inicial.'
rows[-1]['content'] = '__FINAL__'
small = [{'role':'user','content':'Pedido sintético de QA em português.','message_uid':'ux-small-0','timestamp':1700000000},
         {'role':'assistant','content':'__FINAL__','message_uid':'ux-small-1','timestamp':1700000001}]
for sid, title, messages in [('ux-small','Conversa pequena QA',small),('ux-long','Conversa histórica QA',rows)]:
    s=Session(session_id=sid,title=title,messages=messages,context_messages=messages[-20:],
              source_tag='webui',model='offline-test',context_length=131072,
              cwd=os.environ['HERMES_WEBUI_DEFAULT_WORKSPACE'])
    s.save()
print(json.dumps({'sessions':str(SESSION_DIR),'small':small,'long_count':len(rows)}))
'''.replace('__MARKER__', MARKER).replace('__FINAL__', FINAL)

# Defense in depth: no backend outbound sockets, even if a non-test route drifts.
SERVER = r'''
import socket, runpy
_connect=socket.socket.connect
_connect_ex=socket.socket.connect_ex
_getaddrinfo=socket.getaddrinfo
def local(address):
    return not isinstance(address,tuple) or address[0] in ('127.0.0.1','localhost','::1')
def connect(self,address):
    if not local(address): raise OSError('OFFLINE_QA blocked outbound connect: '+str(address))
    return _connect(self,address)
def connect_ex(self,address):
    if not local(address): raise OSError('OFFLINE_QA blocked outbound connect_ex: '+str(address))
    return _connect_ex(self,address)
def getaddrinfo(host,*args,**kwargs):
    if host not in (None,'127.0.0.1','localhost','::1'): raise OSError('OFFLINE_QA blocked DNS: '+str(host))
    return _getaddrinfo(host,*args,**kwargs)
socket.socket.connect=connect
socket.socket.connect_ex=connect_ex
socket.getaddrinfo=getaddrinfo
runpy.run_path('webui_runtime.py',run_name='__main__')
'''

GEOMETRY = r'''selectors => {
 const rect=e=>{const r=e.getBoundingClientRect();const s=getComputedStyle(e);
 return {x:r.x,y:r.y,width:r.width,height:r.height,right:r.right,bottom:r.bottom,
 visible:e.checkVisibility({checkOpacity:true,checkVisibilityCSS:true}),
 inViewport:e.checkVisibility({checkOpacity:true,checkVisibilityCSS:true})&&r.width>0&&r.height>0&&r.left>=-1&&r.top>=-1&&r.right<=innerWidth+1&&r.bottom<=innerHeight+1,
 text:e.innerText||e.getAttribute('aria-label')||'',fontSize:s.fontSize,
 clientWidth:e.clientWidth,scrollWidth:e.scrollWidth,ariaHidden:e.getAttribute('aria-hidden')};};
 return {width:innerWidth,height:innerHeight,visualWidth:visualViewport.width,
 bodyWidth:document.body.scrollWidth,documentWidth:document.documentElement.scrollWidth,
 controls:Object.fromEntries(selectors.map(s=>[s,document.querySelector(s)?rect(document.querySelector(s)):null]))};
}'''


def parse_args():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--chromium', required=True)
    p.add_argument('--output', required=True)
    p.add_argument('--viewports', default='1440x900,522x1232,390x844')
    return p.parse_args()


def isolated_launch(args):
    """Re-exec before any application/dependency import, without inherited overlays."""
    if os.environ.get('UX_QA_ISOLATED') == '1':
        return None
    SCRATCH.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='ux-', dir=SCRATCH) as tmp:
        base = Path(tmp)
        for sub in ['home', 'hermes', 'state', 'workspace', 'absent-core', 'tmp']:
            (base / sub).mkdir()
        (base / 'absent-core/run_agent.py').write_text('"""QA: no agent runtime installed."""\n')
        env = {'PATH': str(Path(sys.executable).parent) + ':/usr/bin:/bin',
               'LANG': 'C.UTF-8', 'HOME': str(base / 'home'), 'TMPDIR': str(base / 'tmp'),
               'HERMES_HOME': str(base / 'hermes'), 'HERMES_BASE_HOME': str(base / 'hermes'),
               'HERMES_CONFIG_PATH': str(base / 'hermes/config.yaml'),
               'HERMES_WEBUI_STATE_DIR': str(base / 'state'),
               'HERMES_WEBUI_DEFAULT_WORKSPACE': str(base / 'workspace'),
               'HERMES_WEBUI_AGENT_DIR': str(base / 'absent-core'),
               'HERMES_WEBUI_HOST': '127.0.0.1', 'HERMES_WEBUI_SKIP_ONBOARDING': '1',
               'HERMES_WEBUI_AUTO_INSTALL': '0', 'HERMES_WEBUI_CHAT_BACKEND': 'gateway',
               'HERMES_WEBUI_GATEWAY_BASE_URL': 'http://127.0.0.1:1',
               'NO_PROXY': 'localhost,127.0.0.1', 'no_proxy': 'localhost,127.0.0.1',
               'UX_QA_ISOLATED': '1', 'UX_QA_ROOT': str(base)}
        cmd = [sys.executable, str(Path(__file__).resolve()), '--chromium', args.chromium,
               '--output', args.output, '--viewports', args.viewports]
        return subprocess.run(cmd, env=env, cwd=ROOT, check=False).returncode


def main(args):
    from playwright.sync_api import sync_playwright
    from browser_conversation_lifecycle import _free_port, _wait_for_health, _terminate_process

    out = Path(args.output).resolve()
    out.mkdir(parents=True, exist_ok=True)
    report = {'scope': 'Real current index + boot + localhost WebUI; synthetic isolated state; offline Chromium',
              'limitations': ['No installed core, provider, inference, production, cross-browser or OS keyboard validation',
                              'External CDN fonts/Prism/xterm blocked, not substituted',
                              'Playwright service_workers=block injects serviceWorker access errors in sandboxed frames; logged separately',
                              'Busy/critical status: injected S.busy/activeStreamId and real updateSendBtn/setComposerStatus',
                              'Runtime metadata read endpoints use explicitly logged fixtures; no writes to product state'],
              'command': [sys.executable, str(Path(__file__).resolve()), *sys.argv[1:]],
              'environment': {k: os.environ.get(k) for k in ['HOME','HERMES_HOME','HERMES_WEBUI_STATE_DIR','HERMES_WEBUI_AGENT_DIR','PYTHONPATH']},
              'source': {}, 'checks': [], 'viewports': [], 'teardown': {}}
    # Source archives intentionally have no .git; hashes below are still evidence.
    for key, command in [('head', ['rev-parse', 'HEAD']), ('git_status_before', ['status', '--short'])]:
        try:
            result = subprocess.run(['git', *command], cwd=ROOT, text=True, capture_output=True, check=False)
            report[key] = result.stdout.strip() if result.returncode == 0 else None
        except FileNotFoundError:
            report[key] = None
    for path in (ROOT / 'static').glob('*'):
        if path.is_file() and path.suffix in ('.js', '.html', '.css'):
            report['source'][str(path.relative_to(ROOT))] = hashlib.sha256(path.read_bytes()).hexdigest()

    def persist():
        (out / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2))

    def check(name, passed, data=None):
        row = {'name': name, 'passed': bool(passed), 'data': data}
        report['checks'].append(row)
        persist()
        print(json.dumps(row, ensure_ascii=False), flush=True)

    settings = {'language': 'pt', 'theme': 'dark', 'onboarding_completed': True,
                'check_for_updates': False, 'default_workspace': os.environ['HERMES_WEBUI_DEFAULT_WORKSPACE'],
                'show_cli_sessions': False, 'sound_enabled': False, 'notifications_enabled': False,
                'hide_composer_status': True, 'default_message_mode': 'queue', 'tts_enabled': False}
    (Path(os.environ['HERMES_WEBUI_STATE_DIR']) / 'settings.json').write_text(json.dumps(settings))
    seeded = subprocess.run([sys.executable, '-c', SEED], cwd=ROOT, env=dict(os.environ),
                            capture_output=True, text=True, timeout=30)
    (out / 'seed.log').write_text(seeded.stdout + seeded.stderr)
    if seeded.returncode:
        check('fixture_seed', False, seeded.stderr)
        return 1
    report['fixture'] = json.loads(seeded.stdout.strip().splitlines()[-1])
    session_dir = Path(report['fixture']['sessions'])
    canonical = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in session_dir.glob('ux-*.json')}
    port = _free_port()
    url = f'http://127.0.0.1:{port}'
    env = dict(os.environ, HERMES_WEBUI_PORT=str(port))
    proc = browser = None
    log = (out / 'server.log').open('w')
    try:
        proc = subprocess.Popen([sys.executable, '-c', SERVER], cwd=ROOT, env=env,
                                stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
        ready = _wait_for_health(url, timeout=25, proc=proc)
        report['backend'] = {'url': url, 'pid': proc.pid, 'ready': ready,
                             'entrypoint': 'webui_runtime.py (socket egress guard, no source overlay)'}
        check('backend_ready', ready)
        if not ready:
            return 1
        with sync_playwright() as pw:
            browser = pw.chromium.launch(executable_path=args.chromium, headless=True,
                args=['--no-sandbox', '--disable-dev-shm-usage', '--disable-background-networking',
                      '--disable-component-update', '--disable-domain-reliability'])
            report['chromium'] = {'executable': args.chromium, 'version': browser.version}
            def run_viewport(viewport):
                w, h = map(int, viewport.split('x'))
                folder = out / viewport
                folder.mkdir(exist_ok=True)
                events = {'pageerrors': [], 'console': [], 'resource_failures': [],
                          'http_failures': [], 'requests': [], 'fixtures': [], 'blocked_external': [],
                          'blocked_actions': []}
                data = {'viewport': viewport, 'events': events, 'snapshots': []}
                report['viewports'].append(data)
                context = browser.new_context(viewport={'width': w, 'height': h}, locale='pt-BR',
                                              accept_downloads=True, service_workers='block')
                context.add_init_script("if(window.top===window){localStorage.setItem('hermes-lang','pt');sessionStorage.setItem('hermes-update-checked','1');}")
                page = context.new_page()
                context.set_default_timeout(6000)
                context.set_default_navigation_timeout(20000)
                page.on('dialog', lambda dialog: dialog.accept())
                page.on('pageerror', lambda err: events['pageerrors'].append({'message':str(err),'stack':err.stack,'class':'fixture_playwright' if "'serviceWorker'" in str(err) and 'sandboxed' in str(err) else 'core'}))
                page.on('console', lambda msg: events['console'].append({'type': msg.type, 'text': msg.text, 'location': msg.location}))
                page.on('requestfailed', lambda req: events['resource_failures'].append({'url': req.url, 'error': req.failure,
                    'class': 'cdn_fixture' if urlsplit(req.url).netloc != urlsplit(url).netloc else 'core'}))
                page.on('response', lambda res: events['http_failures'].append({'url': res.url, 'status': res.status}) if res.status >= 400 else None)

                fixtures = {
                    '/api/models': {'models': [{'id': 'offline-test','name':'offline-test','provider':'offline'}], 'default_model':'offline-test'},
                    '/api/profile/active': {'name':'default','is_default':True,'default_workspace':env['HERMES_WEBUI_DEFAULT_WORKSPACE']},
                    '/api/profiles': {'profiles':[{'name':'default','is_default':True,'active':True}], 'active':'default'},
                    '/api/skills': {'skills':[]}, '/api/skills/usage': {'usage':{}},
                    '/api/crons': {'jobs':[], 'timezone':'UTC'},
                    '/api/providers': {'providers':[]}, '/api/plugins': {'plugins':[]},
                    '/api/extensions/status': {'extensions':[]}, '/api/extensions/registry': {'extensions':[]},
                    '/api/gateway/status': {'running':False,'status':'stopped'},
                    '/api/health/agent': {'status':'stopped','running':False},
                    '/api/model/auxiliary': {'models':{},'auxiliary':{}},
                    '/api/transcribe/capability': {'available':False},
                }

                def route_request(route, request):
                    parsed = urlsplit(request.url)
                    path = parsed.path
                    if parsed.netloc != urlsplit(url).netloc:
                        events['blocked_external'].append(request.url)
                        route.abort('blockedbyclient')
                        return
                    events['requests'].append({'method':request.method,'path':path,'query':parsed.query})
                    if path.startswith(('/api/chat/', '/api/onboarding/probe', '/api/model/set', '/api/models/refresh')):
                        events['blocked_actions'].append({'path':path,'method':request.method})
                        route.abort('blockedbyclient')
                        return
                    if path in fixtures:
                        events['fixtures'].append(path)
                        route.fulfill(json=fixtures[path])
                        return
                    if path.startswith(('/api/update', '/api/provider/', '/api/models/', '/api/model/', '/api/terminal/')):
                        events['fixtures'].append(path)
                        route.fulfill(json={})
                        return
                    if request.method not in ('GET','HEAD') and path not in ('/api/settings','/api/session/draft'):
                        events['blocked_actions'].append({'path':path,'method':request.method})
                        route.abort('blockedbyclient')
                        return
                    route.continue_()

                context.route('**/*', route_request)

                def snapshot(name, selectors=()):
                    geometry = page.evaluate(GEOMETRY, list(selectors))
                    data['snapshots'].append({'name':name,'url':page.url,'geometry':geometry})
                    page.screenshot(path=str(folder / f'{name}.png'), full_page=True)
                    (folder / f'{name}.html').write_text(page.content())
                    (folder / f'{name}.txt').write_text(page.locator('body').inner_text())
                    (folder / f'{name}.a11y.yml').write_text(page.locator('body').aria_snapshot())
                    persist()
                    return geometry

                def boot(sid='ux-small'):
                    page.goto(url + '/session/' + sid, wait_until='domcontentloaded',timeout=20000)
                    page.wait_for_function("sid => typeof S!=='undefined' && S._bootReady && _navigation.ready && !_navigation.restoring && S.session?.session_id===sid && $('msgInner').innerText.includes('Resposta final')", arg=sid, timeout=20000)
                    page.wait_for_timeout(250)

                def mobile_open():
                    if w < 768:
                        sidebar = page.locator('.sidebar')
                        if sidebar.bounding_box()['x'] < -1:
                            page.locator('#btnHamburger').click()
                            page.wait_for_timeout(300)

                def nav(panel):
                    mobile_open()
                    scope = '.sidebar-nav' if w < 768 else '.rail'
                    page.locator(f'{scope} [data-panel="{panel}"]').click()
                    page.wait_for_function('p => _currentPanel===p', arg=panel)

                def section(name):
                    mobile_open()
                    page.locator(f'#settingsMenu [data-settings-section="{name}"]').click()
                    page.wait_for_function('s => _currentSettingsSection===s', arg=name)
                    page.wait_for_timeout(300)

                def run_case(name, fn):
                    try:
                        boot()
                        fn()
                        check(viewport + '/' + name + '/completed', True)
                    except Exception as exc:
                        check(viewport + '/' + name + '/completed', False, str(exc))
                        (folder / f'{name}-exception.txt').write_text(traceback.format_exc())
                        try:
                            snapshot(name + '-failure')
                        except Exception:
                            pass
                    finally:
                        persist()

                def navigation_case():
                    cdp=context.new_cdp_session(page)
                    def history_snapshot(label):
                        data.setdefault('history_trace',[]).append({'label':label,'cdp':cdp.send('Page.getNavigationHistory'),'js':page.evaluate('() => ({url:location.href,state:history.state,index:_navigation.index,generation:_navigation.generation,restoring:_navigation.restoring})')})
                        persist()
                    check(viewport+'/boot_pt', page.evaluate("document.documentElement.lang.startsWith('pt')"))
                    if w < 768:
                        mobile_open()
                        labels = page.locator('.sidebar-nav .nav-tab:not(.nav-tab-hidden) .nav-tab-label').evaluate_all("els=>els.filter(e=>e.getBoundingClientRect().width>0).map(e=>({text:e.innerText,w:e.clientWidth,sw:e.scrollWidth,font:parseFloat(getComputedStyle(e).fontSize),r:e.getBoundingClientRect().toJSON(),p:e.parentElement.getBoundingClientRect().toJSON()}))")
                        check(viewport+'/mobile_labels_readable', len(labels)>=8 and all(x['text'] and x['font']>=10 and x['sw']<=x['w']+1 and x['r']['right']<=x['p']['right']+1 for x in labels), labels)
                        snapshot('mobile-menu', ['.sidebar-nav','.sidebar'])
                    nav('settings')
                    section('preferences')
                    page.reload(wait_until='domcontentloaded')
                    page.wait_for_function("() => S._bootReady && _navigation.ready && !_navigation.restoring && _currentPanel==='settings' && _currentSettingsSection==='preferences'", timeout=20000)
                    check(viewport+'/preferences_reload', '#panel=settings&section=preferences' in page.url, page.url)
                    history_snapshot('before conversation')
                    page.locator('#settingsMaxTokens').fill('8192')
                    page.wait_for_function('() => _settingsDirty')
                    section('conversation')
                    history_snapshot('after conversation')
                    page.evaluate('() => history.back()')
                    page.wait_for_timeout(500)
                    history_snapshot('after back')
                    page.wait_for_function("() => _currentSettingsSection==='preferences'")
                    check(viewport+'/section_back', 'section=preferences' in page.url)
                    page.evaluate('() => history.forward()')
                    page.wait_for_function("() => _currentSettingsSection==='conversation'")
                    check(viewport+'/section_forward', 'section=conversation' in page.url)
                    check(viewport+'/dirty_settings_history_preserved', page.locator('#settingsMaxTokens').input_value()=='8192' and page.evaluate('() => _settingsDirty'))
                    # Revert only the synthetic unsaved edit; do not persist settings.
                    page.evaluate('() => {$("settingsMaxTokens").value="";_settingsDirty=false;}')
                    nav('tasks')
                    page.reload(wait_until='domcontentloaded')
                    page.wait_for_function("() => S._bootReady && _navigation.ready && !_navigation.restoring && _currentPanel==='tasks'", timeout=20000)
                    check(viewport+'/panel_reload', '#panel=tasks' in page.url)
                    page.evaluate('() => history.back()')
                    page.wait_for_function("() => _currentPanel==='settings'")
                    page.evaluate('() => history.forward()')
                    page.wait_for_function("() => _currentPanel==='tasks'")
                    check(viewport+'/panel_back_forward', '#panel=tasks' in page.url)
                    g=snapshot('panel-reload', ['.sidebar'])
                    check(viewport+'/navigation_body_no_overflow',g['documentWidth']<=w and g['bodyWidth']<=w,g)

                def composer_case():
                    page.locator('#msg').fill('Rascunho local; não enviar.')
                    page.wait_for_function("() => $('btnSend').dataset.action==='send' && $('btnSend').classList.contains('visible')")
                    page.wait_for_timeout(250)
                    g=snapshot('composer-idle',['#msg','#btnSend'])
                    check(viewport+'/composer_idle_visible',all(x and x['inViewport'] for x in g['controls'].values()),g)
                    for mode in ['queue','steer','interrupt']:
                        page.evaluate("mode => { S.busy=true;S.activeStreamId='fixture-not-a-real-stream';window._defaultMessageMode=mode;window._composerControlVisibility={...(window._composerControlVisibility||{}),hide_composer_status:true};updateSendBtn();setComposerStatus('Recuperando conexão. Rascunho preservado.',0,'recovery'); }",mode)
                        g=snapshot('composer-busy-'+mode,['#msg','#btnSend','#btnComposerStop','#composerActionLabel','#composerOperationalStatus'])
                        actual=page.locator('#btnSend').get_attribute('data-action')
                        check(viewport+'/busy_'+mode+'_critical_visible', all(x and x['inViewport'] for x in g['controls'].values()) and actual==mode, {'actual_action':actual,'geometry':g})
                        check(viewport+'/busy_'+mode+'_body_no_overflow',g['bodyWidth']<=w and g['documentWidth']<=w)
                    page.evaluate("() => {S.busy=false;S.activeStreamId=null;setComposerStatus('');updateSendBtn();}")
                    page.locator('#msg').fill('')

                def preferences_case():
                    nav('settings')
                    section('preferences')
                    check(viewport+'/auxiliary_collapsed_default',not page.locator('#settingsAuxiliaryAdvanced').evaluate('(e)=>e.open'))
                    page.locator('#settingsAuxiliaryAdvanced > summary').focus()
                    page.keyboard.press('Enter')
                    check(viewport+'/auxiliary_keyboard_open',page.locator('#settingsAuxiliaryAdvanced').evaluate('(e)=>e.open'))
                    page.keyboard.press('Enter')
                    mobile_open()
                    page.locator('#settingsSearch').fill('auxiliary')
                    page.locator('#settingsSearchResults .settings-search-result').first.wait_for(state='visible')
                    page.locator('#settingsSearchResults .settings-search-result').first.click()
                    page.wait_for_timeout(450)
                    g=snapshot('preferences-search',['#settingsAuxiliaryAdvanced','#settingsAuxiliaryField','#btnResetAuxModels'])
                    check(viewport+'/advanced_search_reveals_control',page.locator('#settingsAuxiliaryAdvanced').evaluate('(e)=>e.open') and g['controls']['#btnResetAuxModels']['inViewport'],g)

                def export_case():
                    nav('settings')
                    section('conversation')
                    group=page.evaluate("() => ({closed:!$('conversationExportDetails').open,export:!!$('btnExportJSON').closest('#conversationExportDetails'),share:!!$('btnShareSession').closest('[aria-labelledby=conversationSharingHeading]'),clear:!!$('btnClearConvModal').closest('.settings-conversation-danger'),scope:$('conversationClearHeading').parentElement.innerText})")
                    check(viewport+'/export_share_clear_grouping',all(group[k] for k in ['closed','export','share','clear']),group)
                    page.locator('#conversationExportDetails > summary').click()
                    for selector,extension in [('#btnDownload','md'),('#btnExportJSON','json')]:
                        with page.expect_download(timeout=12000) as awaited:
                            page.locator(selector).click()
                        download=awaited.value
                        dest=folder / ('transcript.'+extension)
                        download.save_as(str(dest))
                        raw=dest.read_text()
                        valid=FINAL in raw and 'Pedido sintético' in raw
                        if extension=='json':
                            payload=json.loads(raw)
                            session=payload.get('session',payload)
                            valid=valid and len(session.get('messages',[]))==2
                        check(viewport+'/real_download_'+extension,valid,{'path':str(dest),'bytes':dest.stat().st_size,'suggested_filename':download.suggested_filename,'sha256':hashlib.sha256(dest.read_bytes()).hexdigest()})
                    g=snapshot('exports',['#btnDownload','#btnExportJSON','#btnShareSession','#btnClearConvModal'])
                    check(viewport+'/export_controls_unclipped',all(x and x['inViewport'] for x in g['controls'].values()),g)

                def dialog_case():
                    nav('skills')
                    page.locator('#panelSkills button[onclick="openSkillCreate()"]').click()
                    page.locator('#skillFormName').fill('rascunho-nao-salvo')
                    page.locator('#skillFormContent').fill('# Rascunho sintético\nNão salvar.')
                    mobile_open()
                    scope='.sidebar-nav' if w<768 else '.rail'
                    trigger=page.locator(f'{scope} [data-panel="chat"]')
                    trigger.click()
                    dialog=page.locator('dialog.app-dialog[open]')
                    dialog.wait_for(state='visible')
                    g=snapshot('dirty-dialog',['dialog.app-dialog[open]','dialog [data-choice="keep"]','dialog [data-choice="discard"]','dialog [data-choice="save"]'])
                    r=g['controls']['dialog.app-dialog[open]']
                    check(viewport+'/dirty_dialog_centered',abs(r['x']+r['width']/2-w/2)<=2 and abs(r['y']+r['height']/2-h/2)<=2,g)
                    check(viewport+'/dirty_dialog_initial_focus',page.evaluate("document.activeElement?.dataset.choice==='keep'"))
                    trapped=True
                    focus_trace=[]
                    for _ in range(5):
                        page.keyboard.press('Tab')
                        state=page.evaluate("() => ({tag:document.activeElement.tagName,id:document.activeElement.id,choice:document.activeElement.dataset.choice,inside:!!document.activeElement.closest('dialog[open]')})")
                        focus_trace.append(state)
                        # Native modal Tab can temporarily focus browser chrome (BODY).
                        trapped=trapped and (state['inside'] or state['tag']=='BODY')
                    check(viewport+'/dirty_dialog_focus_trap',trapped,focus_trace)
                    page.keyboard.press('Escape')
                    check(viewport+'/dirty_dialog_escape_keeps_draft',page.locator('#skillFormName').input_value()=='rascunho-nao-salvo' and page.evaluate("_currentPanel==='skills'"))
                    check(viewport+'/dirty_dialog_focus_return',trigger.evaluate('(e)=>e===document.activeElement'))
                    trigger.click()
                    page.locator('dialog [data-choice="discard"]').click()
                    page.wait_for_function("() => _currentPanel==='chat'")
                    check(viewport+'/dirty_discard_navigates',True)

                def search_case():
                    # Observation-only wrapper: delegate unchanged arguments/result to the real jump.
                    page.evaluate("() => {window.__uxJump=[];const original=jumpToTurnQuestion;jumpToTurnQuestion=async(...args)=>{window.__uxJump.push({args,phase:'start'});const result=await original(...args);window.__uxJump.push({args,phase:'end'});return result;};}")
                    mobile_open()
                    before=sum(x['path']=='/api/sessions/search' and 'scope=full' in x['query'] for x in events['requests'])
                    page.locator('#sessionSearch').fill(MARKER)
                    page.wait_for_timeout(700)
                    quick=sum(x['path']=='/api/sessions/search' and 'scope=full' in x['query'] for x in events['requests'])
                    check(viewport+'/full_search_not_per_keystroke',quick==before)
                    page.locator('#sessionSearchStatus button').first.click()
                    page.wait_for_function("() => _sessionSearchState.status==='ready' && _contentSearchResults.length>0",timeout=12000)
                    snapshot('search-results',['#sessionSearchStatus'])
                    result=page.locator('#sessionList [data-id="ux-long"]')
                    if not result.count():
                        result=page.locator('#sessionList .session-item').filter(has_text='Conversa histórica QA').first
                    result.click()
                    page.wait_for_function("marker => S.session?.session_id==='ux-long' && $('msgInner').innerText.includes(marker)",arg=MARKER,timeout=15000)
                    page.wait_for_timeout(1200)
                    data['search_state']=page.evaluate("() => ({results:_contentSearchResults,offset:_oldestIdx,loaded:S.messages.length,jumps:window.__uxJump,target:S.messages[8],toast:document.querySelector('.toast')?.innerText,frames:[...document.querySelectorAll('iframe')].map(f=>({src:f.src,sandbox:f.getAttribute('sandbox')}))})")
                    g=snapshot('search-open-old',['#msgInner'])
                    found=page.locator('#msgInner .msg').filter(has_text=MARKER).first
                    if not found.count():
                        found=page.get_by_text(MARKER+': mensagem antiga fora das cinco primeiras e da cauda inicial.',exact=False).first
                    r=found.bounding_box()
                    view=page.locator('#messages').bounding_box()
                    visible=bool(r and view and min(r['y']+r['height'],view['y']+view['height'])>max(r['y'],view['y']))
                    check(viewport+'/full_search_old_match_in_view',visible,{'message':r,'viewport':view,'geometry':g})

                try:
                    for name,fn in [('navigation',navigation_case),('composer',composer_case),('preferences',preferences_case),('export',export_case),('dialog',dialog_case),('search',search_case)]:
                        run_case(name,fn)
                    core_errors=[e for e in events['pageerrors'] if e['class']=='core']
                    check(viewport+'/no_core_pageerrors',not core_errors,core_errors)
                    check(viewport+'/no_accidental_actions',not events['blocked_actions'],events['blocked_actions'])
                    (folder / 'events.json').write_text(json.dumps(events,ensure_ascii=False,indent=2))
                finally:
                    context.close()
                    persist()
            for viewport in args.viewports.split(','):
                run_viewport(viewport)
            browser.close()
            browser=None
        after={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in session_dir.glob('ux-*.json')}
        check('canonical_transcripts_unchanged',canonical==after,{'before':canonical,'after':after})
    except Exception as exc:
        check('harness_execution',False,str(exc))
        (out/'harness-exception.txt').write_text(traceback.format_exc())
    finally:
        if browser:
            try:
                browser.close()
            except Exception:
                pass
        if proc:
            try:
                os.killpg(proc.pid,signal.SIGTERM)
            except ProcessLookupError:
                pass
            _terminate_process(proc)
            try:
                os.killpg(proc.pid,signal.SIGKILL)
            except ProcessLookupError:
                pass
        log.close()
        with socket.socket() as sock:
            sock.settimeout(1)
            closed=sock.connect_ex(('127.0.0.1',port))!=0
        report['teardown']={'listener_closed':closed,'process_returncode':proc.returncode if proc else None}
        check('teardown_listener_closed',closed)
        report['totals']={'passed':sum(c['passed'] for c in report['checks']),
                          'failed':sum(not c['passed'] for c in report['checks'])}
        persist()
    print(json.dumps({'report':str(out/'report.json'),'totals':report['totals']}),flush=True)
    return int(bool(report['totals']['failed']))


if __name__ == '__main__':
    options=parse_args()
    result=isolated_launch(options)
    raise SystemExit(main(options) if result is None else result)
