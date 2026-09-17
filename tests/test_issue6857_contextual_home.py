"""Worker home routing must never publish a profile through process env."""
import asyncio
import os
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest

from api import profiles


@pytest.fixture
def homes(monkeypatch, tmp_path):
    from api import config  # establish the agent import path
    import hermes_constants
    base = tmp_path / "root"
    base.mkdir()
    paths = {"default": base}
    for name in ("alpha", "beta"):
        paths[name] = base / "profiles" / name
        paths[name].mkdir(parents=True)
        (paths[name] / "config.yaml").write_text(f"model:\n  default: {name}\n")
    monkeypatch.setenv("HERMES_HOME", str(base))
    monkeypatch.setattr(profiles, "get_hermes_home_for_profile", paths.__getitem__)
    monkeypatch.setattr(profiles, "get_profile_runtime_env", lambda home: {})
    monkeypatch.setattr(profiles, "_profile_secret_env_names", lambda home: set())
    monkeypatch.setattr(profiles, "_hermes_home_override_available", None)
    assert not getattr(config._thread_ctx, "env", {})
    return paths, hermes_constants


def test_parallel_workers_do_not_publish_home(homes, monkeypatch):
    paths, runtime = homes
    barrier = Barrier(2)
    writes = []
    original_set = type(os.environ).__setitem__
    original_del = type(os.environ).__delitem__

    def record_set(env, key, value):
        if key == "HERMES_HOME":
            writes.append((key, value))
        return original_set(env, key, value)

    def record_del(env, key):
        if key == "HERMES_HOME":
            writes.append((key, None))
        return original_del(env, key)

    monkeypatch.setattr(type(os.environ), "__setitem__", record_set)
    monkeypatch.setattr(type(os.environ), "__delitem__", record_del)

    def worker(name):
        with profiles.profile_env_for_background_worker(name, scope_skill_modules=False):
            barrier.wait(timeout=5)
            assert runtime.get_hermes_home() == paths[name]
            assert os.environ["HERMES_HOME"] == str(paths["default"])
            barrier.wait(timeout=5)
        assert runtime.get_hermes_home_override() is None

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(worker, name) for name in ("alpha", "beta")]
        for future in futures:
            future.result(timeout=10)
    assert writes == []


@pytest.mark.parametrize("error", [RuntimeError, asyncio.CancelledError])
def test_nested_default_exception_and_cancel_restore(homes, error):
    paths, runtime = homes
    with profiles.profile_env_for_background_worker("alpha", scope_skill_modules=False):
        with pytest.raises(error):
            with profiles.profile_env_for_background_worker("beta", scope_skill_modules=False):
                assert runtime.get_hermes_home() == paths["beta"]
                raise error()
        assert runtime.get_hermes_home() == paths["alpha"]
        with profiles.profile_env_for_background_worker("default", scope_skill_modules=False):
            assert runtime.get_hermes_home() == paths["default"]
        assert runtime.get_hermes_home() == paths["alpha"]
    assert runtime.get_hermes_home_override() is None


def test_legacy_runtime_fails_closed_cross_home(homes, monkeypatch):
    paths, _ = homes
    monkeypatch.setattr(profiles, "_resolve_hermes_home_override", lambda: None)
    with pytest.raises(RuntimeError, match="context-local"):
        with profiles.profile_env_for_background_worker("alpha", scope_skill_modules=False):
            pytest.fail("must not run under another profile")
    with profiles.profile_env_for_background_worker("default", scope_skill_modules=False):
        assert os.environ["HERMES_HOME"] == str(paths["default"])


def test_absent_runtime_still_rejects_cross_home(homes, monkeypatch):
    import sys
    paths, _ = homes
    monkeypatch.setitem(sys.modules, "hermes_constants", None)
    monkeypatch.setattr(profiles, "_hermes_home_override_available", None)
    with pytest.raises(RuntimeError, match="context-local"):
        profiles.install_profile_home_scope(paths["alpha"])
    assert profiles.install_profile_home_scope(paths["default"]) == (None, None, False)


def test_legacy_missing_process_home_does_not_trust_webui_default(homes, monkeypatch):
    paths, _ = homes
    monkeypatch.setattr(profiles, "_resolve_hermes_home_override", lambda: None)
    monkeypatch.setattr(profiles, "_DEFAULT_HERMES_HOME", paths["alpha"])
    monkeypatch.delenv("HERMES_HOME")
    with pytest.raises(RuntimeError, match="context-local"):
        profiles.install_profile_home_scope(paths["alpha"])


