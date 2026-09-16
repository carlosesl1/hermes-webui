"""Archive metadata must never serialize a cached transcript."""
import hashlib
import json
from unittest.mock import Mock

import pytest
from api.session_auto_archive import _runtime_idle as real_runtime_idle

@pytest.fixture(scope="session", autouse=True)
def test_server():
    yield

@pytest.fixture(autouse=True)
def cleanup_test_sessions():
    yield []

@pytest.fixture
def env(tmp_path, monkeypatch):
    from api import config, models, session_auto_archive as aa
    monkeypatch.setattr(config, "SESSION_DIR", tmp_path)
    monkeypatch.setattr(models, "SESSION_DIR", tmp_path)
    monkeypatch.setattr(models, "SESSION_INDEX_FILE", tmp_path / "_index.json")
    monkeypatch.setattr(config, "SESSIONS", {})
    monkeypatch.setattr(models, "SESSIONS", config.SESSIONS)
    monkeypatch.setattr(config, "load_settings", lambda: {"auto_archive_days": 1})
    monkeypatch.setattr(aa, "_runtime_idle", lambda sid: True)
    monkeypatch.setattr(aa, "_publish", Mock())
    return aa, config, models, tmp_path

def make(env, sid="native", **fields):
    aa, cfg, models, path = env
    s = models.Session(session_id=sid, title="History", workspace=str(path),
        created_at=100, updated_at=100, messages=[{"role":"user", "content":"Olá \n exact"}],
        context_messages=[{"role":"tool", "content":"private context"}],
        tool_calls=[{"result":"exact bytes"}])
    s.source_tag = "webui"
    for k,v in fields.items(): setattr(s,k,v)
    s.save(touch_updated_at=False)
    return s

def digest(path):
    raw=path.read_bytes()
    return hashlib.sha256(raw[raw.index(b'  "messages":'):]).hexdigest()

@pytest.mark.parametrize("days", [0, None, True, -1, 3651, "1", 1.5])
def test_disabled_invalid_no_files_touched(env, days, monkeypatch):
    aa,cfg,models,path=env
    s=make(env)
    before=s.path.read_bytes()
    monkeypatch.setattr(cfg,"load_settings",lambda:{"auto_archive_days":days})
    assert aa.AutoArchiveWorker().sweep(now=100000)==0
    assert s.path.read_bytes()==before

@pytest.mark.parametrize("fields", [{"pinned":True},{"active_stream_id":"busy"},
    {"pending_user_message":"queued"},{"pending_attachments":["x"]},
    {"source_tag":None},{"source_tag":"subagent"},{"source_tag":"unknown"},{"is_cli_session":True},
    {"read_only":True},{"imported":True},{"pre_compression_snapshot":True},
    {"worktree_branch":"branch"},{"updated_at":float("nan")},{"created_at":None},
    {"updated_at":13600},{"updated_at":99999}])
def test_exclusions(env,fields):
    aa,cfg,models,path=env
    s=make(env,**fields)
    before=s.path.read_bytes()
    assert aa.AutoArchiveWorker().sweep(now=100000)==0
    assert s.path.read_bytes()==before

@pytest.mark.parametrize("metadata_only",[False,True])
def test_lossless_archive_restore_cache_index_grace(env,metadata_only):
    aa,cfg,models,path=env
    s=make(env,profile="other")
    original=digest(s.path)
    cached=models.Session.load_metadata_only(s.session_id) if metadata_only else models.Session.load(s.session_id)
    if not metadata_only: cached.messages=[] # deliberately stale full cache
    cfg.SESSIONS[s.session_id]=cached
    worker=aa.AutoArchiveWorker()
    assert worker.sweep(now=100000)==1
    assert digest(s.path)==original
    assert cached.archived
    assert models.Session.load(s.session_id).archived
    assert next(r for r in json.loads((path/"_index.json").read_text()) if r["session_id"] == s.session_id)["archived"]
    result=aa.set_archive_metadata(s.session_id,False,now=100001)
    assert result is not None and not result.archived
    assert digest(s.path)==original
    restored=models.Session.load(s.session_id)
    assert restored.updated_at==100 and restored.auto_archive_restored_at==100001
    assert worker.sweep(now=100002)==0
    assert worker.sweep(now=186402)==1
    aa._publish.assert_called()

