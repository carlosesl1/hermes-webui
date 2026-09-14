"""Read-only rendering previews. Never use these values as model context.

Budgets count text characters (UTF-8 is at most four bytes per character).
Identity/shape metadata is preserved; only data-bearing string leaves are
shortened. Thus these are text budgets, not a hostile-JSON structural size cap.
"""

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
NOTICE = '\n\n[Content truncated in paginated preview; open the full transcript to inspect the complete content.]'
TOOL_NOTICE = '\n\n[Tool output truncated in paginated preview; open the full transcript to inspect the complete result.]'


def bounded_render_messages(messages, *, page_budget=None):
    """Copy and clip text recursively, including hydrated scenes and tool args.

    No JSON serialization of the original giant value is needed to clip it.
    Arrays/dictionaries retain their type, order, IDs and numeric coordinates.
    A shared page budget may also cover session-level legacy tool summaries.
    """
    budget = page_budget if page_budget is not None else [PAGE_CHARS]
    result = []
    for message in messages or []:
        if not isinstance(message, dict):
            result.append(message)
            continue
        remaining = [ROW_CHARS]
        clipped = [False]
        notice = TOOL_NOTICE if message.get('role') == 'tool' else NOTICE
        field_limit = 4096 if message.get('role') == 'tool' else FIELD_CHARS

        def visit(value, key='', text_payload=False, *, field_limit=field_limit,
                  remaining=remaining, clipped=clipped, notice=notice):
            if isinstance(value, str):
                if key in IDENTITY_KEYS or not text_payload:
                    return value
                limit = max(0, min(field_limit, remaining[0], budget[0]))
                kept = min(len(value), limit)
                remaining[0] -= kept
                budget[0] -= kept
                if kept == len(value):
                    return value
                clipped[0] = True
                return value[:kept] + notice
            if isinstance(value, list):
                return [visit(part, text_payload=text_payload) for part in value]
            if isinstance(value, dict):
                return {key: visit(part, key, text_payload or key in TEXT_KEYS) for key, part in value.items()}
            return value

        preview = visit(message)
        if clipped[0]:
            preview['_content_truncated'] = True
            if isinstance(message.get('content'), str):
                preview['_content_original_chars'] = len(message['content'])
        result.append(preview)
    return result
