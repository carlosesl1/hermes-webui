"""Isolated regressions for #6982, #7565, #7548 and #7549."""

import json
import sqlite3
from types import SimpleNamespace
from urllib.parse import quote, urlparse

import pytest

from api import models, routes
from api.media_snapshots import annotate_media_snapshots, media_capture_allowed, snapshot_path_for_digest
from tests.test_media_message_snapshots import _FakeHandler


def commentary(path):
    return {"role": "assistant", "content": "", "codex_message_items": [
        {"type": "message", "role": "assistant", "phase": "commentary",
         "content": [{"type": "output_text", "text": f"MEDIA:{path}"}]}]}


@pytest.mark.parametrize("variant", ["valid", "outer_user", "inner_user", "type", "analysis", "final", "part", "text", "parts", "reasoning"])
def test_typed_commentary_capture_authorization_parity(tmp_path, monkeypatch, variant):
    target = tmp_path / "preview.png"
    target.write_bytes(b"original PNG fixture")
    monkeypatch.setenv("HERMES_WEBUI_MEDIA_SNAPSHOT_DIR", str(tmp_path / "snapshots"))
    message = commentary(target)
    item = message["codex_message_items"][0]
    if variant == "outer_user": message["role"] = "user"
    if variant == "inner_user": item["role"] = "user"
    if variant == "type": item["type"] = "reasoning"
    if variant in {"analysis", "final"}: item["phase"] = variant
    if variant == "part": item["content"][0]["type"] = "reasoning_text"
    if variant == "text": item["content"][0]["text"] = {"text": f"MEDIA:{target}"}
    if variant == "parts": item["content"] = f"MEDIA:{target}"
    if variant == "reasoning": message["codex_reasoning_items"] = message.pop("codex_message_items")
    monkeypatch.setattr(routes, "get_session", lambda sid: SimpleNamespace(messages=[message] if sid == "owner" else []))
    assert routes._session_media_token_allows_path("owner", target, {"image/png"}) is (variant == "valid")
    assert not routes._session_media_token_allows_path("wrong", target, {"image/png"})
    assert not routes._session_media_token_allows_path("owner", target.with_name("other.png"), {"image/png"})
    assert not routes._session_media_token_allows_path("owner", target, {"text/html"})
    assert annotate_media_snapshots([message], allowed_predicate=lambda p: True) == (variant == "valid")
    if variant == "valid":
        reopened = json.loads(json.dumps(message))
        digest = reopened["_media_snapshots"][str(target)]
        target.write_bytes(b"overwritten")
        assert annotate_media_snapshots([reopened], allowed_predicate=lambda p: True) == 0
        assert snapshot_path_for_digest(digest).read_bytes() == b"original PNG fixture"
        from api import auth
        monkeypatch.setattr(auth, "is_auth_enabled", lambda: False)
        monkeypatch.setenv("MEDIA_ALLOWED_ROOTS", str(tmp_path))
        handler = _FakeHandler()
        routes._handle_media(handler, urlparse("/api/media?session_id=owner&path=" + quote(str(target)) + "&snap=" + digest))
        assert handler.status == 200
        assert bytes(handler.body) == b"original PNG fixture"


@pytest.mark.parametrize("layout", ["webui", "webui_state"])
@pytest.mark.parametrize("owner", ["base", "active", "sibling"])
@pytest.mark.parametrize("symlink", [False, True])
def test_state_variants_deny_serve_and_capture(tmp_path, monkeypatch, layout, owner, symlink):
    from api import auth, config, profiles, workspace
    base = tmp_path / "hermes"
    active = base / "profiles" / "active"
    sibling = base / "profiles" / "sibling"
    for p in (base, active, sibling): p.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("HERMES_HOME", str(active))
    monkeypatch.setenv("MEDIA_ALLOWED_ROOTS", str(tmp_path))
    monkeypatch.setattr(profiles, "_DEFAULT_HERMES_HOME", base)
    monkeypatch.setattr(config, "STATE_DIR", active / "webui")
    root = {"base": base, "active": active, "sibling": sibling}[owner]
    target = root / layout / "sessions" / "private.png"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b"private")
    work = active / "workspace"
    work.mkdir(exist_ok=True)
    monkeypatch.setattr(workspace, "get_last_workspace", lambda: str(work))
    if symlink:
        alias = work / "linked.png"
        alias.symlink_to(target)
        target = alias
    assert routes._media_deny_reason(target)
    assert not media_capture_allowed(target)
    monkeypatch.setattr(auth, "is_auth_enabled", lambda: False)
    monkeypatch.setattr(routes, "get_session", lambda sid: SimpleNamespace(messages=[commentary(target)]))
    handler = _FakeHandler()
    routes._handle_media(handler, urlparse("/api/media?session_id=owner&path=" + quote(str(target))))
    assert handler.status == 403
    normal = work / "normal.png"
    normal.write_bytes(b"normal")
    assert routes._media_deny_reason(normal) is None
    assert media_capture_allowed(normal)


