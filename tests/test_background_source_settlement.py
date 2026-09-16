"""Core result settlement must retain WebUI-owned notification provenance."""
import copy
import json
from types import SimpleNamespace

import pytest

from api.streaming import _settle_result_messages, _sanitize_messages_for_agent


@pytest.mark.parametrize("text", [
    '[IMPORTANT: Background process proc_377fd8e3c8e1 completed (exit_code=0).\nCommand: python3 -c "print(1)"\nOutput:\nQA_PROCESS_DONE_733\n]',
    '[ASYNC DELEGATION BATCH COMPLETE — deleg_1]\nResults: finished',
])
@pytest.mark.parametrize("source", ["process_wakeup", "webui"])
def test_core_echo_settlement_retains_authoritative_source(text, source, monkeypatch):
    # Actual core-compatible echo shape: no WebUI _source/token, no eager
    # checkpoint. The run's exact user index and turn id resolve ownership.
    previous = [{"role": "user", "content": "Build it", "timestamp": 1},
                {"role": "assistant", "content": "Launched", "timestamp": 2}]
    result = copy.deepcopy(previous) + [
        {"role": "user", "content": text, "timestamp": 3, "_db_persisted": True},
        {"role": "assistant", "content": "QA_PROCESS_ACK_733", "timestamp": 4, "_db_persisted": True},
    ]
    identity = {"text": text, "source": source, "timestamp": 3,
                "token": "owned-stream", "checkpoint": None,
                "current_turn_user_idx": 2, "turn_id": "core-turn",
                "agent_turn_boundary_resolved": True}
    session = SimpleNamespace(messages=[], context_messages=[], truncation_watermark=None)
    monkeypatch.setattr("api.streaming._annotate_media_snapshots_for_settled_messages", lambda *a: None)
    monkeypatch.setattr("api.streaming._compact_session_image_parts_for_persistence", lambda *a: None)
    monkeypatch.setattr("api.streaming._advance_truncation_watermark_after_commit", lambda *a: None)
    _settle_result_messages(session, previous, previous, result, text, source, identity)
    # JSON round trip is the actual sidecar wire shape; check both owners,
    # not just the pure merge helper that bypassed the broken alignment path.
    for rows in [session.messages, session.context_messages]:
        loaded = json.loads(json.dumps(rows))
        assert [(m["role"], m["content"]) for m in loaded] == [(m["role"], m["content"]) for m in result]
        assert loaded[2].get("_source") == ("process_wakeup" if source == "process_wakeup" else None)
        assert all(not m.get("_source") for m in loaded[:2])
    # Metadata stays off provider requests; a human typing the exact same
    # notification wrapper is still a normal visible human turn.
    assert all("_source" not in m for m in _sanitize_messages_for_agent(session.context_messages))
