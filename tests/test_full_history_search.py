"""UX07: real bounded reader + route scope and asynchronous browser helpers."""
import hashlib
import json
import os
import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from urllib.parse import urlparse

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _fixture(path, sid="s1", profile="default"):
    messages = [{"role": "user", "content": "ordinary"} for _ in range(7)]
    messages.append({"role": "assistant", "content": "unique late phrase <script>", "message_uid": "late-uid"})
    data = {"session_id": sid, "profile": profile, "messages": messages}
    (path / (sid + ".json")).write_text(json.dumps(data))
    return data


def _search(path, rows, cursor="", profile="default", all_profiles=False):
    from api.session_search import search_saved_history
    from api.routes import _session_search_message_text, _session_search_preview
    return search_saved_history(rows, "late phrase", cursor, path, _session_search_message_text, _session_search_preview,
                                active_profile=profile, all_profiles=all_profiles)


def test_phrase_after_fifth_row_and_stable_location(tmp_path):
    data = _fixture(tmp_path)
    result = _search(tmp_path, [data])
    assert result["count"] == 1
    hit = result["sessions"][0]
    assert "late phrase" in hit["match_preview"]
    assert hit["match_location"]["message_uid"] == "late-uid"
    assert hit["match_location"]["index"] == 7
    assert hit["match_location"]["text_sha256"] == hashlib.sha256(data["messages"][7]["content"].encode()).hexdigest()


def test_pagination_bytes_missing_and_no_lru(tmp_path):
    from api import session_search
    rows = [_fixture(tmp_path, "s1"), _fixture(tmp_path, "s2")]
    with patch.object(session_search, "PAGE_SESSIONS", 1):
        page = _search(tmp_path, rows)
        assert page["scanned"] == 1 and page["next_cursor"]
        next_page = _search(tmp_path, rows, page["next_cursor"])
        assert next_page["sessions"][0]["session_id"] == "s2"
        assert next_page["next_cursor"] is None
    with patch.object(session_search, "PAGE_BYTES", 10):
        page = _search(tmp_path, rows)
        assert page["count"] == 0 and page["skipped"] == 2 and page["incomplete"]
    (tmp_path / "s1.json").unlink()
    assert _search(tmp_path, rows)["skipped"] == 1


def test_route_preserves_fast_depth_and_active_profile(tmp_path):
    import api.routes as routes
    active = _fixture(tmp_path)
    foreign = _fixture(tmp_path, "foreign", "other")
    captured = []
    with patch("api.routes.all_sessions", return_value=[active, foreign]), patch("api.config.SESSION_DIR", tmp_path), patch("api.profiles.get_active_profile_name", return_value="default"), patch("api.routes.j", side_effect=lambda h, p, **kw: captured.append(p)), patch("api.routes.get_session_for_scan", return_value=SimpleNamespace(messages=active["messages"])):
        routes._handle_sessions_search(None, urlparse("/api/sessions/search?q=late%20phrase&content=1&depth=5"))
        assert captured[-1]["count"] == 0
        routes._handle_sessions_search(None, urlparse("/api/sessions/search?q=late%20phrase&scope=full"))
    assert [s["session_id"] for s in captured[-1]["sessions"]] == ["s1"]
    assert captured[-1]["active_profile"] == "default"


def _run_js(body):
    src = (ROOT / "static/sessions.js").read_text()
    state = src[src.index("let _searchDebounceTimer"):src.index("function _sessionSearchRanges")]
    helpers = src[src.index("function _sessionSearchScope"):src.index("function _sessionTimestampMs")]
    script = """
const assert=require('node:assert/strict');
const input={value:'late phrase'};
const S={activeProfile:'default',messages:[],session:null};
const $=()=>input;
let timer; function setTimeout(f){timer=f;return 1;} function clearTimeout(){timer=null;}
function syncSessionSearchClear(){} function _syncArchivedSearchPagingRefresh(){}
function renderSessionListFromCache(){} function _sessionSearchDirectAndTitleMatches(){return [];}
const _allSessions=[];
let _loadSessionGeneration=1, _profileSwitchGeneration=0;
""" + state + helpers + "\n(async()=>{" + body + "})().catch(e=>{console.error(e);process.exit(1)});"
    subprocess.run(["node", "-e", script], cwd=ROOT, check=True, capture_output=True, text=True)


