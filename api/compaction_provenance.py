"""Display-only provenance at the core compactor's replay creation boundary.

No runtime imports or blanket prefix hiding. Install on each WebUI-owned
compressor before compaction, never on its shared class. The separate read-only
legacy projection requires an exact same-UID original as well as the core envelope.
"""

from collections.abc import Mapping
from functools import wraps
from itertools import chain
import inspect
import logging
import re
from threading import Lock


_REPLAY_HEADER = (
    '[STILL IN PROGRESS — this is the active request, restated after the '
    'compaction boundary because it was not finished yet. Continue it; do not start over.]'
)
_WORKSPACE_PREFIX = re.compile(r'^\s*\[Workspace::v1:\s*(?:\\.|[^\]\\])+\]\s*')
_NOTICE_PREFIXES = (
    '\n\n[Your active task list was preserved across context compression]',
    '\n\n[Skills pruned during compression — reload before acting on these tasks]',
)


def project_compaction_replays(messages, *, witnesses=()):
    """Mark only proven legacy replay copies; preserve raw coordinates and input.

    Old cores wrote no display metadata. A matching header alone is NOT proof:
    require a nonempty logical UID shared with an unwrapped original user row
    and its exact task text (only the runtime workspace envelope may differ).
    Conflicting witnesses and unfamiliar transformations stay visible. This is
    a read-only presentation projection, never a database migration or a model
    history transformation. Multimodal replay uses explicit producer provenance.
    """
    rows = list(messages or [])
    candidates = {}
    for index, row in enumerate(rows):
        if not isinstance(row, dict) or row.get('role') != 'user':
            continue
        uid, text = row.get('message_uid'), row.get('content')
        if (row.get('display_kind') is None and row.get('display_metadata') is None
                and isinstance(uid, str) and uid and isinstance(text, str)
                and text.startswith(_REPLAY_HEADER + '\n')):
            candidates[index] = uid
    if not candidates:
        return rows
    wanted = set(candidates.values())
    originals = {}
    ambiguous = set()
    for row in chain(rows, witnesses):
        if not isinstance(row, dict) or row.get('role') != 'user':
            continue
        uid, text = row.get('message_uid'), row.get('content')
        if (not isinstance(uid, str) or uid not in wanted or is_compaction_replay(row)
                or not isinstance(text, str) or not text.strip()
                or text.startswith(_REPLAY_HEADER)):
            continue
        text = text.strip()
        if uid in originals and originals[uid] != text:
            ambiguous.add(uid)
        originals[uid] = text
    for index, uid in candidates.items():
        original = originals.get(uid)
        if not original or uid in ambiguous:
            continue
        body = rows[index]['content'][len(_REPLAY_HEADER) + 1:].strip()
        # Try literal first: a real human may have typed a workspace tag.
        variants = (body, _WORKSPACE_PREFIX.sub('', body, count=1).strip())
        if not any(text == original or (
                text.startswith(original) and text[len(original):].startswith(_NOTICE_PREFIXES)
        ) for text in variants):
            continue
        rows[index] = dict(rows[index], display_metadata={
            _REPLAY_KIND: {'version': 1, 'source_message_uid': uid},
        })
    return rows


def display_without_compaction_replays(messages, *, witnesses=()):
    """For newly settled display history only; never use for raw paging/context."""
    return [row for row in project_compaction_replays(messages, witnesses=witnesses)
            if not is_compaction_replay(row)]


_REPLAY_KIND = "webui_compaction_replay"
_WRAPPED = "_webui_compaction_replay_provenance_v1"


def is_compaction_replay(message: object) -> bool:
    """Recognize explicit v1 display provenance, not a user-authored header."""
    if not isinstance(message, Mapping):
        return False
    display = message.get("display_metadata")
    metadata = display.get(_REPLAY_KIND) if isinstance(display, dict) else None
    return (
        message.get("role") == "user"
        and not message.get("display_kind")
        and isinstance(metadata, dict)
        and type(metadata.get("version")) is int
        and metadata["version"] == 1
    )


# A fixed vocabulary and bit mask, not an instance/profile/session registry.
# No object names, exception strings, paths, UIDs or task text enter diagnostics.
_DIAGNOSTIC_REASONS = (
    "missing_hook", "read_only_hook", "signature_incompatible", "remote_capability_unknown",
)
_diagnostic_mask = 0
_diagnostic_lock = Lock()
logger = logging.getLogger(__name__)


