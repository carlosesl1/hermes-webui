"""Chromium regression for a real EventSource done listener with failed history I/O.

Offline API fixtures; real app JS, renderer, CSS, and EventSource in Chromium.
No real provider, server-side lifecycle, or production state is exercised.
LONG_CHAT_BASELINE selects old messages.js for an expected RED run.
"""
import json
import mimetypes
import os
import re
import subprocess
import tempfile
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
FINAL = "FINAL ANSWER: the completed result is visible without refreshing."


def main():
    out = Path(os.environ.get("LONG_CHAT_EVIDENCE") or tempfile.mkdtemp(prefix="settlement-browser-"))
    out.mkdir(parents=True, exist_ok=True)
    baseline = os.environ.get("LONG_CHAT_BASELINE")
    old = subprocess.check_output(["git", "show", baseline + ":static/messages.js"], cwd=ROOT) if baseline else None
    rows = [{"role": "assistant" if i % 2 else "user", "content": f"Historical message {i}", "timestamp": i + 1} for i in range(100)]
    rows += [{"role": "tool", "content": f"Tool result {i}", "timestamp": 101 + i} for i in range(89)]
    rows += [{"role": "assistant", "content": FINAL, "timestamp": 190}]
    results = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True, args=["--no-sandbox"])
        for width, height in [(1440, 900), (522, 1232)]:
            context = browser.new_context(viewport={"width": width, "height": height}, service_workers="block")
            page = context.new_page()
            errors, gap_calls = [], []
            page.on("pageerror", lambda e, errors=errors: errors.append(str(e)))

            def route(route, request, gap_calls=gap_calls):
                url = urlsplit(request.url)
                path = url.path
                if path == "/":
                    html = (ROOT / "static/index.html").read_text().replace("__MAX_UPLOAD_BYTES__", "1048576").replace("__CSRF_TOKEN_JSON__", "null")
                    html = re.sub(r'<script[^>]*src="static/boot.js[^>]*></script>', '', html)
                    html = re.sub(r'<script[^>]*src="https://[^>]*></script>', '', html)
                    html = re.sub(r'<link[^>]*href="https://[^>]*>', '', html)
                    route.fulfill(body=html, content_type="text/html")
                elif path.startswith("/static/") and (ROOT / path.lstrip("/")).is_file():
                    file = ROOT / path.lstrip("/")
                    route.fulfill(body=old if old is not None and file.name == "messages.js" else file.read_bytes(), content_type=mimetypes.guess_type(file)[0] or "application/octet-stream")
                elif path == "/api/chat/stream":
                    payload = {"status": "completed", "session": {"session_id": "settlement-proof", "_settlement_window": "tail_v1", "_messages_offset": 160, "message_count": len(rows), "messages": rows[160:], "tool_calls": []}}
                    body = "event: done\ndata: " + json.dumps(payload) + "\n\nevent: stream_end\ndata: {}\n\n"
                    route.fulfill(body=body, content_type="text/event-stream")
                elif path == "/api/session" and "msg_before" in parse_qs(url.query):
                    gap_calls.append(request.url)
                    route.fulfill(status=503, json={"error": "Fixture: history temporarily unavailable"})
                elif path.startswith("/api/") or path.startswith("/v1/"):
                    route.fulfill(json={"sessions": [], "files": [], "models": [], "ok": True})
                else:
                    route.fulfill(body="", content_type="text/javascript")

            page.route("**/*", route)
            try:
                page.goto("http://settlement.test/", wait_until="load")
                page.evaluate("""rows => {
                    window._virtualizeTranscript=true;
                    window._chatActivityDisplayMode='compact_worklog';
                    S.session={session_id:'settlement-proof',message_count:100};
                    S.messages=rows;S.toolCalls=[];S.busy=true;S.activeStreamId='fixture-run';
                    renderMessages();scrollToBottom(true);
                    attachLiveStream('settlement-proof','fixture-run');
                }""", rows[:100])
                page.wait_for_function("() => !S.busy && S.activeStreamId===null", timeout=10000)
                page.wait_for_timeout(350)
                snapshot = page.evaluate("""final => ({
                    finalVisible:$('msgInner').innerText.includes(final),
                    finalPersistedInPane:S.messages.some(m=>m.content===final),
                    offset:_oldestIdx,historyAvailable:_messagesTruncated,
                    count:S.session.message_count,loaded:S.messages.length,
                    busy:S.busy,stream:S.activeStreamId,
                    overflow:document.documentElement.scrollWidth-innerWidth,
                    tailDistance:$('messages').scrollHeight-$('messages').clientHeight-$('messages').scrollTop
                })""", FINAL)
                snapshot.update(width=width, height=height, errors=errors, gapRequests=len(gap_calls))
                page.screenshot(path=str(out / f"{width}-settled.png"))
                results.append(snapshot)
            except Exception as error:
                results.append({"width": width, "error": str(error), "errors": errors})
                page.screenshot(path=str(out / f"{width}-failure.png"))
            finally:
                context.close()
        browser.close()
    (out / "results.json").write_text(json.dumps(results, indent=2))
    print(json.dumps({"artifacts": str(out), "results": results}, indent=2))
    return int(any(r.get("error") or not r.get("finalVisible") or not r.get("finalPersistedInPane") or r.get("offset") != 160 or not r.get("historyAvailable") or r.get("count") != 190 or r.get("loaded") != 30 or r.get("gapRequests") != 1 or r.get("overflow", 0) > 2 or r.get("errors") for r in results))


if __name__ == "__main__":
    raise SystemExit(main())