def test_streaming_and_cron_use_same_scope(homes):
    from api.streaming import (
        _set_streaming_hermes_home_override, _reset_streaming_hermes_home_override,
    )
    paths, runtime = homes
    before = os.environ["HERMES_HOME"]
    scope = _set_streaming_hermes_home_override(str(paths["alpha"]))
    try:
        assert runtime.get_hermes_home() == paths["alpha"]
        with profiles.cron_profile_context_for_home(paths["beta"]):
            assert runtime.get_hermes_home() == paths["beta"]
            assert os.environ["HERMES_HOME"] == before
        assert runtime.get_hermes_home() == paths["alpha"]
    finally:
        _reset_streaming_hermes_home_override(*scope)
    assert runtime.get_hermes_home_override() is None
    assert os.environ["HERMES_HOME"] == before


def test_failed_setter_never_runs_body(homes, monkeypatch):
    _, runtime = homes
    def broken(home):
        raise RuntimeError("setter failed")
    monkeypatch.setattr(runtime, "set_hermes_home_override", broken)
    with pytest.raises(RuntimeError, match="setter failed"):
        with profiles.profile_env_for_background_worker("alpha", scope_skill_modules=False):
            pytest.fail("must not silently fall back")


@pytest.mark.parametrize("error", [RuntimeError, asyncio.CancelledError])
def test_nested_cron_scopes_restore_on_unwind(homes, monkeypatch, error):
    paths, runtime = homes
    monkeypatch.setattr(profiles, "get_active_hermes_home", lambda: paths["alpha"])
    with profiles.cron_profile_context():
        with pytest.raises(error):
            with profiles.cron_profile_context_for_home(paths["beta"]):
                assert runtime.get_hermes_home() == paths["beta"]
                raise error()
        assert runtime.get_hermes_home() == paths["alpha"]
        assert profiles._cron_profile_context_depth() == 1
    assert profiles._cron_profile_context_depth() == 0
    assert runtime.get_hermes_home_override() is None
    assert os.environ["HERMES_HOME"] == str(paths["default"])


def test_profile_env_cannot_override_authoritative_home(homes, monkeypatch):
    from api import config
    paths, runtime = homes
    monkeypatch.setattr(profiles, "get_profile_runtime_env", lambda home: {"HERMES_HOME": "wrong-home"})
    monkeypatch.setattr(profiles, "filter_runtime_env_for_gateway_parity", lambda env: env)
    monkeypatch.setattr(profiles, "_profile_secret_env_names", lambda home: {"HERMES_HOME"})
    with profiles.profile_env_for_background_worker("alpha", scope_skill_modules=False):
        assert runtime.get_hermes_home() == paths["alpha"]
        assert config._thread_ctx.env["HERMES_HOME"] == str(paths["alpha"])
        assert os.environ["HERMES_HOME"] == str(paths["default"])
    assert runtime.get_hermes_home_override() is None
    assert os.environ["HERMES_HOME"] == str(paths["default"])


def test_setup_failure_after_binding_resets_outer_token(homes, monkeypatch):
    from api import config
    paths, runtime = homes
    with profiles.profile_env_for_background_worker("alpha", scope_skill_modules=False):
        def broken(*args, **kwargs):
            raise RuntimeError("env setup failed")
        monkeypatch.setattr(profiles, "_apply_profile_env_to_process", broken)
        with pytest.raises(RuntimeError, match="env setup failed"):
            with profiles.profile_env_for_background_worker("beta", scope_skill_modules=False):
                pytest.fail("body must not run")
        assert runtime.get_hermes_home() == paths["alpha"]
        assert config._thread_ctx.env["HERMES_HOME"] == str(paths["alpha"])
    assert runtime.get_hermes_home_override() is None
    assert not getattr(config._thread_ctx, "env", {})


def test_streaming_and_cron_reject_unsupported_cross_home(homes, monkeypatch):
    from api.streaming import _set_streaming_hermes_home_override
    paths, _ = homes
    monkeypatch.setattr(profiles, "_resolve_hermes_home_override", lambda: None)
    with pytest.raises(RuntimeError, match="context-local"):
        _set_streaming_hermes_home_override(str(paths["alpha"]))
    with pytest.raises(RuntimeError, match="context-local"):
        with profiles.cron_profile_context_for_home(paths["alpha"]):
            pytest.fail("body must not run")
    assert profiles._cron_profile_context_depth() == 0
    assert os.environ["HERMES_HOME"] == str(paths["default"])


