"""Producer-boundary tests: no Hermes runtime, bootstrap, or persistent state."""

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import gc
from threading import Barrier
from types import SimpleNamespace
import weakref

import pytest

from api.compaction_provenance import (
    install_compaction_replay_provenance,
    is_compaction_replay,
)


KIND = "webui_compaction_replay"


class Compressor:
    """The supported core shape: return the same list, append one fresh row."""

    def _reappend_inflight_user_task(self, compressed, inflight):
        if not compressed or inflight is None:
            return compressed
        replay = dict(inflight)
        replay.pop("timestamp", None)
        replay["content"] = deepcopy(inflight["content"])
        compressed.append(replay)
        return compressed


def rows():
    return [
        {"role": "user", "content": "human"},
        {"role": "assistant", "content": "summary", "display_kind": "context_summary"},
    ]


@pytest.mark.parametrize("content", [
    "Please finish the task",
    [{"type": "text", "text": "task"}, {"type": "image_url", "image_url": {"url": "example"}}],
])
def test_stamp_only_fresh_replay_and_preserve_payload(content):
    compressor = Compressor()
    assert install_compaction_replay_provenance(compressor)
    original = {"role": "user", "content": content, "message_uid": "user-1", "timestamp": 42,
                "api_content": "unchanged by shim", "extra": {"a": 1}}
    before = deepcopy(original)
    compressed = rows()
    expected_prefix = deepcopy(compressed)
    result = compressor._reappend_inflight_user_task(compressed=compressed, inflight=original)
    assert result is compressed
    assert result[:-1] == expected_prefix
    assert original == before
    replay = result[-1]
    assert replay is not original
    assert is_compaction_replay(replay)
    assert replay["display_metadata"] == {KIND: {"version": 1, "source_message_uid": "user-1"}}
    assert "display_kind" not in replay
    assert {k: v for k, v in replay.items() if k not in {"display_kind", "display_metadata"}} == {
        k: v for k, v in original.items() if k != "timestamp"
    }


@pytest.mark.parametrize("uid", [None, "", 1, True, [], {}, "uid", "  literal uid  "])
def test_source_uid_uses_core_nonempty_string_contract_without_coercion(uid):
    compressor = Compressor()
    install_compaction_replay_provenance(compressor)
    replay = compressor._reappend_inflight_user_task(rows(), {
        "role": "user", "content": "task", "message_uid": uid,
    })[-1]
    expected = {"version": 1}
    if isinstance(uid, str) and uid:
        expected["source_message_uid"] = uid
    assert replay["display_metadata"] == {KIND: expected}
    assert replay["message_uid"] == uid


@pytest.mark.parametrize("message", [
    None, [], "text", {}, {"role": "user", "content": "replay-looking header"},
    *[{"role": "user", "display_metadata": {KIND: {"version": v}}}
      for v in [None, 0, 2, "1", True, 1.0]],
    {"role": "assistant", "display_kind": KIND, "display_metadata": {"version": 1}},
    {"role": "user", "display_kind": "context_summary", "display_metadata": {"version": 1}},
    {"role": "user", "display_kind": KIND},
    {"role": "user", "display_kind": KIND, "display_metadata": []},
    *[{"role": "user", "display_kind": KIND, "display_metadata": {"version": v}}
      for v in [None, 0, 2, "1", True, 1.0]],
])
def test_predicate_rejects_missing_incompatible_and_text_only_provenance(message):
    assert not is_compaction_replay(message)


@pytest.mark.parametrize("metadata", [{"version": 1}, {"version": 1, "source_message_uid": "id"}])
def test_predicate_accepts_version_one_with_optional_source(metadata):
    assert is_compaction_replay({"role": "user", "display_metadata": {KIND: metadata}})


@pytest.mark.parametrize("display", [
    {"display_kind": "human"},
    {"display_kind": "context_summary", "display_metadata": {"version": 1}},
    {"display_metadata": {"other": "owned elsewhere"}},
    {"display_kind": KIND, "display_metadata": {"version": 2}},
    {"display_kind": KIND, "display_metadata": {"version": 1, "source_message_uid": "prior", "extra": 3}},
])
def test_existing_display_fields_are_not_overwritten(display):
    compressor = Compressor()
    install_compaction_replay_provenance(compressor)
    inflight = {"role": "user", "content": "task", "message_uid": "current", **display}
    result = compressor._reappend_inflight_user_task(rows(), inflight)
    assert result[-1] == inflight
    assert result[-1].get("display_metadata") is inflight.get("display_metadata")


