"""Offline compatibility probes against the actual WebUI helper, not core imports."""
from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
import inspect
import logging
from types import SimpleNamespace

import pytest

from api import compaction_provenance as provenance


def rows():
    return [{"role": "user", "content": "same"},
            {"role": "assistant", "content": "summary"}]


@pytest.mark.parametrize("fresh_list", [False, True])
def test_optional_arguments_keywords_and_original_prefix(fresh_list):
    class Core:
        def _reappend_inflight_user_task(self, compressed, inflight, suffix="", *, extra=None):
            replay = dict(inflight, content=inflight["content"] + suffix, extra=extra)
            if fresh_list:
                return compressed + [replay]
            compressed.append(replay)
            return compressed

    compressor = Core()
    signature = inspect.signature(compressor._reappend_inflight_user_task)
    assert provenance.install_compaction_replay_provenance(compressor)
    assert inspect.signature(compressor._reappend_inflight_user_task) == signature
    prefix = rows()
    witnesses = tuple(prefix)
    source = {"role": "user", "content": "same", "message_uid": "original"}
    snapshot = deepcopy(source)
    extra = object()
    result = compressor._reappend_inflight_user_task(prefix, source, "!", extra=extra)
    assert (result is not prefix) == fresh_list
    assert all(result[i] is row for i, row in enumerate(witnesses))
    assert all("display_metadata" not in row for row in witnesses)
    assert result[-1]["extra"] is extra
    assert result[-1]["content"] == "same!"
    assert result[-1] is not source and source == snapshot
    assert "display_kind" not in result[-1]
    assert provenance.is_compaction_replay(result[-1])
    assert result[-1]["display_metadata"]["webui_compaction_replay"]["source_message_uid"] == "original"
    keyword_result = compressor._reappend_inflight_user_task(compressed=rows(), inflight=source)
    assert provenance.is_compaction_replay(keyword_result[-1])


@pytest.mark.parametrize("method", [
    lambda messages, task: messages,
    lambda compressed: compressed,
    lambda *args, **kwargs: args,
    lambda compressed, inflight, required: compressed,
])
def test_unknown_signatures_are_untouched_at_installation(method):
    compressor = SimpleNamespace(_reappend_inflight_user_task=method)
    assert not provenance.install_compaction_replay_provenance(compressor)
    assert compressor._reappend_inflight_user_task is method


def test_positional_only_and_keyword_only_preserve_call_contract():
    class Core:
        def _reappend_inflight_user_task(self, compressed, /, *, inflight, option=None):
            return compressed + [dict(inflight)]
    compressor = Core()
    assert provenance.install_compaction_replay_provenance(compressor)
    result = compressor._reappend_inflight_user_task(rows(), inflight={"role": "user"})
    assert provenance.is_compaction_replay(result[-1])
    with pytest.raises(TypeError):
        compressor._reappend_inflight_user_task(compressed=rows(), inflight={"role": "user"})


def test_core_exception_with_optional_arguments_is_not_caught():
    failure = TypeError("core failure")
    class Core:
        def _reappend_inflight_user_task(self, compressed, inflight, *, option=None):
            raise failure
    compressor = Core()
    assert provenance.install_compaction_replay_provenance(compressor)
    with pytest.raises(TypeError) as caught:
        compressor._reappend_inflight_user_task(rows(), {}, option=True)
    assert caught.value is failure


@pytest.mark.parametrize("mode", ["reuse", "inflight", "copied", "reordered", "other-kind", "other-metadata"])
def test_new_list_rejects_unproven_rows(mode):
    class Core:
        def _reappend_inflight_user_task(self, compressed, inflight):
            prefix = compressed
            replay = dict(inflight)
            if mode == "reuse": replay = compressed[0]
            if mode == "inflight": replay = inflight
            if mode == "copied": prefix = deepcopy(compressed)
            if mode == "reordered": prefix = list(reversed(compressed))
            if mode == "other-kind": replay["display_kind"] = "other"
            if mode == "other-metadata": replay["display_metadata"] = {"other": 1}
            return prefix + [replay]
    compressor = Core()
    assert provenance.install_compaction_replay_provenance(compressor)
    result = compressor._reappend_inflight_user_task(rows(), {"role": "user", "content": "same"})
    assert not any(provenance.is_compaction_replay(row) for row in result)


