"""Portable render/admission contracts, synthetic state and offline compressor."""
import copy
from urllib.parse import urlparse

import pytest

from api import models, routes, streaming
from api.render_payload import FIELD_CHARS, bounded_render_messages
from tests.test_issue4836_manual_compression_recovery import (
    _FakeAgent, _FakeCompressor, _FakeHandler, _install_fake_compression_runtime,
)


@pytest.fixture
def session(monkeypatch, tmp_path):
    directory = tmp_path / "sessions"
    directory.mkdir()
    monkeypatch.setattr(models, "SESSION_DIR", directory)
    monkeypatch.setattr(models, "SESSION_INDEX_FILE", directory / "_index.json")
    s = models.Session(
        session_id="portable-render", title="Synthetic", model="test-model",
        workspace=str(tmp_path),
        messages=[{"role": "user" if i % 2 == 0 else "assistant",
                   "content": "repeat", "timestamp": i + 1, "message_uid": f"uid-{i}"}
                  for i in range(6)],
    )
    monkeypatch.setattr(routes, "get_session", lambda *a, **kw: s)
    monkeypatch.setattr(routes, "ensure_agent_runtime_current", lambda: None)
    _install_fake_compression_runtime(monkeypatch, _FakeAgent)
    monkeypatch.setattr(routes, "require_ai_agent_class", lambda: _FakeAgent)
    return s


def get(s, query=""):
    handler = _FakeHandler()
    routes._handle_session_get(handler, urlparse(
        f"/api/session?session_id={s.session_id}&messages=1&resolve_model=0{query}"))
    assert handler.status == 200, handler.payload()
    return handler.payload()["session"]


def compress(s):
    handler = _FakeHandler()
    routes._handle_session_compress(handler, {"session_id": s.session_id})
    return handler.status, handler.payload()


@pytest.mark.parametrize("settlement", [False, True])
def test_opaque_metadata_is_bounded_without_identity_collisions(settlement):
    prefix = "data:" + "x" * 70000
    rows = [{"role": "user", "id": prefix + suffix, "message_uid": prefix + suffix,
             "content": "repeat", "attachments": [{"url": prefix, "filename": "a.png"}],
             "metadata": {"opaque": prefix}}
            for suffix in ("a", "b")]
    original = copy.deepcopy(rows)
    preview = bounded_render_messages(rows, settlement=settlement)
    assert len(preview) == 2
    assert [r["id"] for r in preview] == [r["id"] for r in original]
    assert [r["message_uid"] for r in preview] == [r["message_uid"] for r in original]
    assert all(len(r["attachments"][0]["url"]) < 100 for r in preview)
    assert all(len(r["metadata"]["opaque"]) < 100 for r in preview)
    assert all(r["_content_truncated"] and not r["_preview_content_truncated"] for r in preview)
    assert rows == original


def test_preview_keeps_every_raw_row_and_full_export_is_explicit(session):
    session.messages[1]["content"] = "answer " * 12000
    session.messages.insert(2, {"role": "tool", "tool_call_id": "orphan", "content": "raw tool"})
    original = copy.deepcopy(session.messages)
    legacy = get(session)
    preview = get(session, "&render_preview=1")
    full = get(session, "&content_full=1&render_preview=1&msg_limit=1&msg_before=1")
    assert legacy["messages"] == original
    assert full["messages"] == original
    assert len(preview["messages"]) == len(original)
    assert preview["_messages_offset"] == 0
    assert preview["_messages_truncated"] is False
    assert len(preview["messages"][1]["content"]) <= FIELD_CHARS
    assert "content_full=1" in preview["_full_content_url"]
    assert session.messages == original


def test_admission_rejects_before_copy_sanitize_runtime_or_tokens(session, monkeypatch):
    class NoCopy(dict):
        def __deepcopy__(self, memo):
            pytest.fail("deep copy before admission")

    session.messages[0]["content"] = "x" * (2 * 1024 * 1024 + 1)
    session.messages[0]["attachments"] = NoCopy()
    before_context = session.context_messages
    monkeypatch.setattr(streaming, "_sanitize_messages_for_api", lambda *a, **kw: pytest.fail("sanitizer before admission"))
    monkeypatch.setattr(routes, "ensure_agent_runtime_current", lambda: pytest.fail("runtime before admission"))
    status, payload = compress(session)
    assert status == 413, payload
    assert "no history was changed" in payload["error"]
    assert session.context_messages is before_context
    assert not session.path.exists()