@pytest.mark.parametrize("mode", ["noop", "merge", "reuse", "inflight", "assistant", "multiple", "new-list", "replace"])
def test_unknown_or_nonstandalone_shapes_do_not_stamp_rows(mode):
    class DifferentCompressor:
        def _reappend_inflight_user_task(self, compressed, inflight):
            if mode == "merge":
                compressed[0]["content"] += " merged task"
            elif mode == "reuse":
                compressed.append(compressed[0])
            elif mode == "inflight":
                compressed.append(inflight)
            elif mode == "assistant":
                compressed.append({"role": "assistant", "content": "unrelated"})
            elif mode == "multiple":
                compressed.extend([dict(inflight), dict(inflight)])
            elif mode == "new-list":
                return compressed + [dict(inflight)]
            elif mode == "replace":
                compressed[0] = dict(compressed[0])
                compressed.append(dict(inflight))
            return compressed

    compressor = DifferentCompressor()
    install_compaction_replay_provenance(compressor)
    source = {"role": "user", "content": "task"}
    result = compressor._reappend_inflight_user_task(rows(), source)
    assert not any(is_compaction_replay(row) for row in result)
    assert "display_kind" not in source
    assert all("display_metadata" not in row for row in result)


def test_install_is_instance_local_and_idempotent():
    original_method = Compressor._reappend_inflight_user_task
    first, second = Compressor(), Compressor()
    assert install_compaction_replay_provenance(first)
    wrapper = first._reappend_inflight_user_task
    assert install_compaction_replay_provenance(first)
    assert first._reappend_inflight_user_task is wrapper
    assert Compressor._reappend_inflight_user_task is original_method
    inflight = {"role": "user", "content": "task"}
    assert not is_compaction_replay(second._reappend_inflight_user_task(rows(), inflight)[-1])
    assert is_compaction_replay(first._reappend_inflight_user_task(rows(), inflight)[-1])


@pytest.mark.parametrize("compressor", [None, object(), SimpleNamespace(), SimpleNamespace(_reappend_inflight_user_task=None), Compressor])
def test_missing_or_noninstance_hook_is_unsupported(compressor):
    assert not install_compaction_replay_provenance(compressor)


def test_readonly_instance_is_unsupported():
    class ReadOnly:
        __slots__ = ()

        def _reappend_inflight_user_task(self, compressed, inflight):
            return compressed

    assert not install_compaction_replay_provenance(ReadOnly())


def test_original_exception_propagates_unchanged():
    failure = RuntimeError("core failure")

    class Broken:
        def _reappend_inflight_user_task(self, compressed, inflight):
            raise failure

    compressor = Broken()
    install_compaction_replay_provenance(compressor)
    with pytest.raises(RuntimeError) as caught:
        compressor._reappend_inflight_user_task(rows(), {"role": "user", "content": "task"})
    assert caught.value is failure


def test_concurrent_instances_never_share_source_metadata():
    barrier = Barrier(2)

    class Concurrent(Compressor):
        def _reappend_inflight_user_task(self, compressed, inflight):
            barrier.wait(timeout=5)
            return super()._reappend_inflight_user_task(compressed, inflight)

    compressors = [Concurrent(), Concurrent()]
    for compressor in compressors:
        install_compaction_replay_provenance(compressor)

    def run(index):
        return compressors[index]._reappend_inflight_user_task(rows(), {
            "role": "user", "content": "same text", "message_uid": f"source-{index}",
        })[-1]

    with ThreadPoolExecutor(max_workers=2) as pool:
        first, second = list(pool.map(run, range(2)))
    assert first["display_metadata"] == {KIND: {"version": 1, "source_message_uid": "source-0"}}
    assert second["display_metadata"] == {KIND: {"version": 1, "source_message_uid": "source-1"}}
    assert first["display_metadata"] is not second["display_metadata"]


def test_wrapper_does_not_retain_per_call_rows():
    class Message(dict):
        pass

    compressor = Compressor()
    install_compaction_replay_provenance(compressor)
    source = Message(role="user", content="task", message_uid="source")
    witness = weakref.ref(source)
    result = compressor._reappend_inflight_user_task(rows(), source)
    del source, result
    gc.collect()
    assert witness() is None


def test_noop_inputs_preserve_return_identity():
    compressor = Compressor()
    install_compaction_replay_provenance(compressor)
    empty = []
    assert compressor._reappend_inflight_user_task(empty, {"role": "user", "content": "task"}) is empty
    compressed = rows()
    assert compressor._reappend_inflight_user_task(compressed, None) is compressed