def test_busy_lock_failure_and_hostile_paths(env,monkeypatch):
    aa,cfg,models,path=env
    s=make(env)
    lock=cfg._get_session_agent_lock(s.session_id)
    with lock: assert aa.set_archive_metadata(s.session_id,True,now=100000) is None
    for sid in ['../escape','a/b','_index']:
        assert aa.set_archive_metadata(sid,True,now=100000) is None
    (path/'link.json').symlink_to(s.path)
    assert aa.set_archive_metadata('link',True,now=100000) is None
    monkeypatch.setattr(aa.os,'replace',Mock(side_effect=OSError('failed')))
    assert aa.set_archive_metadata(s.session_id,True,now=100000) is None
    assert lock.acquire(False)
    lock.release()
    assert not list(path.glob('.archive-*'))
    assert not models.Session.load(s.session_id).archived
def test_final_busy_recheck(env,monkeypatch):
    aa,cfg,models,path=env
    s=make(env)
    calls=iter([True,False])
    monkeypatch.setattr(aa,'_runtime_idle',lambda sid:next(calls))
    assert aa.set_archive_metadata(s.session_id,True,now=100000) is None
    assert not models.Session.load(s.session_id).archived
def test_worker_stop_and_bounded_fairness(env):
    aa,cfg,models,path=env
    for i in range(28): make(env,sid=f's{i}')
    worker=aa.AutoArchiveWorker()
    assert worker.sweep(now=100000)<=25
    for _ in range(3): worker.sweep(now=100000)
    assert all(json.loads(p.read_text())['archived'] for p in path.glob('s*.json'))
    worker.start()
    worker.stop()
    assert not worker.thread.is_alive()

@pytest.mark.parametrize("registry", ["ACTIVE_RUNS", "STREAMS", "SESSION_WRITEBACK_OWNERS",
    "DEFERRED_PROCESS_WAKEUPS", "PENDING_BG_TASK_COMPLETIONS",
    "PENDING_GOAL_CONTINUATION"])
def test_real_runtime_busy_guards(env, monkeypatch, registry):
    aa,cfg,models,path=env
    from api import background
    monkeypatch.setattr(background,"STATE_DIR",path)
    monkeypatch.setattr(aa,"_runtime_idle",real_runtime_idle)
    s=make(env)
    original=s.path.read_bytes()
    monkeypatch.setattr(cfg,registry,{"other-session":"busy"})
    assert aa.set_archive_metadata(s.session_id,True,now=100000) is None
    assert s.path.read_bytes()==original


def test_real_background_btw_compression_and_idle(env,monkeypatch):
    import sqlite3
    from api import background, routes
    aa,cfg,models,path=env
    monkeypatch.setattr(background,"STATE_DIR",path)
    monkeypatch.setattr(aa,"_runtime_idle",real_runtime_idle)
    s=make(env)
    monkeypatch.setattr(background,"_BTW_TRACKING",{"parent":{}})
    assert aa.set_archive_metadata(s.session_id,True,now=100000) is None
    background._BTW_TRACKING.clear()
    monkeypatch.setattr(routes,"_MANUAL_COMPRESSION_JOBS",{"other":{"status":"running"}})
    assert aa.set_archive_metadata(s.session_id,True,now=100000) is None
    routes._MANUAL_COMPRESSION_JOBS.clear()
    with sqlite3.connect(path/'background_tasks.sqlite3') as db:
        db.execute('CREATE TABLE background_tasks(status TEXT)')
        db.execute("INSERT INTO background_tasks VALUES ('running')")
    assert aa.set_archive_metadata(s.session_id,True,now=100000) is None
    with sqlite3.connect(path/'background_tasks.sqlite3') as db:
        db.execute("UPDATE background_tasks SET status='done'")
    assert aa.set_archive_metadata(s.session_id,True,now=100000).archived