def _diagnose_once(reason):
    global _diagnostic_mask
    bit = 1 << _DIAGNOSTIC_REASONS.index(reason)
    with _diagnostic_lock:
        if _diagnostic_mask & bit:
            return
        _diagnostic_mask |= bit
    logger.warning(
        "Compaction replay provenance: %s; producer coverage is not established; "
        "conservative read-only legacy projection remains available", reason,
    )


def prepare_agent_compaction_provenance(agent: object) -> bool:
    """Call before each local run (streaming or synchronous), including cache hits."""
    return install_compaction_replay_provenance(getattr(agent, "context_compressor", None))


def gateway_compaction_provenance_capability() -> str:
    """Remote core capability is unknown; never pretend a local hook covers it."""
    _diagnose_once("remote_capability_unknown")
    return "unknown"


def _compatible_signature(original):
    try:
        signature = inspect.signature(original)
    except (TypeError, ValueError):
        return None
    parameters = tuple(signature.parameters.values())
    if (len(parameters) < 2
            or tuple(p.name for p in parameters[:2]) != ("compressed", "inflight")
            or any(p.kind in (p.VAR_POSITIONAL, p.VAR_KEYWORD) for p in parameters[:2])
            or any(p.default is p.empty and p.kind not in (p.VAR_POSITIONAL, p.VAR_KEYWORD)
                   for p in parameters[2:])
            or inspect.iscoroutinefunction(original)
            or inspect.isgeneratorfunction(original)
            or inspect.isasyncgenfunction(original)):
        return None
    return signature


def install_compaction_replay_provenance(compressor: object) -> bool:
    """Wrap this instance's replay producer once; report hook availability.

    Supported core shape: ``_reappend_inflight_user_task(compressed, inflight)``
    returns a list with the original prefix objects in order and one fresh user
    row appended (same or newly allocated list). Unknown shapes stay untouched.
    The original call's return, exceptions, and model-facing payload are preserved.
    All row witnesses are local to a call; no session/profile state is captured.
    """
    if isinstance(compressor, type):
        _diagnose_once("signature_incompatible")
        return False
    original = getattr(compressor, "_reappend_inflight_user_task", None)
    if not callable(original):
        _diagnose_once("missing_hook")
        return False
    if getattr(original, _WRAPPED, False):
        return True
    signature = _compatible_signature(original)
    if signature is None:
        _diagnose_once("signature_incompatible")
        return False

    @wraps(original)
    def reappend_with_provenance(*args, **kwargs):
        try:
            bound = signature.bind(*args, **kwargs)
        except TypeError:
            # Delegate invalid calls too: preserve the core's own exception.
            return original(*args, **kwargs)
        bound.apply_defaults()
        compressed = bound.arguments["compressed"]
        inflight = bound.arguments["inflight"]
        # Keep references, not just ids: replacement/removal must not permit id
        # reuse to turn a previously existing row into a "fresh" replay.
        before = tuple(compressed) if isinstance(compressed, list) else None
        source_uid = inflight.get("message_uid") if isinstance(inflight, dict) else None
        result = original(*args, **kwargs)
        if (
            before is None
            or not before
            or not isinstance(inflight, dict)
            or inflight.get("role") != "user"
            or not isinstance(result, list)
            or len(result) != len(before) + 1
            or any(current is not prior for current, prior in zip(result, before, strict=False))
        ):
            return result

        replay = result[-1]
        if (
            not isinstance(replay, dict)
            or replay.get("role") != "user"
            or replay is inflight
            or any(replay is prior for prior in before)
        ):
            return result

        # A fresh copy may inherit someone else's display contract. Never
        # relabel those rows, nor mutate a shallow-copied metadata dictionary.
        if is_compaction_replay(replay):
            return result
        if replay.get("display_kind") is not None:
            return result
        metadata = replay.get("display_metadata")
        if metadata is not None and (not isinstance(metadata, dict) or metadata):
            return result

        metadata = {"version": 1}
        # Match core message_uid_or_none: non-empty string, never coerced or
        # normalized. It is a witness, not authority to classify other rows.
        if isinstance(source_uid, str) and source_uid:
            metadata["source_message_uid"] = source_uid
        # display_kind is deliberately ABSENT: the core treats any nonempty
        # kind as non-actionable input and would lose this task on the next
        # compaction. A namespaced metadata field survives storage/wire stripping
        # without changing model-context authority or the replay payload.
        replay["display_metadata"] = {_REPLAY_KIND: metadata}
        return result

    setattr(reappend_with_provenance, _WRAPPED, True)
    try:
        compressor._reappend_inflight_user_task = reappend_with_provenance
    except (AttributeError, TypeError):
        # Older/slot-only compressors can lack a writable per-instance hook.
        _diagnose_once("read_only_hook")
        return False
    return True
