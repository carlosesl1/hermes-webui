"""Offline Chromium smoke for long-chat rendering, using the real app shell/JS.

Run with a Playwright-equipped Python and LONG_CHAT_EVIDENCE under scratch.
Optional LONG_CHAT_BASELINE=git-revision serves that revision's ui.js for RED
proof/screenshots. Only API boot, CDN assets and boot.js are excluded; the
production renderMessages, markdown, virtualizer, CSS and scroll helpers run.
No app server, real sessions, credentials, provider or outbound network needed.
"""
import json
import mimetypes
import os
import re
import subprocess
import tempfile
from pathlib import Path
from urllib.parse import urlsplit

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]


def main():
    out = Path(os.environ.get("LONG_CHAT_EVIDENCE") or tempfile.mkdtemp(prefix="long-chat-"))
    out.mkdir(parents=True, exist_ok=True)
    baseline = os.environ.get("LONG_CHAT_BASELINE")
    old_ui = subprocess.check_output(["git", "show", baseline + ":static/ui.js"], cwd=ROOT) if baseline else None
    results = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True, args=["--no-sandbox"])
        for width, height in [(1440, 900), (390, 844)]:
            context = browser.new_context(viewport={"width": width, "height": height}, service_workers="block")
            page = context.new_page()
            errors = []
            page.on("pageerror", lambda error, errors=errors: errors.append(str(error)))

            def route(request):
                path = urlsplit(request.request.url).path
                if path == "/":
                    html = (ROOT / "static/index.html").read_text()
                    html = html.replace("__MAX_UPLOAD_BYTES__", "1048576").replace("__CSRF_TOKEN_JSON__", "null")
                    html = re.sub(r'<script[^>]*src="static/boot.js[^>]*></script>', '', html)
                    html = re.sub(r'<script[^>]*src="https://[^>]*></script>', '', html)
                    html = re.sub(r'<link[^>]*href="https://[^>]*>', '', html)
                    request.fulfill(body=html, content_type="text/html")
                elif path.startswith("/static/") and (ROOT / path.lstrip("/")).is_file():
                    file = ROOT / path.lstrip("/")
                    body = old_ui if file.name == "ui.js" and old_ui is not None else file.read_bytes()
                    request.fulfill(body=body, content_type=mimetypes.guess_type(file)[0] or "application/octet-stream")
                elif path.startswith("/api/"):
                    request.fulfill(json={})
                else:
                    request.fulfill(body="", content_type="text/javascript")

            page.route("**/*", route)
            page.goto("http://long-chat.test/", wait_until="load")
            page.evaluate("""() => {
                window._virtualizeTranscript=true;
                window._chatActivityDisplayMode='compact_worklog';
                S.session={session_id:'cache-proof',tool_calls:[]};
                S.messages=Array.from({length:400},(_,i)=>({
                    role:i%2?'assistant':'user',id:'row-'+i,timestamp:i,
                    content:'Message '+i+' — '+('A readable historical message. '.repeat(12))
                }));
                const prefix='Shared answer heading. '.repeat(14);
                const suffix=' Same answer footer.'.repeat(14);
                S.messages[397].content=prefix+'ANSWER A'+suffix;
                S.messages[399].content=prefix+'ANSWER B'+suffix;
                window.beforeTranscript=JSON.stringify(S.messages);
                renderMessages();
                scrollToBottom(true);
            }""")
            page.wait_for_timeout(350)
            page.screenshot(path=str(out / f"{width}-latest.png"))
            snapshot = page.evaluate("""() => {
                const inner=$('msgInner'), pane=$('messages');
                const row=inner.querySelector('[data-msg-idx="399"]');
                const result={latestCorrect:!!row&&row.innerText.includes('ANSWER B'),
                    rows:inner.querySelectorAll('.msg-row').length,
                    loaded:S.messages.length,
                    tailDistance:pane.scrollHeight-pane.clientHeight-pane.scrollTop,
                    bodyOverflow:document.documentElement.scrollWidth-innerWidth};
                _cancelBottomSettle();
                pane.scrollTop=pane.scrollHeight*.55;
                _messageUserUnpinned=true;_scrollPinned=false;
                _scheduleMessageVirtualizedRender(true);
                return result;
            }""")
            page.wait_for_timeout(250)
            snapshot.update(page.evaluate("""() => {
                const pane=$('messages');
                const anchor=_captureMessageViewportAnchor();
                const composer=$('msg');composer.focus({preventScroll:true});
                renderMessages({preserveScroll:true});
                const restored=_captureMessageViewportAnchor();
                return {focusPreserved:document.activeElement===composer,
                    readerPreserved:!!anchor&&!!restored&&anchor.key===restored.key
                        &&Math.abs(anchor.topOffset-restored.topOffset)<=2,
                    transcriptPreserved:JSON.stringify(S.messages)===window.beforeTranscript,
                    readingOlder:_messageUserUnpinned&&!_scrollPinned};
            }"""))
            page.screenshot(path=str(out / f"{width}-reading.png"))
            snapshot.update(width=width, height=height, errors=errors)
            results.append(snapshot)
            context.close()
        browser.close()
    (out / "results.json").write_text(json.dumps(results, indent=2))
    print(json.dumps({"artifacts": str(out), "results": results}, indent=2))
    return int(any(not r["latestCorrect"] or not r["focusPreserved"]
                   or not r["readerPreserved"] or not r["transcriptPreserved"]
                   or not r["readingOlder"] or r["rows"] >= 120
                   or r["tailDistance"] > 2 or r["bodyOverflow"] > 2 or r["errors"]
                   for r in results))


if __name__ == "__main__":
    raise SystemExit(main())
