"""Offline Chromium RED/GREEN evidence for the two duplicate-settlement fixes.

Run with the existing Playwright runtime; no server/provider/private chat is used.
DUPLICATE_BASELINE=704cb8c3 serves that revision's messages.js and sessions.js.
DUPLICATE_EVIDENCE selects the results/screenshot directory. A baseline run is
expected to EXIT NONZERO: assertions are identical for baseline and patched.

Scope: production HTML/CSS/scripts, renderer, native EventSource token/tool/done
listeners, actual settlement and real backend bounded_render_messages previews.
Boot/network APIs are controlled offline. The clipped case deliberately supplies
an independently retained live scene and a user-only local message array at the
done boundary (controlled projection collaborator, not a claim about how every
provider/reconnect assembles that input). No settlement/merge/render helper is
replaced. The stream's full token was processed before supplying that input.
"""
import hashlib
import json
import mimetypes
import os
import re
import runpy
import subprocess
import tempfile
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
SID = "duplicate-fixture"
STREAM = "duplicate-run"
QUESTION = "CURRENT UNIQUE QUESTION: report the result."
FINAL = "FINAL ANSWER: the completed unique result is visible."
NARRATION = "PRE-TOOL NARRATION: I will inspect the offline results first."
FULL = "FINAL RESULT START\n\n" + "\n\n".join(
    f"Result {i}: verified offline fixture item with a readable explanation."
    for i in range(500)
) + "\n\nFULL_ONLY_END_SENTINEL"


def fixture(case, project):
    if case == "gap":
        rows = [{"id": str(i), "role": "assistant" if i % 2 else "user",
                 "content": f"Historical message {i}", "timestamp": i + 1}
                for i in range(8)]
        rows += [{"id": "current-user", "role": "user", "content": QUESTION, "timestamp": 9},
                 {"id": "current-final", "role": "assistant", "content": FINAL, "timestamp": 10}]
        return dict(initial=project(rows[:4]), final=FINAL, canonical=rows,
                    incoming={"session_id": SID, "_settlement_window": "tail_v1",
                              "_messages_offset": 8, "message_count": 10,
                              "messages": project(rows[8:], settlement=True), "tool_calls": []})
    rows = [{"id": "current-user", "role": "user", "content": QUESTION, "timestamp": 1},
            {"id": "current-final", "role": "assistant", "content": FULL, "timestamp": 2}]
    preview = project(rows, settlement=True)
    assert preview[-1]["_preview_content_truncated"]
    assert len(preview[-1]["content"]) < len(FULL) / 2
    assert FULL.startswith(preview[-1]["content"])
    scene = {"version": "activity_scene_v1", "mode": "compact_worklog",
             "identity": {"session_id": SID, "stream_id": STREAM},
             "activity_rows": [
                 {"role": "prose", "kind": "process_prose", "source_event_type": "token",
                  "local_id": f"live-prose:{STREAM}:0", "status": "completed", "text": NARRATION},
                 {"role": "tool", "kind": "tool_result", "source_event_type": "tool_complete",
                  "local_id": "fixture-tool", "status": "completed", "text": "Offline inspection complete",
                  "tool": {"id": "fixture-tool", "name": "fixture_inspect", "result": "Offline inspection complete"}},
                 {"role": "prose", "kind": "process_prose", "source_event_type": "token",
                  "local_id": f"live-prose:{STREAM}:1", "status": "running", "text": FULL}]}
    return dict(initial=project(rows[:1]), final=preview[-1]["content"], scene=scene,
                incoming={"session_id": SID, "_settlement_window": "tail_v1",
                          "_messages_offset": 0, "message_count": 2,
                          "messages": preview, "tool_calls": []})