def test_sync_chat_runs_in_session_home(homes, monkeypatch, tmp_path):
    """Real synchronous route, uniquely owned fixture (no reused cached session)."""
    from types import SimpleNamespace
    import api.config as config
    import api.models as models
    from api import routes
    paths, runtime = homes
    directory = tmp_path / 'unique-session-state'
    directory.mkdir()
    monkeypatch.setattr(models, 'SESSION_DIR', directory)
    monkeypatch.setattr(models, 'SESSION_INDEX_FILE', directory/'index.json')
    monkeypatch.setattr(routes, 'SESSION_INDEX_FILE', directory/'index.json')
    monkeypatch.setattr(routes, 'get_session', models.get_session)
    monkeypatch.setattr(routes, 'title_from', models.title_from)
    monkeypatch.setattr(config, 'get_config', lambda: {'model': 'test-model', 'provider': 'test-provider'})
    monkeypatch.setattr(routes, 'get_config', config.get_config)
    monkeypatch.setattr(routes, 'resolve_trusted_workspace', lambda value: tmp_path)
    monkeypatch.setattr(routes, 'load_settings', lambda: {})
    monkeypatch.setattr(routes, '_resolve_cli_toolsets', lambda: [])
    monkeypatch.setattr(profiles, 'get_hermes_home_for_profile', lambda name: paths['alpha'])
    seen = []
    class ScopedAgent:
        def __init__(self, **kwargs):
            seen.append(runtime.get_hermes_home())
            assert runtime.get_hermes_home() == paths['alpha']
            assert os.environ['HERMES_HOME'] == str(paths['default'])
            assert os.environ['TERMINAL_CWD'] == str(tmp_path)
        def run_conversation(self, **kwargs):
            return {'messages': list(kwargs.get('conversation_history') or []) + [
                {'role': 'user', 'content': kwargs['persist_user_message']},
                {'role': 'assistant', 'content': 'ok'}], 'final_response': 'ok', 'completed': True}
    monkeypatch.setattr(routes, 'require_ai_agent_class', lambda: ScopedAgent)
    session = models.Session(session_id='unique_profile_scoped_sync', workspace=str(tmp_path),
                             model='test-model', model_provider='test-provider')
    session.save()
    class Handler:
        headers = {}
        wfile = SimpleNamespace(write=lambda data: None)
        def send_response(self, status): self.status = status
        def send_header(self, *args): pass
        def end_headers(self): pass
    handler = Handler()
    routes._handle_chat_sync(handler, {'session_id': session.session_id, 'message': 'scope test', 'workspace': str(tmp_path)})
    assert handler.status == 200
    assert seen == [paths['alpha']]
    assert runtime.get_hermes_home() == paths['default']


def test_startup_credentials_belong_only_to_launch_home(homes, monkeypatch):
    from api import config
    paths, _ = homes
    monkeypatch.setattr(profiles, "_INITIAL_HERMES_HOME", str(paths["default"]))
    monkeypatch.setattr(profiles, "_INITIAL_PROCESS_ENV", {"OPENAI_API_KEY": "synthetic-launch"})
    monkeypatch.setattr(profiles, "_profile_secret_env_names", lambda home: {"OPENAI_API_KEY"})
    monkeypatch.setenv("OPENAI_API_KEY", "synthetic-transient")
    with profiles.profile_env_for_background_worker("default", scope_skill_modules=False):
        assert config._thread_ctx.env["OPENAI_API_KEY"] == "synthetic-launch"
        assert os.environ["OPENAI_API_KEY"] == "synthetic-launch"
        with profiles.profile_env_for_background_worker("alpha", scope_skill_modules=False):
            assert "OPENAI_API_KEY" not in config._thread_ctx.env
            assert "OPENAI_API_KEY" not in os.environ
        assert os.environ["OPENAI_API_KEY"] == "synthetic-launch"
    assert os.environ["OPENAI_API_KEY"] == "synthetic-transient"


def test_authoritative_worker_workspace_wins_profile_default(homes, monkeypatch):
    from api import config
    monkeypatch.setattr(profiles, "get_profile_runtime_env", lambda home: {"TERMINAL_CWD": "/profile-default"})
    with profiles.profile_env_for_background_worker("alpha", scope_skill_modules=False,
             runtime_overrides={"TERMINAL_CWD": "/selected-workspace", "HERMES_SESSION_KEY": "s"}):
        assert config._thread_ctx.env["TERMINAL_CWD"] == "/selected-workspace"
        assert os.environ["TERMINAL_CWD"] == "/selected-workspace"
        assert config._thread_ctx.env["HERMES_SESSION_KEY"] == "s"