@pytest.mark.parametrize("source", ["cli", "cron", "webhook", "kanban"])
def test_db_archive_projection_and_explicit_override(tmp_path, monkeypatch, source):
    db = tmp_path / "state.db"
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TABLE sessions(id TEXT, title TEXT, model TEXT, message_count INTEGER, started_at REAL, source TEXT, archived INTEGER)")
        conn.execute("INSERT INTO sessions VALUES ('archive_row', 'Imported', 'model', 2, 123, ?, 1)", (source,))
    sd = tmp_path / "sessions"
    sd.mkdir()
    monkeypatch.setattr(models, "SESSION_DIR", sd)
    monkeypatch.setattr(models, "get_last_workspace", lambda: str(tmp_path))
    for name in ("ensure_cron_project", "ensure_webhook_project"):
        monkeypatch.setattr(models, name, lambda **kwargs: "project")
    def project():
        return models._load_cli_sessions_uncached(tmp_path, db, "owner", include_claude_code=False)
    rows = project()
    assert rows and rows[0]["archived"] is True
    sidecar = sd / "archive_row.json"
    sidecar.write_text(json.dumps({"session_id": "archive_row", "title": "Renamed", "messages": []}))
    assert project()[0]["archived"] is True
    sidecar.write_text(json.dumps({"session_id": "archive_row", "title": "Renamed", "archived": False, "messages": []}))
    assert project()[0]["archived"] is False
    sidecar.write_text(json.dumps({"session_id": "archive_row", "title": "Renamed", "archived": True, "messages": []}))
    assert project()[0]["archived"] is True


@pytest.mark.parametrize("archived", [True, False])
@pytest.mark.parametrize("owner,status", [("beta", 200), ("alpha", 404), ("../beta", 400)])
def test_archive_explicit_owner_disk_readback(tmp_path, monkeypatch, archived, owner, status):
    sd = tmp_path / "sessions"
    sd.mkdir()
    monkeypatch.setattr(models, "SESSION_DIR", sd)
    monkeypatch.setattr(models, "SESSION_INDEX_FILE", sd / "_index.json")
    monkeypatch.setattr(routes, "get_active_profile_name", lambda: "alpha")
    monkeypatch.setattr(routes, "_is_isolated_profile_mode", lambda: False)
    session = models.Session(session_id="archive_owner", profile="beta", messages=[{"role": "user", "content": "keep"}])
    session.archived = not archived
    session.save(skip_index=True)
    monkeypatch.setattr(routes, "get_session", lambda *a, **kw: session)
    monkeypatch.setattr(routes, "_check_csrf", lambda h: True)
    monkeypatch.setattr(routes, "read_body", lambda h: {"session_id": session.session_id, "profile": owner, "archived": archived})
    monkeypatch.setattr(routes, "publish_session_list_changed", lambda *a, **kw: None)
    h = _FakeHandler()
    routes.handle_post(h, SimpleNamespace(path="/api/session/archive", query=""))
    assert h.status == status
    loaded = models.Session.load(session.session_id)
    assert loaded.profile == "beta"
    assert loaded.archived is (archived if status == 200 else not archived)
    assert loaded.messages == [{"role": "user", "content": "keep"}]


def test_archive_isolated_rejects_foreign_before_lookup(monkeypatch):
    monkeypatch.setattr(routes, "_is_isolated_profile_mode", lambda: True)
    monkeypatch.setattr(routes, "get_active_profile_name", lambda: "alpha")
    monkeypatch.setattr(routes, "get_session", lambda *a, **kw: pytest.fail("foreign lookup"))
    monkeypatch.setattr(routes, "_check_csrf", lambda h: True)
    monkeypatch.setattr(routes, "read_body", lambda h: {"session_id": "foreign", "profile": "beta"})
    h = _FakeHandler()
    routes.handle_post(h, SimpleNamespace(path="/api/session/archive", query=""))
    assert h.status == 404