def main():
    out = Path(os.environ.get("DUPLICATE_EVIDENCE") or tempfile.mkdtemp(prefix="duplicate-browser-"))
    out.mkdir(parents=True, exist_ok=True)
    baseline = os.environ.get("DUPLICATE_BASELINE")
    overrides = {name: subprocess.check_output(["git", "show", f"{baseline}:static/{name}"], cwd=ROOT)
                 for name in ("messages.js", "sessions.js")} if baseline else {}
    project = runpy.run_path(str(ROOT / "api/render_payload.py"))["bounded_render_messages"]
    results = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True, args=["--no-sandbox"])
        for case in ("gap", "clipped"):
            data = fixture(case, project)
            for width, height in ((1440, 900), (522, 1232)):
                context = browser.new_context(viewport={"width": width, "height": height},
                                              service_workers="block", reduced_motion="reduce")
                page = context.new_page()
                errors, console_errors, gaps, writes, unexpected = [], [], [], [], []
                page.on("pageerror", lambda e, errors=errors: errors.append(str(e)))
                page.on("console", lambda m, console_errors=console_errors: console_errors.append(m.text) if m.type == "error" else None)

                def route(route, request, *, case=case, data=data, gaps=gaps, writes=writes, unexpected=unexpected):
                    url = urlsplit(request.url)
                    path = url.path
                    if url.hostname != "duplicate.test":
                        unexpected.append(request.url)
                        route.fulfill(body="", content_type="text/plain")
                    elif path == "/":
                        html = (ROOT / "static/index.html").read_text().replace("__MAX_UPLOAD_BYTES__", "1048576").replace("__CSRF_TOKEN_JSON__", "null")
                        html = re.sub(r'<script[^>]*src="static/boot.js[^>]*></script>', '', html)
                        html = re.sub(r'<script[^>]*src="https://[^>]*></script>', '', html)
                        html = re.sub(r'<link[^>]*href="https://[^>]*>', '', html)
                        route.fulfill(body=html, content_type="text/html")
                    elif path.startswith("/static/") and (ROOT / path.lstrip("/")).is_file():
                        file = ROOT / path.lstrip("/")
                        route.fulfill(body=overrides.get(file.name, file.read_bytes()),
                                      content_type=mimetypes.guess_type(file)[0] or "application/octet-stream")
                    elif path == "/api/chat/stream":
                        events = []
                        if case == "clipped":
                            events = [("token", {"text": NARRATION}),
                                      ("tool", {"id": "fixture-tool", "name": "fixture_inspect", "args": {}}),
                                      ("tool_complete", {"id": "fixture-tool", "name": "fixture_inspect", "result": "Offline inspection complete"}),
                                      ("token", {"text": FULL})]
                        events += [("done", {"status": "completed", "session": data["incoming"]}), ("stream_end", {})]
                        route.fulfill(body="".join(f"event: {name}\ndata: {json.dumps(payload)}\n\n" for name, payload in events),
                                      content_type="text/event-stream")
                    elif path == "/api/session" and "msg_before" in parse_qs(url.query):
                        before = int(parse_qs(url.query)["msg_before"][0])
                        gaps.append(before)
                        # Reconciled page moved the final pair into old raw slots.
                        # Offsets/lengths still fit, but its right boundary is absent.
                        if case == "gap" and before == 8:
                            session = {"session_id": SID, "_messages_offset": 6,
                                       "messages": project(data["canonical"][8:]), "_messages_boundary": None, "tool_calls": []}
                        elif case == "gap" and before == 6:
                            session = {"session_id": SID, "_messages_offset": 2,
                                       "messages": project(data["canonical"][2:6]),
                                       "_messages_boundary": project(data["canonical"][8:9])[0], "tool_calls": []}
                        else:
                            unexpected.append(request.url)
                            session = {}
                        route.fulfill(json={"session": session})
                    elif "text/event-stream" in request.headers.get("accept", ""):
                        route.fulfill(status=204, body="")
                    elif path == "/api/session/anchor-scene":
                        writes.append(request.post_data_json)
                        route.fulfill(json={"ok": True})
                    elif path.startswith(("/api/", "/v1/")):
                        route.fulfill(json={"sessions": [], "files": [], "models": [], "ok": True})
                    else:
                        route.fulfill(body="", content_type="text/javascript")

                page.route("**/*", route)
                result = {"case": case, "width": width, "height": height, "failures": []}
                try:
                    page.goto("http://duplicate.test/", wait_until="load")
                    page.evaluate("""({data, sid, stream, caseName, full}) => {
                        window._virtualizeTranscript=true;
                        window._chatActivityDisplayMode='compact_worklog';
                        S.session={session_id:sid,message_count:data.initial.length};
                        S.messages=structuredClone(data.initial); S.toolCalls=[];
                        S.busy=true; S.activeStreamId=stream;
                        window.fixtureTokens=[]; window.fixtureNative=false;
                        if(caseName==='clipped'){
                            const realAnchors=window.HermesAssistantTurnAnchors;
                            window.HermesAssistantTurnAnchors={...realAnchors,
                                projectAssistantTurnAnchorActivityScene:(...args)=>window.fixtureAtDone
                                    ? structuredClone(data.scene)
                                    : realAnchors.projectAssistantTurnAnchorActivityScene(...args)};
                        }
                        const NativeEventSource=window.EventSource;
                        // Native transport, unchanged event delivery. This observer runs
                        // before the production done listener, supplying its audited input.
                        window.EventSource=class extends NativeEventSource {
                            constructor(url, opts){
                                super(url,opts); window.fixtureNative=this instanceof NativeEventSource;
                                this.addEventListener('token',e=>window.fixtureTokens.push(JSON.parse(e.data).text));
                                if(caseName==='clipped') this.addEventListener('done',()=>{
                                    window.fixtureFullProcessed=String(INFLIGHT[sid]?.lastAssistantText||'').includes(full);
                                    window.fixtureBeforeDone=S.messages.map(m=>({role:m.role,chars:String(m.content||'').length}));
                                    S.messages=structuredClone(data.initial);
                                    window.fixtureAtDone=true;
                                });
                            }
                        };
                        renderMessages(); scrollToBottom(true);
                        attachLiveStream(sid,stream);
                    }""", {"data": data, "sid": SID, "stream": STREAM, "caseName": case, "full": FULL})
                    page.wait_for_function("() => !S.busy && S.activeStreamId===null", timeout=10000)
                    page.wait_for_timeout(350)
                    result.update(page.evaluate(r"""({question, final, full, narration}) => {
                        const msg=$('msgInner'), last=S.messages.filter(m=>m.role==='assistant').at(-1);
                        const scene=last?._anchor_activity_scene;
                        const normalize=t=>String(t||'').replace(/\s+/g,' ').trim();
                        const bodies=[...msg.querySelectorAll('.msg-body')].map(n=>n.textContent.trim());
                        return {
                            nativeEventSource:window.fixtureNative,
                            offset:_oldestIdx, loaded:S.messages.length, count:S.session.message_count,
                            historyAvailable:_messagesTruncated,
                            userOccurrences:S.messages.filter(m=>m.content===question).length,
                            finalOccurrences:S.messages.filter(m=>m.content===final).length,
                            domUserOccurrences:bodies.filter(t=>t===question).length,
                            domFinalOccurrences:bodies.filter(t=>normalize(t).includes(normalize(final))).length,
                            finalRetained:last?.content===final,
                            finalInDom:bodies.some(t=>normalize(t).includes(normalize(final))),
                            documentOverflow:document.documentElement.scrollWidth-innerWidth,
                            bodyOverflow:document.body.scrollWidth-innerWidth,
                            innerWidth, visualWidth:visualViewport.width,
                            streamedFull:window.fixtureTokens.includes(full),
                            fullProcessed:window.fixtureFullProcessed||false,
                            scenePresent:!!scene,
                            sceneFinalRetained:scene?.final_answer===final,
                            duplicateFullRows:(scene?.activity_rows||[]).filter(r=>r.role==='prose'&&r.text===full).length,
                            narrationRows:(scene?.activity_rows||[]).filter(r=>r.role==='prose'&&r.text===narration).length,
                            toolRows:(scene?.activity_rows||[]).filter(r=>r.role==='tool').length,
                            beforeDone:window.fixtureBeforeDone||[],
                            sceneRows:(scene?.activity_rows||[]).map(r=>({role:r.role,local_id:r.local_id,chars:(r.text||'').length}))
                        };
                    }""", {"question": QUESTION, "final": data["final"], "full": FULL, "narration": NARRATION}))
                    # User-facing disclosure, then inspect actual settled DOM, not only scene helpers.
                    if case == "clipped":
                        summary = page.locator('#msgInner .tool-worklog-summary').first
                        summary.click(timeout=3000)
                        page.wait_for_timeout(100)
                        result.update(page.evaluate("""({narration})=>({
                            worklogDomFullCopies:[...document.querySelectorAll('#msgInner [data-anchor-row-role="prose"]')].filter(n=>n.textContent.includes('FULL_ONLY_END_SENTINEL')).length,
                            narrationInDom:$('msgInner').innerText.includes(narration),
                            worklogGroups:document.querySelectorAll('#msgInner .tool-worklog-group').length
                        })""", {"narration": NARRATION}))
                        page.screenshot(path=str(out / f"{case}-{width}-worklog.png"))
                    # Show the retained final's start, even when the preview is long.
                    target = page.locator('#msgInner .msg-body').filter(has_text=data["final"][:40]).last
                    target.scroll_into_view_if_needed(timeout=3000)
                    result["finalVisible"] = target.is_visible()
                    page.screenshot(path=str(out / f"{case}-{width}-settled.png"))
                    checks = {
                        "native EventSource": result["nativeEventSource"],
                        "unique current user": result["userOccurrences"] == 1 and result["domUserOccurrences"] == 1,
                        "unique final retained in DOM": result["finalOccurrences"] == 1 and result["finalRetained"] and result["finalInDom"] and result["finalVisible"],
                        "no body overflow": result["documentOverflow"] <= 2 and result["bodyOverflow"] <= 2,
                        "exact viewport": result["innerWidth"] == width and result["visualWidth"] == width,
                    }
                    if case == "gap":
                        checks.update({"coherent authoritative tail": (result["offset"], result["loaded"], result["count"]) == (8, 2, 10),
                                       "older history remains available": result["historyAvailable"],
                                       "reject incompatible first gap page": gaps == [8]})
                    else:
                        checks.update({"full answer actually streamed": result["streamedFull"] and result["fullProcessed"],
                                       "real clipped preview retained": result["scenePresent"] and result["sceneFinalRetained"],
                                       "no full answer duplicated in Worklog": result["duplicateFullRows"] == 0 and result["worklogDomFullCopies"] == 0,
                                       "pre-tool narration preserved": result["narrationRows"] == 1 and result["narrationInDom"],
                                       "tool and Worklog retained": result["toolRows"] >= 1 and result["worklogGroups"] >= 1,
                                       "scene persistence attempted offline": bool(writes)})
                    result["failures"] += [name for name, passed in checks.items() if not passed]
                    if writes:
                        (out / f"{case}-{width}-scene-payload.json").write_text(json.dumps(writes, indent=2))
                except Exception as exc:
                    result["failures"].append(f"{type(exc).__name__}: {exc}")
                    page.screenshot(path=str(out / f"{case}-{width}-failure.png"))
                finally:
                    result.update(errors=errors, consoleErrors=console_errors, gapRequests=gaps, unexpectedRequests=unexpected)
                    if errors or console_errors:
                        result["failures"].append("uncaught or console errors")
                    if unexpected:
                        result["failures"].append("unexpected request (intercepted; not sent)")
                    results.append(result)
                    context.close()
        browser.close()
    revision = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True, capture_output=True)
    report = {"baseline": baseline, "head": revision.stdout.strip() if revision.returncode == 0 else None,
              "source_sha256": {name: hashlib.sha256(overrides.get(Path(name).name, (ROOT / name).read_bytes())).hexdigest()
                                for name in ("static/messages.js", "static/sessions.js", "api/render_payload.py")},
              "artifacts": str(out), "controlledCollaborators": ["offline APIs; boot omitted", "clipped case: user-only local messages and known live projection at done"],
              "fullChars": len(FULL), "previewChars": len(fixture("clipped", project)["final"]), "results": results}
    (out / "results.json").write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))
    return int(any(r["failures"] for r in results))


if __name__ == "__main__":
    raise SystemExit(main())