def test_display_only_blobs_do_not_block_model_compression(session):
    session.messages[0]["attachments"] = [{"url": "x" * (3 * 1024 * 1024)}]
    original = copy.deepcopy(session.messages)
    status, payload = compress(session)
    assert status == 200, payload
    assert len(payload["session"]["messages"]) == len(original)
    assert payload["session"]["messages"][0]["attachments"][0]["url"] == ""
    assert session.messages == original
    assert len(session.context_messages) == 2
    assert "attachments" not in session.context_messages[0]
    assert models.Session.load(session.session_id).messages == original


@pytest.mark.parametrize("field", ["content", "attachments"])
def test_nested_concurrent_changes_abort_without_overwriting_canonical(session, monkeypatch, field):
    session.messages[0]["content"] = [{"type": "text", "text": "before"}]
    session.messages[0]["attachments"] = [{"url": "before"}]
    old_context = copy.deepcopy(session.context_messages)

    def mutate(self, messages, **kwargs):
        if field == "content":
            session.messages[0]["content"][0]["text"] = "concurrent"
        else:
            session.messages[0]["attachments"][0]["url"] = "concurrent"
        return [messages[0], messages[-1]]

    monkeypatch.setattr(_FakeCompressor, "compress", mutate)
    status, payload = compress(session)
    assert status == 409, payload
    assert session.context_messages == old_context
    assert not session.path.exists()
    assert "concurrent" in str(session.messages[0][field])


def test_compression_response_is_preview_not_model_context_or_row_window(session):
    for row in session.messages:
        row["content"] = "answer " * 12000
    session.tool_calls = [{"id": "call-1", "assistant_msg_idx": 1, "result": "x" * 200000}]
    original = copy.deepcopy(session.messages)
    status, payload = compress(session)
    assert status == 200, payload
    response = payload["session"]
    assert len(response["messages"]) == len(original)
    assert [r["message_uid"] for r in response["messages"]] == [r["message_uid"] for r in original]
    assert all(len(r["content"]) <= FIELD_CHARS for r in response["messages"])
    assert response["tool_calls"][0]["assistant_msg_idx"] == 1
    assert len(response["tool_calls"][0]["result"]) <= FIELD_CHARS
    assert response["_messages_truncated"] is False
    assert response["_messages_offset"] == 0
    assert "content_full=1" in response["_full_content_url"]
    assert session.messages == original
    assert session.context_messages[0]["content"] == original[0]["content"]
    assert models.Session.load(session.session_id).messages == original


@pytest.mark.parametrize("value", ["invalid", "0", "-1"])
def test_invalid_admission_configuration_falls_back_to_safe_default(session, monkeypatch, value):
    monkeypatch.setenv("HERMES_WEBUI_COMPRESS_MAX_CHARS", value)
    session.messages[0]["content"] = "x" * (2 * 1024 * 1024 + 1)
    status, payload = compress(session)
    assert status == 413, payload


def test_admission_limit_can_be_lowered(session, monkeypatch):
    monkeypatch.setenv("HERMES_WEBUI_COMPRESS_MAX_CHARS", "10")
    status, payload = compress(session)
    assert status == 413, payload


@pytest.mark.parametrize("field", ["content", "tool_calls", "reasoning_content"])
def test_nested_model_fields_count_before_sanitizing(session, monkeypatch, field):
    session.messages[1][field] = [{"nested": "x" * (2 * 1024 * 1024 + 1)}]
    monkeypatch.setattr(streaming, "_sanitize_messages_for_api",
                        lambda *a, **kw: pytest.fail("sanitizer before admission"))
    status, payload = compress(session)
    assert status == 413, payload


def test_budget_boundary_and_operator_override(monkeypatch):
    from api.render_payload import exceeds_text_budget, manual_compress_char_limit
    value = [{"nested": ["abc", "de"]}]
    assert not exceeds_text_budget(value, 5)
    assert exceeds_text_budget(value, 4)
    monkeypatch.setenv("HERMES_WEBUI_COMPRESS_MAX_CHARS", "4194304")
    assert manual_compress_char_limit() == 4194304
