"""Routing and durable toolset snapshot regressions (#7585/#7535/#7530)."""
import json
import sqlite3
from types import SimpleNamespace
from urllib.parse import urlparse

import pytest

import api.config as config
import api.profiles as profiles
import api.routes as routes
from tests.test_issue4490_presession_toolsets import _DummyHandler


def group(provider, model):
    return {"provider_id": provider, "models": [{"id": model}]}


@pytest.fixture
def routing(monkeypatch):
    cfg = {"model": {"provider": "nous", "default": "hermes-3"}}
    catalog = {"groups": [group("nous", "hermes-3")]}
    monkeypatch.setattr(routes, "get_available_models", lambda **kw: catalog)
    monkeypatch.setattr(routes, "_read_profile_model_config", lambda *a: (None, None, cfg))
    monkeypatch.setattr(config, "get_config", lambda: cfg)
    s = SimpleNamespace(model="hermes-3", model_provider="openrouter", profile="default")
    return s, cfg, catalog


def repair(s, cfg):
    return routes._repair_foreign_session_model_provider(
        s, requested_model=s.model, requested_provider=s.model_provider,
        resolved_model=s.model, resolved_provider=s.model_provider,
        explicit_model_pick=False, profile_provider=cfg["model"]["provider"],
        profile_config=cfg,
    )


def test_missing_builtin_group_repairs_and_display_matches_runtime(routing):
    s, cfg, _ = routing
    assert repair(s, cfg) == "nous"
    assert routes._resolve_effective_session_model_provider_for_display(s) == "nous"
    assert routes._resolve_effective_session_model_for_display(s) == "hermes-3"
    assert s.model_provider == "openrouter"  # GET is a projection, not a write.


@pytest.mark.parametrize("provider", ["custom", "custom:proxy", "ollama", "lmstudio", "vllm", "plugin-only"])
def test_absent_custom_local_plugin_preserved(routing, provider):
    s, cfg, _ = routing
    s.model_provider = provider
    assert repair(s, cfg) == provider


@pytest.mark.parametrize("kind", ["empty", "ambiguous", "incomplete", "error", "unknown-owner", "custom-endpoint"])
def test_uncertain_catalog_does_not_repair(routing, kind):
    s, cfg, catalog = routing
    if kind == "empty":
        catalog["groups"] = []
    elif kind == "ambiguous":
        catalog["groups"].append(group("anthropic", s.model))
    elif kind == "incomplete":
        catalog["incomplete"] = True
    elif kind == "error":
        catalog["groups"].append({"provider_id": "openrouter", "models_endpoint_error": "unavailable"})
    elif kind == "unknown-owner":
        catalog["groups"] = [group("plugin-only", s.model)]
    else:
        cfg["providers"] = {"openrouter": {"base_url": "http://localhost:8080/v1"}}
    assert repair(s, cfg) == "openrouter"


def test_context_uses_selected_provider_endpoint_not_global():
    cfg = {
        "model": {"provider": "openrouter", "base_url": "https://foreign.invalid/v1", "context_length": 1024},
        "providers": {"nous": {"base_url": "https://nous.invalid/v1", "models": {"hermes-3": {"context_length": 131072}}}},
    }
    display = routes._context_length_lookup_inputs_for_model("hermes-3", "nous", cfg=cfg)
    runtime = routes._context_length_lookup_inputs_for_model("hermes-3", "nous", cfg=cfg, base_url="https://nous.invalid/v1")
    for field in ("base_url", "provider", "config_context_length", "api_key", "custom_providers"):
        assert getattr(display, field) == getattr(runtime, field)
    assert display.base_url == "https://nous.invalid/v1"
    assert display.config_context_length == 131072
    cfg["providers"] = {}
    assert routes._context_length_lookup_inputs_for_model("hermes-3", "nous", cfg=cfg).base_url == ""
    assert routes._context_length_lookup_inputs_for_model("hermes-3", "nous", cfg=cfg).config_context_length is None


def test_custom_endpoint_remains_explicit():
    cfg = {"model": {"provider": "openrouter", "base_url": "https://foreign.invalid"},
           "custom_providers": [{"name": "Local", "base_url": "http://localhost:9000/v1", "models": {"m": {"context_length": 32000}}}]}
    result = routes._context_length_lookup_inputs_for_model("m", "custom:local", cfg=cfg)
    assert result.base_url == "http://localhost:9000/v1"
    assert result.config_context_length == 32000


@pytest.fixture
def tool_session(tmp_path, monkeypatch):
    homes = {name: tmp_path / name for name in ("active", "owner")}
    for home in homes.values():
        home.mkdir()
        with sqlite3.connect(home / "state.db") as conn:
            conn.execute("CREATE TABLE sessions (id TEXT PRIMARY KEY, tool_names TEXT, system_prompt_hash TEXT)")
            conn.executemany("INSERT INTO sessions VALUES (?, ?, ?)", [("s", '["terminal"]', "old"), ("other", '["terminal"]', "old")])
    monkeypatch.setattr(profiles, "_resolve_profile_home_for_name", lambda name: homes[name])
    monkeypatch.setattr(profiles, "get_active_hermes_home", lambda: homes["active"])
    monkeypatch.setattr(profiles, "_is_root_profile", lambda name: name == "default")
    s = SimpleNamespace(session_id="s", profile="owner", enabled_toolsets=["terminal"])
    saved = tmp_path / "session.json"
    def save():
        saved.write_text(json.dumps(s.enabled_toolsets))
    s.save = save
    save()
    monkeypatch.setattr(routes, "get_session", lambda sid, **kw: s)
    monkeypatch.setattr(profiles, "get_active_profile_name", lambda: "owner")
    monkeypatch.setattr(routes, "_get_active_profile_name", lambda: "owner")
    monkeypatch.setattr(profiles, "_is_isolated_profile_mode", lambda: False)
    monkeypatch.setattr(routes, "_session_is_subagent_view_only", lambda sid: False)
    return s, homes, saved


