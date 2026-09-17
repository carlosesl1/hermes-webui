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
    """Exercise the real synchronous route with an asserting agent factory."""
    from tests.test_issue6751_api_content_agent_replay import (
        test_issue6751_sync_chat_agent_receives_original_api_content_bytes as exercise,
    )
    from api import routes
    paths, runtime = homes
    original_factory = routes.require_ai_agent_class
    seen = []

    def scoped_factory():
        agent_class = original_factory()

        class ScopedAgent(agent_class):
            def __init__(self, **kwargs):
                seen.append(runtime.get_hermes_home())
                assert runtime.get_hermes_home() == paths["alpha"]
                assert os.environ["HERMES_HOME"] == str(paths["default"])
                super().__init__(**kwargs)
        return ScopedAgent

    # Map the fixture session's resolved home away from the launch home.
    monkeypatch.setattr(profiles, "get_hermes_home_for_profile", lambda name: paths["alpha"])
    monkeypatch.setattr(routes, "require_ai_agent_class", scoped_factory)
    exercise(monkeypatch, tmp_path)
    assert seen == [paths["alpha"]]
    assert runtime.get_hermes_home_override() is None