def test_cancel_stale_query_profile_and_error():
    _run_js("""
let resolve; let signal;
global.api=(url,opts)=>{signal=opts.signal;return new Promise(r=>resolve=r)};
let pending=searchSessionHistory();cancelSessionHistorySearch();
assert.equal(signal.aborted,true);
resolve({sessions:[{session_id:'stale'}],active_profile:'default'});await pending;
assert.equal(_contentSearchResults.length,0);assert.equal(_sessionSearchState.status,'cancelled');
pending=searchSessionHistory(); input.value='different'; filterSessions();
resolve({sessions:[{session_id:'stale'}],active_profile:'default'});await pending;
assert.equal(_contentSearchResults.length,0);
input.value='late phrase';pending=searchSessionHistory();S.activeProfile='other';
resolve({sessions:[{session_id:'foreign'}],active_profile:'default'});await pending;
assert.equal(_contentSearchResults.length,0);
S.activeProfile='default';global.api=async()=>{throw new Error('offline')};
await searchSessionHistory();assert.equal(_sessionSearchState.status,'error');
filterSessions();await timer();assert.equal(_sessionSearchState.status,'error');
""")


def test_open_verifies_uid_after_canonical_hydration_and_rejects_changed_text():
    _run_js("""
const cryptoModule=require('node:crypto');global.crypto=cryptoModule.webcrypto;
let jumped=-1,toasts=0,hydrated=0;
global._openSidebarSession=async s=>{S.session=s};
global.expandFullTranscript=async()=>{hydrated++;S.messages=[{role:'user',content:'first'},{role:'assistant',content:'late phrase',message_uid:'uid'}]};
global.jumpToTurnQuestion=async i=>{jumped=i};global.showToast=()=>{toasts++};global.t=k=>k;
const hit={session_id:'s1',profile:'default',match_location:{message_uid:'uid',role:'assistant',index:7,text_sha256:cryptoModule.createHash('sha256').update('late phrase').digest('hex')}};
await _openSessionSearchResult(hit);assert.equal(hydrated,1);assert.equal(jumped,1);
jumped=-1;hit.match_location.text_sha256='changed';await _openSessionSearchResult(hit);
assert.equal(jumped,-1);assert.equal(toasts,1);
""")


def test_cursor_binds_profile_store_and_scope(tmp_path):
    from api import session_search
    rows = [_fixture(tmp_path, "m1"), _fixture(tmp_path, "z1")]
    other = tmp_path / "other"
    other.mkdir()
    foreign = [_fixture(other, "a1", "other")]
    with patch.object(session_search, "PAGE_SESSIONS", 1):
        cursor = _search(tmp_path, rows)["next_cursor"]
        assert _search(tmp_path / ".", rows, cursor)["sessions"][0]["session_id"] == "z1"
        for directory, profile, all_profiles in [(other, "default", False), (tmp_path, "other", False), (tmp_path, "default", True)]:
            with pytest.raises(ValueError, match="scope"):
                _search(directory, foreign, cursor, profile, all_profiles)
        assert _search(other, foreign, profile="other")["sessions"][0]["session_id"] == "a1"


def test_profile_roundtrip_invalidates_pending_and_continuation():
    _run_js("""
let resolve,urls=[];
global.api=(url)=>{urls.push(url);return new Promise(r=>resolve=r)};
let pending=searchSessionHistory();
resolve({sessions:[{session_id:'m1'}],active_profile:'default',next_cursor:'page2'});await pending;
S.activeProfile='other';++_profileSwitchGeneration;
assert.equal(_syncSessionSearchOwner(),true);
assert.equal(_contentSearchResults.length,0);assert.equal(_sessionSearchState.cursor,null);
S.activeProfile='default';++_profileSwitchGeneration;
pending=searchSessionHistory();assert.equal(new URL(urls.at(-1),'http://local').searchParams.get('cursor'),'');
S.activeProfile='other';++_profileSwitchGeneration;S.activeProfile='default';++_profileSwitchGeneration;
resolve({sessions:[{session_id:'old-visit'}],active_profile:'default',next_cursor:'bad'});await pending;
assert.equal(_contentSearchResults.length,0);
""")


def test_cap_preserves_first_results_until_explicit_restart():
    _run_js("""
let calls=0;
global.api=async()=>({sessions:Array.from({length:24},(_,i)=>({session_id:'s'+(calls*24+i)})),active_profile:'default',next_cursor:'p'+(++calls)});
for(let i=0;i<10;i++) await searchSessionHistory();
assert.equal(_contentSearchResults.length,240);assert.equal(_contentSearchResults[0].session_id,'s0');
assert.equal(_sessionSearchState.status,'capped');assert.equal(_sessionSearchState.cursor,null);
await searchSessionHistory();assert.equal(calls,10);assert.equal(_contentSearchResults[0].session_id,'s0');
await searchSessionHistory({restart:true});assert.equal(calls,11);assert.equal(_contentSearchResults.length,24);
assert.equal(_sessionSearchState.capped,false);
""")