def snapshots(home, sid="s"):
    with sqlite3.connect(home / "state.db") as conn:
        return conn.execute("SELECT tool_names, system_prompt_hash FROM sessions WHERE id=?", (sid,)).fetchone()


def post_tools(value):
    h = _DummyHandler({"session_id": "s", "toolsets": value})
    routes.handle_post(h, urlparse("/api/session/toolsets"))
    return h


@pytest.mark.parametrize("value", [["web"], None, []])
def test_toolsets_invalidate_owner_before_save(tool_session, value):
    s, homes, saved = tool_session
    original = s.save
    def save():
        assert snapshots(homes["owner"]) == (None, None)
        assert routes._get_session_agent_lock("s").locked()
        original()
    s.save = save
    assert post_tools(value).status == 200
    assert json.loads(saved.read_text()) == value
    assert snapshots(homes["active"]) == ('["terminal"]', "old")
    assert snapshots(homes["owner"], "other") == ('["terminal"]', "old")


def test_db_failure_leaves_settings_unchanged_and_retry_works(tool_session):
    s, homes, saved = tool_session
    with sqlite3.connect(homes["owner"] / "state.db") as conn:
        conn.execute("CREATE TRIGGER deny_update BEFORE UPDATE ON sessions BEGIN SELECT RAISE(ABORT, 'blocked'); END")
    assert post_tools(["web"]).status == 503
    assert s.enabled_toolsets == ["terminal"]
    assert json.loads(saved.read_text()) == ["terminal"]
    assert snapshots(homes["owner"]) == ('["terminal"]', "old")
    with sqlite3.connect(homes["owner"] / "state.db") as conn:
        conn.execute("DROP TRIGGER deny_update")
    assert post_tools(["web"]).status == 200
    assert snapshots(homes["owner"]) == (None, None)


def test_save_failure_restores_memory_and_safe_retry(tool_session):
    s, homes, saved = tool_session
    original = s.save
    s.save = lambda: (_ for _ in ()).throw(OSError("write failed"))
    assert post_tools(["web"]).status == 503
    assert s.enabled_toolsets == ["terminal"]
    assert json.loads(saved.read_text()) == ["terminal"]
    assert snapshots(homes["owner"]) == (None, None)
    s.save = original
    assert post_tools(["web"]).status == 200


def test_absent_owner_db_never_invalidates_active(tool_session):
    s, homes, saved = tool_session
    (homes["owner"] / "state.db").unlink()
    assert post_tools(None).status == 200  # no persisted snapshot to invalidate
    assert snapshots(homes["active"]) == ('["terminal"]', "old")
    assert not (homes["owner"] / "state.db").exists()


@pytest.mark.parametrize("provider", ["ollama", "lmstudio", "custom", "plugin-only"])
def test_present_local_custom_plugin_inventory_is_not_negative_evidence(routing, provider):
    s, cfg, catalog = routing
    s.model_provider = provider
    catalog["groups"].append(group(provider, "some-other-model"))
    assert repair(s, cfg) == provider


def test_persisted_explicit_selection_wins_over_catalog(routing):
    from api.models import model_explicit_pick_signature
    s, cfg, _ = routing
    s.model_explicit_pick_signature = model_explicit_pick_signature(s.model, s.model_provider)
    assert repair(s, cfg) == "openrouter"


def test_missing_group_requires_literal_model_id(routing):
    s, cfg, catalog = routing
    catalog["groups"] = [group("nous", "HERMES.3")]
    assert repair(s, cfg) == "openrouter"


def test_unlabelled_global_endpoint_does_not_follow_selected_provider():
    cfg = {"model": {"base_url": "https://unrelated.invalid/v1"}}
    assert routes._context_length_lookup_inputs_for_model("hermes-3", "nous", cfg=cfg).base_url == ""


@pytest.mark.parametrize("schema", ["legacy", "missing-row", "corrupt"])
def test_owner_db_schema_and_missing_row(tool_session, schema):
    s, homes, saved = tool_session
    path = homes["owner"] / "state.db"
    if schema == "corrupt":
        path.write_bytes(b"not a database")
        assert post_tools(["web"]).status == 503
        assert json.loads(saved.read_text()) == ["terminal"]
    else:
        with sqlite3.connect(path) as conn:
            if schema == "legacy":
                conn.execute("DROP TABLE sessions")
                conn.execute("CREATE TABLE sessions (id TEXT PRIMARY KEY)")
            else:
                conn.execute("DELETE FROM sessions WHERE id='s'")
        assert post_tools(["web"]).status == 200
    assert snapshots(homes["active"]) == ('["terminal"]', "old")
