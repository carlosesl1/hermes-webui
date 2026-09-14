"""Bounded, lossless coalescing of already-owned process wakeup entries."""
from __future__ import annotations

import hashlib
import json

MAX_BATCH_EVENTS = 16
MAX_BATCH_BYTES = 64 * 1024
_BATCH_HEADER = (
    '[BACKGROUND UPDATES]\n'
    'These are results of existing background tasks, not new user requests. '
    'Review them together and continue unfinished work when needed. '
    'Report a consolidated new result or actionable issue; do not acknowledge '
    'each notification or repeat an already delivered answer.\n\n'
)


def coalesce_wakeup_entries(entries: list[dict]) -> tuple[str, str, list[dict]]:
    """Retain individual payloads and FIFO overflow, with one retry identity.

    A single event remains byte-for-byte compatible with the legacy path. An
    oversized event is still delivered alone (never silently truncated). The
    byte budget applies to combining events, not to canonical tool output.
    """
    valid = [e for e in entries if isinstance(e, dict) and str(e.get('wakeup_prompt') or '').strip()]
    if not valid:
        return '', '', []
    chosen = []
    size = len(_BATCH_HEADER.encode()) + 64  # counts/header reserve
    for entry in valid:
        # This server-assigned namespace survives admission requeue unchanged.
        # Retry envelopes are FIFO barriers, not one more constituent event:
        # merging them would nest headers, exceed budgets and change identity.
        # Do not classify arbitrary tool text by a matching prompt header.
        if str(entry.get('process_id') or '').startswith('wakeup-batch-'):
            if chosen:
                break
            return str(entry['wakeup_prompt']), str(entry['process_id']), valid[1:]
        cost = len(str(entry['wakeup_prompt']).encode('utf-8')) + 2
        if chosen and (len(chosen) >= MAX_BATCH_EVENTS or size + cost > MAX_BATCH_BYTES):
            break
        chosen.append(entry)
        size += cost
    if len(chosen) == 1:
        return str(chosen[0]['wakeup_prompt']), str(chosen[0].get('process_id') or ''), valid[1:]
    from api.process_event_utils import wakeup_display_meta

    failed = 0
    for entry in chosen:
        meta = wakeup_display_meta(entry['wakeup_prompt']) or {}
        failed += int(meta.get('failure_count') or 0)
        code = meta.get('exit_code')
        if isinstance(code, int) and code != 0:
            failed += 1
    header = _BATCH_HEADER.replace('[BACKGROUND UPDATES]\n', f'[BACKGROUND UPDATES]\nEvents: {len(chosen)}; failed: {failed}\n', 1)
    prompt = header + '\n\n'.join(str(e['wakeup_prompt']) for e in chosen)
    identity = json.dumps([(str(e.get('process_id') or ''), str(e['wakeup_prompt'])) for e in chosen], ensure_ascii=False, separators=(',', ':'))
    batch_id = 'wakeup-batch-' + hashlib.sha256(identity.encode()).hexdigest()
    return prompt, batch_id, valid[len(chosen):]