@pytest.mark.parametrize("duplicates,owner,active,isolated,status", [
    (1, "beta", "alpha", False, 200), (2, "beta", "alpha", False, 404),
    (1, "alpha", "alpha", False, 404), (1, None, "alpha", False, 400),
    (1, None, "beta", False, 200), (1, None, "beta", True, 200),
    (2, None, "beta", False, 404), (1, None, "alpha", True, 404),
])
def test_archive_db_only_route_owner_validation(tmp_path, monkeypatch, duplicates, owner, active, isolated, status):
    from api import profiles
    db = tmp_path / "state.db"
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TABLE sessions(id TEXT, title TEXT, model TEXT, message_count INTEGER, started_at REAL, source TEXT, archived INTEGER)")
        conn.execute("INSERT INTO sessions VALUES ('db_owner', 'Channel', 'model', 2, 123, 'telegram', 1)")
    sd = tmp_path / "sessions"
    sd.mkdir()
    monkeypatch.setattr(models, "SESSION_DIR", sd)
    monkeypatch.setattr(models, "SESSION_INDEX_FILE", sd / "_index.json")
    monkeypatch.setattr(models, "get_last_workspace", lambda: str(tmp_path))
    monkeypatch.setattr(routes, "get_last_workspace", lambda: str(tmp_path))
    monkeypatch.setattr(routes, "get_active_profile_name", lambda: active)
    monkeypatch.setattr(routes, "_get_active_profile_name", lambda: active)
    monkeypatch.setattr(routes, "_is_isolated_profile_mode", lambda: isolated)
    monkeypatch.setattr(profiles, "get_hermes_home_for_profile", lambda profile: tmp_path)
    def missing(sid):
        raise KeyError(sid)
    monkeypatch.setattr(routes, "get_session", missing)
    def rows(**kwargs):
        return models._load_cli_sessions_uncached(tmp_path, db, "beta", include_claude_code=False) * duplicates
    monkeypatch.setattr(routes, "get_cli_sessions", rows)
    monkeypatch.setattr(routes, "_check_csrf", lambda h: True)
    body = {"session_id": "db_owner", "archived": False}
    if owner is not None:
        body["profile"] = owner
    monkeypatch.setattr(routes, "read_body", lambda h: body)
    monkeypatch.setattr(routes, "publish_session_list_changed", lambda *a, **kw: None)
    h = _FakeHandler()
    routes.handle_post(h, SimpleNamespace(path="/api/session/archive", query=""))
    assert h.status == status
    sidecar = sd / "db_owner.json"
    if status == 200:
        loaded = models.Session.load("db_owner")
        assert loaded.profile == "beta"
        assert loaded.archived is False
        assert rows()[0]["archived"] is False
        with sqlite3.connect(db) as conn:
            assert conn.execute("SELECT archived FROM sessions").fetchone()[0] == 1
    else:
        assert not sidecar.exists()


@pytest.mark.parametrize("layout", ["webui", "webui_state"])
def test_symlinked_state_root_secret_denied(tmp_path, monkeypatch, layout):
    from api import profiles, workspace
    base = tmp_path / "hermes"
    external = tmp_path / "external"
    base.mkdir()
    external.mkdir()
    (base / layout).symlink_to(external, target_is_directory=True)
    secret = external / "settings.json"
    secret.write_text('{"secret": true}')
    monkeypatch.setenv("HERMES_HOME", str(base))
    monkeypatch.setenv("MEDIA_ALLOWED_ROOTS", str(tmp_path))
    monkeypatch.setattr(profiles, "_DEFAULT_HERMES_HOME", base)
    monkeypatch.setattr(workspace, "get_last_workspace", lambda: str(external))
    assert routes._media_deny_reason(secret)
    assert not media_capture_allowed(secret)


def test_archive_frontend_owner_transport_all_entrypoints():
    from pathlib import Path
    js = (Path(__file__).resolve().parents[1] / "static" / "sessions.js").read_text()
    calls = [line for line in js.splitlines() if "api('/api/session/archive'" in line]
    assert len(calls) == 3
    assert all("profile:" in line for line in calls)