def test_large_unknown_prefix_stat_race_and_no_read_archival(env,monkeypatch):
    aa,cfg,models,path=env
    s=make(env)
    before=s.path.read_bytes()
    models.Session.load_metadata_only(s.session_id)
    models.Session.load(s.session_id)
    assert s.path.read_bytes()==before
    monkeypatch.setattr(aa,'MAX_FILE_BYTES',10)
    assert aa.set_archive_metadata(s.session_id,True,now=100000) is None
    monkeypatch.setattr(aa,'MAX_FILE_BYTES',8*1024*1024)
    monkeypatch.setattr(aa,'MAX_PREFIX_BYTES',10)
    assert aa.set_archive_metadata(s.session_id,True,now=100000) is None
    monkeypatch.setattr(aa,'MAX_PREFIX_BYTES',64*1024)
    original_fsync=aa.os.fsync
    changed=False
    def race(fd):
        nonlocal changed
        original_fsync(fd)
        if not changed:
            changed=True
            s.path.write_bytes(before+b' ')
    monkeypatch.setattr(aa.os,'fsync',race)
    assert aa.set_archive_metadata(s.session_id,True,now=100000) is None
    assert s.path.read_bytes()==before+b' '


def test_historical_source_uses_own_profile_db(env, monkeypatch):
    import sqlite3
    from api import profiles
    aa, cfg, models, path = env
    home = path / 'own-profile'
    home.mkdir()
    monkeypatch.setattr(profiles, '_is_isolated_profile_mode', lambda: False)
    monkeypatch.setattr(profiles, '_resolve_profile_home_for_name', lambda name: home if name == 'own' else path / 'missing')
    with sqlite3.connect(home / 'state.db') as db:
        db.execute('CREATE TABLE sessions(id TEXT PRIMARY KEY, source TEXT)')
        db.executemany('INSERT INTO sessions VALUES (?,?)', [('old', 'webui'), ('external', 'cli')])
    old = make(env, sid='old', source_tag=None, profile='own', parent_session_id='compression-parent')
    external = make(env, sid='external', source_tag=None, profile='own')
    unknown = make(env, sid='unknown', source_tag=None, profile='other')
    worker = aa.AutoArchiveWorker()
    assert worker.sweep(now=100000) == 1
    assert models.Session.load(old.session_id).archived
    assert not models.Session.load(external.session_id).archived
    assert not models.Session.load(unknown.session_id).archived


def test_finished_process_mapping_does_not_block_forever(env, monkeypatch):
    import sys
    import types
    import threading
    from api import background
    aa, cfg, models, path = env
    monkeypatch.setattr(background, 'STATE_DIR', path)
    monkeypatch.setattr(aa, '_runtime_idle', real_runtime_idle)
    monkeypatch.setattr(cfg, 'PROCESS_SESSION_INDEX', {'receipt': 'old'})
    registry = types.SimpleNamespace(_lock=threading.Lock(), count_running=lambda: 0)
    monkeypatch.setitem(sys.modules, 'tools.process_registry', types.SimpleNamespace(process_registry=registry))
    s = make(env)
    assert aa.set_archive_metadata(s.session_id, True, now=100000) is not None
    registry.count_running = lambda: 1
    assert aa.set_archive_metadata(s.session_id, False, now=100001) is None


def test_unicode_split_prefix_and_policy_changed_during_copy(env, monkeypatch):
    aa, cfg, models, path = env
    s = make(env)
    s.messages = [{'role': 'user', 'content': 'ã' * 70000}]
    s.save(touch_updated_at=False)
    raw = s.path.read_bytes()
    start = raw.index('ã'.encode())
    monkeypatch.setattr(aa, 'MAX_PREFIX_BYTES', start + 1)
    assert aa.set_archive_metadata(s.session_id, True, now=100000, days=1) is not None
    assert digest(s.path) == hashlib.sha256(raw[raw.index(b'  "messages":'):]).hexdigest()
    monkeypatch.setattr(aa, 'MAX_PREFIX_BYTES', 64 * 1024)
    aa.set_archive_metadata(s.session_id, False, now=100001)
    original_fsync = aa.os.fsync
    def disable(fd):
        original_fsync(fd)
        monkeypatch.setattr(cfg, 'load_settings', lambda: {'auto_archive_days': 0})
    monkeypatch.setattr(aa.os, 'fsync', disable)
    assert aa.set_archive_metadata(s.session_id, True, now=300000, days=1) is None
    assert not models.Session.load(s.session_id).archived
