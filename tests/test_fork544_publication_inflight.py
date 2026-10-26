"""In-process sidecar publication: barriers exercise real disposable persistence."""
import json
import threading
from pathlib import Path

import pytest

from api import models, routes, session_recovery


@pytest.fixture
def store(monkeypatch, tmp_path):
    sessions = tmp_path / "sessions"
    sessions.mkdir()
    for mod in (models, routes):
        monkeypatch.setattr(mod, "SESSION_DIR", sessions)
        monkeypatch.setattr(mod, "SESSION_INDEX_FILE", sessions / "_index.json")
    monkeypatch.setattr(models, "_sync_sidecar_from_state_db_if_newer", lambda s: False)
    monkeypatch.setattr(models, "_repair_stale_pending", lambda s: False)
    monkeypatch.setattr(models, "_session_has_pending_journal_retry", lambda s: False)
    models.SESSIONS.clear()
    yield sessions
    models.SESSIONS.clear()


def seed(store, sid="publication-inflight"):
    session = models.Session(session_id=sid, title="old title", workspace=str(store.parent),
                             messages=[{"role": "user", "content": "hi"}])
    session.project_id = "deleted-project"
    session.save(skip_index=True)
    return session


def disk(store, sid):
    return json.loads((store / f"{sid}.json").read_text())


class PausedWrites:
    def __init__(self, monkeypatch, store, sid, failures=()):
        self.entered = {name: threading.Event() for name in ("writer-a", "writer-b")}
        self.resume = {name: threading.Event() for name in self.entered}
        self.errors = {}
        self.threads = []
        replace = models._safe_replace
        path_replace = Path.replace

        def pause():
            name = threading.current_thread().name
            if name in self.entered:
                self.entered[name].set()
                assert self.resume[name].wait(5), "writer was never released"
                if name in failures:
                    raise OSError("injected replace failure")

        def safe_replace(src, dst):
            if dst == store / f"{sid}.json":
                pause()
            return replace(src, dst)

        def recovery_replace(src, dst):
            if dst == store / f"{sid}.json":
                pause()
            return path_replace(src, dst)

        monkeypatch.setattr(models, "_safe_replace", safe_replace)
        monkeypatch.setattr(Path, "replace", recovery_replace)

    def start(self, name, operation):
        def run():
            try:
                operation()
            except BaseException as exc:
                self.errors[name] = exc
        thread = threading.Thread(target=run, name=name)
        self.threads.append(thread)
        thread.start()
        assert self.entered[name].wait(5), "writer did not reach replace"

    def finish(self, name):
        self.resume[name].set()
        thread = next(t for t in self.threads if t.name == name)
        thread.join(5)
        assert not thread.is_alive()

    def close(self):
        for event in self.resume.values():
            event.set()
        for thread in self.threads:
            thread.join(5)
            assert not thread.is_alive()


def assert_read_refused(sid):
    # Writer is deliberately held until this call returns. Waiting for the
    # writer would deadlock; bounded refusal must not publish any old object.
    with pytest.raises(RuntimeError, match="Session changed repeatedly during cold load"):
        models.get_session(sid)
    assert sid not in models.SESSIONS
    assert sid not in models._FULL_SESSION_RESOLVE_INFLIGHT


@pytest.mark.parametrize("overlap", [False, True])
@pytest.mark.parametrize("fail_first", [False, True])
def test_cold_read_entirely_inside_inflight_replace(store, monkeypatch, overlap, fail_first):
    initial = seed(store)
    sid = initial.session_id
    writer = models.Session.load(sid)
    writer.title = "first title"
    barriers = PausedWrites(monkeypatch, store, sid, {"writer-a"} if fail_first else ())
    try:
        barriers.start("writer-a", lambda: writer.save(skip_index=True))
        if overlap:
            # Session.save serializes peer saves. Raw recovery is a distinct
            # real writer and can overlap it without weakening the save lock.
            path = store / f"{sid}.json"
            recovered = disk(store, sid)
            recovered["title"] = "new title"
            recovered["messages"].append({"role": "assistant", "content": "recovered"})
            path.with_suffix(".json.bak").write_text(json.dumps(recovered))
            barriers.start("writer-b", lambda: session_recovery.recover_session(path))
        assert_read_refused(sid)
        # Another session is not blocked by the active writer(s).
        other = seed(store, "unrelated")
        assert models.get_session(other.session_id).title == "old title"
        barriers.finish("writer-a")
        if overlap:
            # A completed or failed writer must not clear the OTHER writer's guard.
            assert_read_refused(sid)
            barriers.finish("writer-b")
    finally:
        barriers.close()
    assert set(barriers.errors) == ({"writer-a"} if fail_first else set())
    expected = "new title" if overlap else ("old title" if fail_first else "first title")
    published = models.get_session(sid)
    assert published.title == disk(store, sid)["title"] == expected
    assert models.SESSIONS[sid] is published
    published.archived = True
    published.save(skip_index=True)
    assert disk(store, sid)["title"] == expected
    assert disk(store, sid)["archived"] is True