def test_agent_helper_handles_fresh_cached_replaced_and_missing_compressor():
    class Core:
        def _reappend_inflight_user_task(self, compressed, inflight):
            return compressed + [dict(inflight)]
    original = Core._reappend_inflight_user_task
    agent = SimpleNamespace(context_compressor=Core())
    assert provenance.prepare_agent_compaction_provenance(agent)
    wrapper = agent.context_compressor._reappend_inflight_user_task
    assert provenance.prepare_agent_compaction_provenance(agent)
    assert agent.context_compressor._reappend_inflight_user_task is wrapper
    agent.context_compressor = Core()
    assert provenance.prepare_agent_compaction_provenance(agent)
    assert Core._reappend_inflight_user_task is original
    result = agent.context_compressor._reappend_inflight_user_task(rows(), {"role": "user"})
    assert provenance.is_compaction_replay(result[-1])
    assert not provenance.prepare_agent_compaction_provenance(SimpleNamespace())


def test_diagnostics_are_bounded_static_and_thread_safe(monkeypatch, caplog):
    monkeypatch.setattr(provenance, "_diagnostic_mask", 0)
    caplog.set_level(logging.WARNING, logger=provenance.__name__)
    class ReadOnly:
        __slots__ = ()
        def _reappend_inflight_user_task(self, compressed, inflight):
            return compressed
    def attempt(_):
        assert not provenance.install_compaction_replay_provenance(None)
        assert not provenance.install_compaction_replay_provenance(ReadOnly())
        assert not provenance.install_compaction_replay_provenance(
            SimpleNamespace(_reappend_inflight_user_task=lambda private_secret: None))
        assert provenance.gateway_compaction_provenance_capability() == "unknown"
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(attempt, range(40)))
    records = [r.getMessage() for r in caplog.records if r.name == provenance.__name__]
    assert len(records) == 4
    for reason in ("missing_hook", "read_only_hook", "signature_incompatible", "remote_capability_unknown"):
        assert sum(reason in record for record in records) == 1
    assert all("private_secret" not in record for record in records)
    assert type(provenance._diagnostic_mask) is int


def test_gateway_bridge_reports_unknown_without_contacting_remote(monkeypatch, caplog):
    from api import gateway_chat
    monkeypatch.setattr(provenance, "_diagnostic_mask", 0)
    caplog.set_level(logging.WARNING, logger=provenance.__name__)
    monkeypatch.setattr(gateway_chat, "peek_stream", lambda _: object())
    stop = RuntimeError("stop before any remote call or state write")
    def stop_at_registration(*args, **kwargs):
        raise stop
    monkeypatch.setattr(gateway_chat, "register_active_run", stop_at_registration)
    with pytest.raises(RuntimeError) as caught:
        gateway_chat._run_gateway_chat_streaming(
            "private-session", "private-task", "private-model", "private-workspace", "private-stream")
    assert caught.value is stop
    records = [r.getMessage() for r in caplog.records if r.name == provenance.__name__]
    assert len(records) == 1 and "remote_capability_unknown" in records[0]
    assert "private-" not in records[0]


def test_variadic_extensions_are_forwarded_without_reinterpretation():
    observed = []
    class Core:
        def _reappend_inflight_user_task(self, compressed, inflight=None, *args, **kwargs):
            observed.append((args, kwargs))
            return compressed if inflight is None else compressed + [dict(inflight)]
    compressor = Core()
    assert provenance.install_compaction_replay_provenance(compressor)
    prefix = rows()
    assert compressor._reappend_inflight_user_task(prefix) is prefix
    marker = object()
    result = compressor._reappend_inflight_user_task(prefix, {"role": "user"}, marker, option=marker)
    assert observed == [((), {}), ((marker,), {"option": marker})]
    assert provenance.is_compaction_replay(result[-1])


def test_async_and_generator_signatures_are_not_wrapped():
    async def async_producer(compressed, inflight):
        return compressed
    def generator_producer(compressed, inflight):
        yield compressed
    for producer in (async_producer, generator_producer):
        compressor = SimpleNamespace(_reappend_inflight_user_task=producer)
        assert not provenance.install_compaction_replay_provenance(compressor)
        assert compressor._reappend_inflight_user_task is producer


def test_uninspectable_signature_is_not_wrapped():
    class Callable:
        __signature__ = "invalid"
        def __call__(self, compressed, inflight):
            return compressed
    method = Callable()
    compressor = SimpleNamespace(_reappend_inflight_user_task=method)
    assert not provenance.install_compaction_replay_provenance(compressor)
    assert compressor._reappend_inflight_user_task is method
