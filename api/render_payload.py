"""Read-only rendering previews. Never use these values as model context.

Budgets count text characters (UTF-8 is at most four bytes per character).
Identity/shape metadata is preserved; only data-bearing string leaves are
shortened. Thus these are text budgets, not a hostile-JSON structural size cap.
"""

SETTLEMENT_ROWS = 30
FIELD_CHARS = 8192
ROW_CHARS = 32768
PAGE_CHARS = 262144
# These locate/group rows, content blocks, tool calls and activity scenes. A
# depleted text budget must never turn an ID, role, status or type into a preview.
IDENTITY_KEYS = frozenset({
    'id', 'role', 'type', 'name', 'tool_call_id', 'tool_use_id', 'stream_id',
    'session_id', 'anchor_id', 'turn_id', '_anchor_stream_id', 'status', 'phase',
    'kind', 'schema', 'version', 'source', 'mime_type', 'mimeType',
    'url', 'path', 'file_path', 'filename', 'timestamp', 'created_at',
    'updated_at', 'data', 'base64', 'b64_json',
})
TEXT_KEYS = frozenset({
    'content', 'text', 'thinking', 'reasoning', 'reasoning_content',
    'arguments', 'input', 'output', 'result', 'snippet', 'summary',
    'detail', 'description', 'preview', 'command',
})


def _fair_shares(demands, available):
    """Max-min allocation: satisfy short text first, split the rest fairly."""
    shares = [0] * len(demands)
    available = max(0, available)
    for left, index in enumerate(sorted(range(len(demands)), key=demands.__getitem__)):
        share = min(demands[index], available // (len(demands) - left))
        shares[index] = share
        available -= share
    return shares


def _conversation_text(message):
    """Schema-selected visible prose; never infer provenance from magic text."""
    if not isinstance(message, dict) or message.get('role') not in ('user', 'assistant'):
        return {}
    content = message.get('content')
    if isinstance(content, str):
        return {('content',): content}
    if isinstance(content, list):
        return {('content', i, 'text'): block['text']
                for i, block in enumerate(content)
                if isinstance(block, dict)
                and block.get('type') in ('text', 'input_text', 'output_text')
                and isinstance(block.get('text'), str)}
    return {}


def bounded_render_messages(messages, *, page_budget=None, settlement=False):
    """Copy render data, reserving fair shares for readable conversation first.

    Field, row and shared page budgets count retained data-bearing characters.
    Arrays/dictionaries retain order, IDs and coordinates. Clipping is metadata,
    not appended text. This projection must never replace canonical/model data.
    """
    messages = list(messages or [])
    budget = page_budget if page_budget is not None else [PAGE_CHARS]
    prose = [_conversation_text(message) for message in messages]
    demands = [min(ROW_CHARS, sum(min(len(text), FIELD_CHARS) for text in leaves.values()))
               for leaves in prose]
    row_shares = _fair_shares(demands, budget[0])
    # Reserve every row's prose before any tool/scene/reasoning can spend it.
    budget[0] -= sum(row_shares)
    result = []
    for index, message in enumerate(messages):
        if not isinstance(message, dict):
            result.append(message)
            continue
        leaves = prose[index]
        shares = _fair_shares([min(len(text), FIELD_CHARS) for text in leaves.values()], row_shares[index])
        reserved = {path: text[:share] for (path, text), share in zip(leaves.items(), shares, strict=True)}
        prose_clipped = any(len(reserved[path]) < len(text) for path, text in leaves.items())
        remaining = [ROW_CHARS - row_shares[index]]
        clipped = [prose_clipped]
        field_limit = 4096 if message.get('role') == 'tool' else FIELD_CHARS

        def visit(value, key='', text_payload=False, path=(), *, field_limit=field_limit,
                  remaining=remaining, clipped=clipped, reserved=reserved):
            if path in reserved:
                return reserved[path]
            if settlement and key in ('api_content', 'encrypted_content'):
                return None
            if isinstance(value, str):
                if settlement and len(value) > FIELD_CHARS and (
                    key in ('data', 'base64', 'b64_json') or value.startswith('data:')
                ):
                    clipped[0] = True
                    return '[Inline data available in full transcript]'
                if key in IDENTITY_KEYS or not text_payload:
                    return value
                limit = max(0, min(field_limit, remaining[0], budget[0]))
                kept = min(len(value), limit)
                remaining[0] -= kept
                budget[0] -= kept
                if kept < len(value):
                    clipped[0] = True
                return value[:kept]
            if isinstance(value, list):
                return [visit(part, text_payload=text_payload, path=path + (i,))
                        for i, part in enumerate(value)]
            if isinstance(value, dict):
                return {key: visit(part, key, text_payload or key in TEXT_KEYS, path + (key,))
                        for key, part in value.items()}
            return value

        preview = visit(message)
        if clipped[0]:
            preview['_content_truncated'] = True
            preview['_preview_content_truncated'] = prose_clipped
            if isinstance(message.get('content'), str):
                preview['_content_original_chars'] = len(message['content'])
        result.append(preview)
    return result


def bounded_settlement_session(session, *, tool_calls=None):
    """Read-only done window, without reconciling or hashing the history prefix.

    Raw-row coordinates match session pagination. Regeneration authority must
    still be obtained from the explicit full endpoint, not this preview.
    """
    from copy import copy
    from urllib.parse import urlencode
    from api.todo_state import attach_todo_state

    source = session.messages
    total = len(source)
    start = max(0, total - SETTLEMENT_ROWS)
    tail = source[start:total]
    # compact computes user counts by walking messages: use a shallow view,
    # never temporarily mutate the live Session read by other workers.
    view = copy(session)
    view.messages = tail
    raw = view.compact()
    raw.pop('user_message_count', None)
    for key in ('composer_draft', 'context_engine_state', 'gateway_routing_history',
                'compression_anchor_details', 'compression_anchor_summary'):
        raw.pop(key, None)
    raw.update(messages=tail, message_count=total, _messages_offset=start,
               _messages_truncated=start > 0, has_more=start > 0, _msg_limit_max=500,
               _settlement_window='tail_v1')
    budget = [PAGE_CHARS]
    raw['messages'] = bounded_render_messages(tail, page_budget=budget, settlement=True)
    cards = (tool_calls or [])[-SETTLEMENT_ROWS:]
    raw['tool_calls'] = bounded_render_messages([
        card for card in cards if isinstance(card, dict)
        and isinstance(card.get('assistant_msg_idx'), int)
        and start <= card['assistant_msg_idx'] < total
    ], page_budget=budget, settlement=True)
    attach_todo_state(raw, raw['messages'])
    full_url = '/api/session?' + urlencode({
        'session_id': session.session_id, 'messages': 1, 'resolve_model': 0,
    })
    raw['_full_content_url'] = full_url
    for row in raw['messages'] + raw['tool_calls']:
        if isinstance(row, dict) and row.get('_content_truncated'):
            row['_full_content_url'] = full_url
    return raw
