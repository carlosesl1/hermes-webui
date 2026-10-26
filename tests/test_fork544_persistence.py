"""Fork-specific persistence boundaries for upstream 544 ports."""
import json
import threading
from collections import OrderedDict

import pytest

from api import models, profiles, run_journal
from tests.test_issue8267_cold_publication_race import _isolate_session_store, _seed


@pytest.mark.parametrize("helper", ["ensure_cron_project", "ensure_webhook_project"])
def test_system_projects_share_catalog_lock(tmp_path, monkeypatch, helper):
    monkeypatch.setattr(models, "PROJECTS_FILE", tmp_path / "projects.json")
    monkeypatch.setattr(models, "_projects_migrated", True)
    monkeypatch.setattr(profiles, "get_active_profile_name", lambda: "profile-a")
    entered = threading.Event()
    completed = threading.Event()
    errors = []

    def create():
        entered.set()
        try:
            getattr(models, helper)()
        except Exception as exc:
            errors.append(exc)
        finally:
            completed.set()

    with models.PROJECTS_CATALOG_LOCK:
        worker = threading.Thread(target=create)
        worker.start()
        assert entered.wait(2)
        assert not completed.wait(0.05)
        models.save_projects([{"project_id": "user-project", "name": "User", "profile": "profile-a",
                               "model": "chosen-model", "model_provider": "chosen-provider",
                               "workspaces": ["/synthetic/workspace"]}])
    worker.join(3)
    assert not worker.is_alive() and not errors
    rows = json.loads(models.PROJECTS_FILE.read_text())
    assert len(rows) == 2
    assert rows[0]["model"] == "chosen-model"
    assert rows[0]["workspaces"] == ["/synthetic/workspace"]
    assert all(row["profile"] == "profile-a" for row in rows)


def test_path_cache_lru_is_bounded(tmp_path, monkeypatch):
    monkeypatch.setattr(run_journal, "_RUN_PATH_CACHE", OrderedDict())
    monkeypatch.setattr(run_journal, "_RUN_PATH_CACHE_MAX_ENTRIES", 2)
    for rid in ("one", "two", "three"):
        run_journal.append_run_event("session", rid, "token", {"text": rid}, session_dir=tmp_path)
        run_journal.find_run_file(rid, session_dir=tmp_path)
    assert [key[1] for key in run_journal._RUN_PATH_CACHE] == ["two", "three"]


def test_contested_cold_read_exhaustion_never_publishes(tmp_path, monkeypatch):
    session_dir = _isolate_session_store(tmp_path, monkeypatch)
    sid = "persist-repeated-write"
    _seed(session_dir, sid)
    monkeypatch.setattr(models, "_sync_sidecar_from_state_db_if_newer", lambda _: False)
    real_load = models.Session.load.__func__
    calls = []

    def load(cls, value):
        result = real_load(cls, value)
        calls.append(value)
        # A different writer invalidates every publication attempt.
        worker = threading.Thread(target=models._note_sidecar_write, args=(value,))
        worker.start()
        worker.join(2)
        assert not worker.is_alive()
        return result

    monkeypatch.setattr(models.Session, "load", classmethod(load))
    with pytest.raises(RuntimeError, match="changed repeatedly during cold load"):
        models.get_session(sid)
    assert len(calls) == models._COLD_PUBLISH_ATTEMPTS
    assert sid not in models.SESSIONS
    assert sid not in models._FULL_SESSION_RESOLVE_INFLIGHT


def test_self_heal_write_does_not_force_cold_reload(tmp_path, monkeypatch):
    session_dir = _isolate_session_store(tmp_path, monkeypatch)
    sid = "persist-own-write"
    _seed(session_dir, sid)
    monkeypatch.setattr(models, "_sync_sidecar_from_state_db_if_newer", lambda _: False)
    monkeypatch.setattr(models, "_repair_stale_pending", lambda _: False)
    real_load = models.Session.load.__func__
    calls = []

    def load(cls, value):
        result = real_load(cls, value)
        result.title = "self healed"
        result.save(touch_updated_at=False)
        calls.append(value)
        return result

    monkeypatch.setattr(models.Session, "load", classmethod(load))
    assert models.get_session(sid).title == "self healed"
    assert calls == [sid]
    models.SESSIONS.clear()
