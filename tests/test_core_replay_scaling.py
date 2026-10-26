"""Upstream scaling algorithms tested against the fork's original guards."""
import copy
import random
import pytest
from api import streaming
from api.streaming import _message_replay_key, _message_text

def reference_prefix(existing_messages, candidates):
    """Drop a candidate prefix that is already the suffix of existing_messages.

    Compression/continuation can replay the active tail from state.db after the
    previous WebUI context/display already contains it. Prefix-only merge logic
    then treats that replayed tail as a fresh delta and duplicates a whole turn.
    Strip the largest exact suffix/prefix overlap before appending.
    """
    existing_messages = list(existing_messages or [])
    candidates = list(candidates or [])
    max_overlap = min(len(existing_messages), len(candidates))
    for overlap in range(max_overlap, 0, -1):
        left = [_message_replay_key(m) for m in existing_messages[-overlap:]]
        right = [_message_replay_key(m) for m in candidates[:overlap]]
        from api.models import _cross_source_replay_match
        if left == right and all(
            _cross_source_replay_match(a, b, allow_legacy=overlap >= 3)
            for a, b in zip(existing_messages[-overlap:], candidates[:overlap], strict=True)
        ):
            return candidates[overlap:]
    return candidates


def _looks_like_replayed_session_arc_summary(previous_msg, candidate_msg):
    """Return True for repeated LCM/session summaries with refreshed hints.

    LCM summary cards can be re-injected with the same long recovered context
    and a different tail such as an expand hint. Exact identity misses those,
    but appending both copies bloats every later model prompt.
    """
    if not isinstance(previous_msg, dict) or not isinstance(candidate_msg, dict):
        return False
    if previous_msg.get('role') != candidate_msg.get('role'):
        return False
    previous_api_content = previous_msg.get("api_content")
    candidate_api_content = candidate_msg.get("api_content")
    normalized_previous_api_content = (
        " ".join(previous_api_content.split())
        if isinstance(previous_api_content, str) and previous_api_content
        else None
    )
    normalized_candidate_api_content = (
        " ".join(candidate_api_content.split())
        if isinstance(candidate_api_content, str) and candidate_api_content
        else None
    )
    if normalized_previous_api_content != normalized_candidate_api_content:
        return False
    from api.models import _message_private_identity_compatible
    if not _message_private_identity_compatible(previous_msg, candidate_msg):
        return False
    previous_text = " ".join(_message_text(previous_msg.get('content', '')).split())
    candidate_text = " ".join(_message_text(candidate_msg.get('content', '')).split())
    if len(previous_text) < 2000 or len(candidate_text) < 2000:
        return False
    marker = '[Session Arc Summary'
    if not previous_text.startswith(marker) or not candidate_text.startswith(marker):
        return False
    return previous_text[:1500] == candidate_text[:1500]


def reference_context(existing_messages, candidates):
    """Drop replayed non-adjacent context blocks before persisting context."""
    existing_messages = list(existing_messages or [])
    candidates = list(candidates or [])
    if not existing_messages or not candidates:
        return candidates

    existing_keys = [_message_replay_key(m) for m in existing_messages]
    candidate_keys = [_message_replay_key(m) for m in candidates]
    existing_large = [m for m in existing_messages if isinstance(m, dict)]
    cleaned = []
    idx = 0
    from api.models import _message_private_identity_compatible
    min_block = 3
    while idx < len(candidates):
        msg = candidates[idx]
        if any(_looks_like_replayed_session_arc_summary(prev, msg) for prev in existing_large):
            idx += 1
            continue

        best = 0
        for start in range(len(existing_keys)):
            length = 0
            while (
                idx + length < len(candidate_keys)
                and start + length < len(existing_keys)
                and candidate_keys[idx + length] == existing_keys[start + length]
                and _message_private_identity_compatible(candidates[idx + length], existing_messages[start + length])
            ):
                length += 1
            if length > best:
                best = length
        if best >= min_block:
            idx += best
            continue

        cleaned.append(msg)
        idx += 1
    return cleaned

@pytest.mark.parametrize("helper,reference", [
    ("_strip_replayed_prefix", reference_prefix),
    ("_strip_replayed_context_items", reference_context),
])
def test_fork_differential_identity_and_repetition(helper, reference):
    rng = random.Random(8076)
    rows = [None, {"role": "user", "content": "again"},
            {"role": "assistant", "content": "answer"},
            {"role": "assistant", "content": [{"type": "text", "text": "answer"}]},
            {"role": "tool", "content": "ok", "tool_call_id": "call-1"},
            {"role": "tool", "content": "ok", "tool_call_id": "call-2"},
            {"role": "assistant", "content": "", "tool_calls": [{"id": "call-1"}]}]
    for _ in range(400):
        old = [copy.deepcopy(rng.choice(rows)) for _ in range(rng.randrange(20))]
        new = [copy.deepcopy(rng.choice(rows)) for _ in range(rng.randrange(20))]
        if old:
            new[:0] = copy.deepcopy(old[rng.randrange(len(old)):])
        for collection in (old, new):
            for row in collection:
                if isinstance(row, dict) and rng.random() < .4:
                    row[rng.choice(["id", "message_uid", "_row_id", "_source", "_active_turn_token", "api_content"])] = rng.choice(["a", "b", 1, 2, None])
        before = copy.deepcopy((old, new))
        result = getattr(streaming, helper)(old, new)
        assert result == reference(old, new)
        assert (old, new) == before
        assert all(any(row is candidate for candidate in new) for row in result)

@pytest.mark.parametrize("helper", ["_strip_replayed_prefix", "_strip_replayed_context_items"])
def test_key_and_normalization_cost(helper, monkeypatch):
    size = 256
    old = [{"role": "assistant", "content": f"old {i}"} for i in range(size)]
    new = [{"role": "assistant", "content": f"new {i}"} for i in range(size)]
    calls = {"_message_replay_key": 0, "_message_text": 0}
    for name in calls:
        original = getattr(streaming, name)
        def counted(value, name=name, original=original):
            calls[name] += 1
            assert calls[name] <= 8 * size
            return original(value)
        monkeypatch.setattr(streaming, name, counted)
    assert getattr(streaming, helper)(old, new) == new

@pytest.mark.parametrize("field", ["message_uid", "id", "_row_id", "_source", "_active_turn_token", "tool_use_id"])
def test_conflicting_occurrence_block_survives(field):
    old = [{"role": "user", "content": "again", field: 1} for _ in range(4)]
    new = [{"role": "user", "content": "again", field: 2} for _ in range(4)]
    assert streaming._strip_replayed_prefix(old, new) == new
    assert streaming._strip_replayed_context_items(old, new) == new

def test_partial_provider_payload_conflict_survives():
    old = [{"role": "assistant", "content": "", "_partial": True, "api_content": "wire-a"} for _ in range(3)]
    new = [{**row, "api_content": "wire-b"} for row in old]
    assert streaming._strip_replayed_context_items(old, new) == new


def test_full_content_guard_beyond_truncated_replay_key():
    old = [{"role": "assistant", "content": "x" * 600 + "old"} for _ in range(3)]
    new = [{"role": "assistant", "content": "x" * 600 + "new"} for _ in range(3)]
    assert streaming._strip_replayed_prefix(old, new) == new


def test_short_unidentified_user_repeat_survives():
    assert streaming._strip_replayed_prefix([{"role":"user","content":"again"}], [{"role":"user","content":"again"}]) == [{"role":"user","content":"again"}]
