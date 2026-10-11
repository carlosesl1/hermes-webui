"""Opt-in, paged search of saved WebUI transcripts; never warms the session LRU.

Limits apply to file reads as well as returned rows. Missing/oversized/changed
sources are reported as incomplete, not as proof of absence. Canonical session
loading remains the sole authority when a result is opened.
"""
import hashlib
import json
import os
import stat
import time

PAGE_SESSIONS = 24
PAGE_BYTES = 8 * 1024 * 1024
PAGE_SECONDS = 2.0


def search_saved_history(sessions, query, cursor, directory, text, preview, *,
                         active_profile, all_profiles=False):
    if len(query) > 256:
        raise ValueError("Search query is too long")
    # Continuations belong to a canonical store AND the effective request scope.
    # A last-ID from another profile would otherwise skip its earlier sessions.
    scope_data = ["saved-history-v2", str(directory.resolve()), active_profile,
                  bool(all_profiles), query]
    scope = hashlib.sha256(json.dumps(scope_data, ensure_ascii=True).encode()).hexdigest()
    after = ""
    if cursor:
        parts = cursor.split(":", 1)
        if len(parts) != 2 or parts[0] != scope:
            raise ValueError("Search cursor does not match query or scope; restart search")
        after = parts[1]
    rows = sorted((s for s in sessions if str(s.get("session_id", "")) > after),
                  key=lambda s: str(s.get("session_id", "")))
    deadline = time.monotonic() + PAGE_SECONDS
    remaining = PAGE_BYTES
    results, skipped = [], 0
    visited = 0
    last = after
    for meta in rows[:PAGE_SESSIONS]:
        if time.monotonic() >= deadline or remaining <= 0:
            break
        sid = str(meta.get("session_id", ""))
        # IDs are drawn from the already profile-filtered sidebar metadata.
        if not sid or sid in (".", "..") or "/" in sid or "\\" in sid:
            skipped += 1
            visited += 1
            last = sid
            continue
        try:
            fd = os.open(directory / (sid + ".json"), os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
            with os.fdopen(fd, "rb") as source:
                before = os.fstat(source.fileno())
                if not stat.S_ISREG(before.st_mode) or before.st_size > PAGE_BYTES:
                    skipped += 1
                    visited += 1
                    last = sid
                    continue
                if before.st_size > remaining:
                    break
                chunks = []
                while True:
                    if time.monotonic() >= deadline:
                        raise TimeoutError("Search page deadline")
                    chunk = source.read(min(65536, remaining + 1))
                    if not chunk:
                        break
                    remaining -= len(chunk)
                    if remaining < 0:
                        raise ValueError("Search byte budget exceeded")
                    chunks.append(chunk)
                after_stat = os.fstat(source.fileno())
                if (before.st_size, before.st_mtime_ns) != (after_stat.st_size, after_stat.st_mtime_ns):
                    raise ValueError("Transcript changed during search")
            data = json.loads(b"".join(chunks))
            if data.get("session_id") != sid or (data.get("profile") or "default") != (meta.get("profile") or "default"):
                raise ValueError("Transcript scope changed")
            messages = data.get("messages", [])
            for index, message in enumerate(messages):
                if time.monotonic() >= deadline:
                    raise TimeoutError("Search page deadline")
                if not isinstance(message, dict) or message.get("role") not in ("user", "assistant"):
                    continue
                body = text(message)
                if query not in body.lower():
                    continue
                item = dict(meta, match_type="content", match_preview=preview(body, query))
                item["match_location"] = {
                    "message_uid": message.get("message_uid"), "index": index,
                    "role": message["role"], "message_count": len(messages),
                    "unique_text": bool(message.get("message_uid")) or sum(
                        1 for row in messages if isinstance(row, dict)
                        and row.get("role") == message["role"] and text(row) == body
                    ) == 1,
                    "text_sha256": hashlib.sha256(body.encode("utf-8", errors="replace")).hexdigest(),
                }
                results.append(item)
                break
        except (OSError, ValueError, TypeError, AttributeError):
            skipped += 1
        visited += 1
        last = sid
    has_more = visited < len(rows)
    return {"sessions": results, "count": len(results), "scope": "saved_history",
            "scanned": visited, "skipped": skipped, "incomplete": bool(skipped or has_more),
            "next_cursor": scope + ":" + last if has_more else None,
            "limits": {"bytes": PAGE_BYTES, "sessions": PAGE_SESSIONS, "seconds": PAGE_SECONDS}}
