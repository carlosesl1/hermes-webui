"""UX07: real bounded reader + route scope and asynchronous browser helpers."""
import hashlib
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]


def _fixture(path, sid="s1", profile="default"):
    messages = [{"role": "user", "content": "ordinary"} for _ in range(7)]
    messages.append({"role": "assistant", "content": "unique late phrase <script>", "message_uid": "late-uid"})
    data = {"session_id": sid, "profile": profile, "messages": messages}
    (path / (sid + ".json")).write_text(json.dumps(data))
    return data


def _search(path, rows, cursor=""):
    from api.session_search import search_saved_history
    from api.routes import _session_search_message_text, _session_search_preview
    return search_saved_history(rows, "late phrase", cursor, path, _session_search_message_text, _session_search_preview)


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
let _loadSessionGeneration=1;
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