@pytest.mark.parametrize("fail", [False, True])
def test_recovery_replace_uses_the_same_publication_guard(store, monkeypatch, fail):
    initial = seed(store)
    sid = initial.session_id
    path = store / f"{sid}.json"
    recovered = disk(store, sid)
    recovered["title"] = "recovered title"
    recovered["messages"].append({"role": "assistant", "content": "recovered reply"})
    path.with_suffix(".json.bak").write_text(json.dumps(recovered))
    barriers = PausedWrites(monkeypatch, store, sid, {"writer-a"} if fail else ())
    result = {}
    try:
        barriers.start("writer-a", lambda: result.update(session_recovery.recover_session(path)))
        assert_read_refused(sid)
        barriers.finish("writer-a")
    finally:
        barriers.close()
    assert not barriers.errors
    assert result["restored"] is not fail
    published = models.get_session(sid)
    assert published.title == ("old title" if fail else "recovered title")
    published.archived = True
    published.save(skip_index=True)
    assert disk(store, sid)["title"] == published.title
    assert len(disk(store, sid)["messages"]) == (1 if fail else 2)


@pytest.mark.parametrize("recovery", [False, True])
def test_same_thread_self_heal_can_publish_without_retry(store, monkeypatch, recovery):
    initial = seed(store)
    sid = initial.session_id
    real_load = models.Session.load.__func__
    loads = []

    def load(cls, requested_sid):
        loads.append(requested_sid)
        session = real_load(cls, requested_sid)
        if recovery:
            path = store / f"{sid}.json"
            healed = disk(store, sid)
            healed["title"] = "healed title"
            healed["messages"].append({"role": "assistant", "content": "healed reply"})
            path.with_suffix(".json.bak").write_text(json.dumps(healed))
            assert session_recovery.recover_session(path)["restored"]
            session = real_load(cls, requested_sid)
        else:
            session.title = "healed title"
            session.save(skip_index=True)
        return session

    monkeypatch.setattr(models.Session, "load", classmethod(load))
    published = models.get_session(sid)
    assert published.title == "healed title"
    assert models.SESSIONS[sid] is published
    assert loads == [sid]


def test_publication_holds_writer_guard_until_cache_insert(store, monkeypatch):
    """A writer must not start between final validation and cache insertion."""
    import collections

    initial = seed(store)
    sid = initial.session_id
    attempts = []

    class PublicationProbe(collections.OrderedDict):
        def __setitem__(self, key, value):
            if key == sid:
                def try_writer_begin():
                    guard = models._SIDECAR_WRITE_GENERATIONS_LOCK
                    acquired = guard.acquire(blocking=False)
                    attempts.append(acquired)
                    if acquired:
                        guard.release()
                writer = threading.Thread(target=try_writer_begin)
                writer.start()
                writer.join(5)
                assert not writer.is_alive()
            super().__setitem__(key, value)

    cache = PublicationProbe()
    monkeypatch.setattr(models, "SESSIONS", cache)
    published = models.get_session(sid)
    assert cache[sid] is published
    assert attempts == [False], "writer begin slipped between validation and publication"


@pytest.mark.parametrize("newer_resident", [False, True])
def test_delete_target_preserves_concurrently_published_object(store, monkeypatch, newer_resident):
    initial = seed(store)
    sid = initial.session_id
    real_load = models.Session.load.__func__
    held = {}
    loading = False

    def load(cls, requested_sid):
        nonlocal loading
        older = real_load(cls, requested_sid)
        if not loading:
            loading = True
            # Real cold resolution publishes during the delete helper's I/O.
            held["session"] = models.get_session(sid)
            if newer_resident:
                held["session"].title = "new resident title"
                held["session"].composer_draft = {"text": "keep this"}
        return older

    monkeypatch.setattr(models.Session, "load", classmethod(load))
    with routes._get_session_agent_lock(sid):
        target = routes._delete_target_session(sid, set())
        assert target is held["session"] is models.SESSIONS[sid]
        if newer_resident:
            assert target.title == "new resident title"
            assert target.composer_draft == {"text": "keep this"}
        target.project_id = None
        target.save(skip_index=True)
    # A draft waiter captured the canonical object before acquiring agent lock.
    with routes._get_session_agent_lock(sid):
        held["session"].composer_draft = {"text": "new draft"}
        held["session"].save(skip_index=True)
    persisted = disk(store, sid)
    assert persisted["project_id"] is None
    assert persisted["composer_draft"] == {"text": "new draft"}
    assert persisted["title"] == ("new resident title" if newer_resident else "old title")