@pytest.mark.parametrize("width,target", [(1440, 17), (522, 181)])
def test_real_renderer_long_chat_jump_and_stale_match(tmp_path, width, target):
    """Controlled HTTP boundary; real hydration, virtualizer and scrolling.

    Boot/provider/navigation are not exercised. No renderer or jump stubs.
    HERMES_TEST_CHROMIUM reuses an installed browser without downloading one.
    """
    playwright = pytest.importorskip("playwright.sync_api")
    messages = [{"role": "assistant" if i % 2 else "user", "content": f"Ordinary row {i} " + "body text " * 12,
                 "message_uid": f"u{i}"} for i in range(520)]
    messages[target]["content"] = f"unique late phrase {target}"
    session = {"session_id": "long", "profile": "default", "title": "Long fixture", "messages": messages,
               "message_count": len(messages), "tool_calls": []}
    (tmp_path / "long.json").write_text(json.dumps(session))
    hit = _search(tmp_path, [session])["sessions"][0]
    requests, errors = [], []
    with playwright.sync_playwright() as p:
        executable = os.environ.get("HERMES_TEST_CHROMIUM")
        if not executable and not Path(p.chromium.executable_path).exists():
            pytest.skip("No installed Chromium; set HERMES_TEST_CHROMIUM")
        browser = p.chromium.launch(executable_path=executable, headless=True, args=["--no-sandbox"])
        page = browser.new_page(viewport={"width": width, "height": 900})
        page.on("pageerror", lambda error: errors.append(str(error)))

        def route_handler(route):
            url = urlparse(route.request.url)
            if url.path == "/":
                html = (ROOT / "static/index.html").read_text().replace("__MAX_UPLOAD_BYTES__", "1000000").replace("__CSRF_TOKEN_JSON__", '"offline-fixture"')
                route.fulfill(status=200, content_type="text/html", body=html)
            elif url.path == "/static/boot.js":
                route.fulfill(status=200, content_type="application/javascript", body="")
            elif url.path == "/api/session":
                requests.append(url.query)
                full = "content_full=1" in url.query
                route.fulfill(status=200, content_type="application/json", body=json.dumps({"session": {
                    **session, "messages": messages if full else messages[-30:],
                    "_messages_offset": 0 if full else 490, "_messages_truncated": not full}}))
            elif url.path.startswith("/api/"):
                route.fulfill(status=200, content_type="application/json", body="{}")
            elif url.hostname == "127.0.0.1" and url.path.startswith("/static/"):
                asset = ROOT / url.path.lstrip("/")
                if asset.is_file():
                    route.fulfill(path=str(asset))
                else:
                    route.fulfill(status=404, body="")
            else:
                route.abort()

        page.route("**/*", route_handler)
        page.goto("http://127.0.0.1:17899/", wait_until="load")
        page.evaluate("""() => {
          window.qaToasts=[];showToast=text=>qaToasts.push(text);
          window.qaJumps=0;const realJump=jumpToTurnQuestion;
          jumpToTurnQuestion=async(...args)=>{qaJumps++;return realJump(...args);};
          _openSidebarSession=async s=>{
            S.activeProfile='default';S.session=s;S.messages=[];S.busy=false;S.activeStreamId=null;
            _loadingSessionId=s.session_id;++_loadSessionGeneration;
            await _ensureMessagesLoaded(s.session_id,{loadGeneration:_loadSessionGeneration});
            _loadingSessionId=null;renderMessages();window.qaInitialCount=S.messages.length;
          };
        }""")
        page.evaluate("hit=>_openSessionSearchResult(hit)", hit)
        page.wait_for_function("""target=>{
          const c=document.getElementById('messages').getBoundingClientRect();
          return Array.from(document.querySelectorAll('[data-msg-idx="'+target+'"]')).some(n=>{
            const r=n.getBoundingClientRect();return n.getClientRects().length && r.top>=c.top-1 && r.top<c.bottom && r.bottom>c.top;
          });
        }""", arg=target, timeout=10000)
        outcome = page.evaluate("""() => ({initial:qaInitialCount,ids:S.messages.map(m=>m.message_uid),
          content:S.messages.map(m=>m.content),toasts:qaToasts,dom:document.querySelectorAll('[data-msg-idx]').length})""")
        assert outcome["initial"] == 30
        assert outcome["ids"] == [row["message_uid"] for row in messages]
        assert outcome["content"] == [row["content"] for row in messages]
        assert outcome["toasts"] == []
        assert outcome["dom"] < len(messages)
        assert any("msg_limit=" in query for query in requests)
        assert any("content_full=1" in query for query in requests)
        evidence = os.environ.get("HERMES_SEARCH_EVIDENCE")
        if evidence:
            Path(evidence).mkdir(parents=True, exist_ok=True)
            page.screenshot(path=str(Path(evidence) / f"search-jump-{width}.png"))
        # Same UID, edited text: fail closed and do not move to the old occurrence.
        messages[target]["content"] = "edited since search"
        page.evaluate("hit=>_openSessionSearchResult(hit)", hit)
        assert page.evaluate("({toasts:qaToasts.length,jumps:qaJumps})") == {"toasts": 1, "jumps": 1}
        assert not errors, errors
        browser.close()
