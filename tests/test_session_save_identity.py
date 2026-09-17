"""Disk identity is evidence, not a trusted metadata count."""
import json
import pytest

@pytest.fixture
def store(tmp_path, monkeypatch):
    import api.models as m
    monkeypatch.setattr(m, 'SESSION_DIR', tmp_path)
    monkeypatch.setattr(m, '_clear_webui_zero_message_orphan_tombstone', lambda *a: None)
    monkeypatch.setattr(m, '_clear_webui_deleted_session_tombstone', lambda *a: None)
    return m

def session(store):
    s = store.Session(session_id='save_identity', messages=[{'role':'user','content':str(i)} for i in range(4)])
    s.save(skip_index=True)
    return s

def test_unchanged_save_does_not_parse_history(store, monkeypatch):
    s = session(store)
    original = store.json.loads
    def loads(value, *a, **kw):
        raw = value.encode() if isinstance(value, str) else value
        assert b'"messages"' not in raw
        return original(value, *a, **kw)
    monkeypatch.setattr(store.json, 'loads', loads)
    s.save(skip_index=True)

def test_draft_does_not_rewrite_transcript(store):
    s = session(store)
    before = s.path.read_bytes()
    s.save_draft({'text':'hello'})
    assert s.path.read_bytes() == before
    assert store.Session.load(s.session_id).composer_draft == {'text':'hello'}
    assert store.Session.load_metadata_only(s.session_id).composer_draft == {'text':'hello'}

def test_backup_failure_blocks_shrink(store, monkeypatch):
    s = session(store)
    before = s.path.read_bytes()
    s.messages.pop()
    original = store._safe_replace
    def replace(src, dst):
        if str(dst).endswith('.bak'):
            raise OSError('backup unavailable')
        return original(src,dst)
    monkeypatch.setattr(store, '_safe_replace', replace)
    with pytest.raises(OSError):
        s.save(skip_index=True)
    assert s.path.read_bytes() == before

@pytest.mark.parametrize('replace', [False, True])
def test_external_change_invalidates_count(store, replace):
    s = session(store)
    data = json.loads(s.path.read_bytes())
    data['messages'].append({'role':'user','content':'external append'})
    data['message_count'] = 0  # deliberately stale, never authoritative
    external = json.dumps(data).encode()
    if replace:
        p = s.path.with_suffix('.external')
        p.write_bytes(external)
        p.replace(s.path)
    else:
        s.path.write_bytes(external)
    s.save(skip_index=True)
    assert s.path.with_suffix('.json.bak').read_bytes() == external

def test_draft_survives_stale_agent_and_clear_persists(store):
    s = session(store)
    agent = store.Session.load(s.session_id)
    stub = store.Session.load_metadata_only(s.session_id)
    stub.save_draft({'text':'new typing'})
    agent.messages.append({'role':'assistant','content':'agent append'})
    agent.save(skip_index=True)
    loaded = store.Session.load(s.session_id)
    assert len(loaded.messages) == 5
    assert loaded.composer_draft == {'text':'new typing'}
    loaded.composer_draft = {}
    loaded.save(skip_index=True)
    assert store.Session.load(s.session_id).composer_draft == {}

def test_draft_failure_leaves_memory_and_transcript(store, monkeypatch):
    s = session(store)
    before = s.path.read_bytes()
    def fail(*args):
        raise OSError('disk full')
    monkeypatch.setattr(store, '_safe_replace', fail)
    with pytest.raises(OSError):
        s.save_draft({'text':'not saved'})
    assert s.composer_draft == {}
    assert s.path.read_bytes() == before
    assert not list(s.path.parent.glob('*.tmp.*'))

def test_corrupt_file_is_backed_up(store):
    s = session(store)
    s.path.write_bytes(b'broken json\xff')
    s.save(skip_index=True)
    assert s.path.with_suffix('.json.bak').read_bytes() == b'broken json\xff'

def test_load_primes_only_matching_identity(store, monkeypatch):
    s = session(store)
    loaded = store.Session.load(s.session_id)
    original = store.json.loads
    def loads(value, *args, **kwargs):
        assert b'"messages"' not in (value.encode() if isinstance(value, str) else value)
        return original(value, *args, **kwargs)
    monkeypatch.setattr(store.json, 'loads', loads)
    loaded.save(skip_index=True)

def test_race_during_backup_rechecks_latest_file(store, monkeypatch):
    s = session(store)
    s.messages.pop()
    latest = json.loads(s.path.read_bytes())
    latest['messages'].append({'role':'user','content':'racing append'})
    latest_bytes = json.dumps(latest).encode()
    original = store._safe_replace
    changed = []
    def replace(src, dst):
        result = original(src, dst)
        if str(dst).endswith('.bak') and not changed:
            changed.append(True)
            s.path.write_bytes(latest_bytes)
        return result
    monkeypatch.setattr(store, '_safe_replace', replace)
    s.save(skip_index=True)
    assert s.path.with_suffix('.json.bak').read_bytes() == latest_bytes

def test_parallel_save_and_draft_keep_agent_append(store):
    from concurrent.futures import ThreadPoolExecutor
    s = session(store)
    draft = store.Session.load_metadata_only(s.session_id)
    s.messages.append({'role':'assistant','content':'new answer'})
    with ThreadPoolExecutor(max_workers=2) as pool:
        a = pool.submit(s.save, skip_index=True)
        b = pool.submit(draft.save_draft, {'text':'typing'})
        a.result(timeout=5)
        b.result(timeout=5)
    loaded = store.Session.load(s.session_id)
    assert len(loaded.messages) == 5
    assert loaded.composer_draft == {'text':'typing'}

def test_post_draft_route_preserves_transcript(store, monkeypatch):
    from types import SimpleNamespace
    import api.routes as routes
    s = session(store)
    before = s.path.read_bytes()
    monkeypatch.setattr(routes, '_check_csrf', lambda handler: True)
    monkeypatch.setattr(routes, '_is_subagent_child_session_id', lambda sid: False)
    modes = []
    def get_metadata(sid, metadata_only=False):
        modes.append(metadata_only)
        assert metadata_only is True
        return store.Session.load_metadata_only(sid)
    monkeypatch.setattr(routes, 'get_session', get_metadata)
    monkeypatch.setattr(routes, 'read_body', lambda handler: {'session_id':s.session_id, 'text':'route draft'})
    output = {}
    monkeypatch.setattr(routes, 'j', lambda handler, payload, **kw: output.update(payload) or True)
    handler = SimpleNamespace(command='POST', _safe_webui_print=lambda *a: None)
    routes.handle_post(handler, SimpleNamespace(path='/api/session/draft'))
    assert output['draft'] == {'text':'route draft'}
    assert modes and all(modes), "Draft route must perform metadata-only reads"
    assert s.path.read_bytes() == before


def test_draft_never_serializes_or_parses_history(store, monkeypatch):
    s = session(store)
    stub = store.Session.load_metadata_only(s.session_id)
    before = s.path.read_bytes()
    original = store.json.dumps
    def dumps(value, *args, **kwargs):
        assert 'messages' not in value
        return original(value, *args, **kwargs)
    def no_parse(*args, **kwargs):
        pytest.fail('draft persistence must not parse the transcript')
    monkeypatch.setattr(store.json, 'dumps', dumps)
    monkeypatch.setattr(store.json, 'loads', no_parse)
    stub.save_draft({'text': 'typing'})
    assert s.path.read_bytes() == before
    assert stub.updated_at == s.updated_at


@pytest.mark.parametrize('replace', [False, True])
def test_same_size_mtime_external_change_invalidates_cache(store, monkeypatch, replace):
    import os
    s = session(store)
    before = s.path.stat()
    # Some filesystems expose nanosecond fields with a coarser clock tick.
    # Advance beyond that tick so this test exercises a changed ctime identity.
    import time
    time.sleep(0.02)
    raw = s.path.read_bytes().replace(b'"content": "0"', b'"content": "X"')
    target = s.path.with_suffix('.external') if replace else s.path
    target.write_bytes(raw)
    os.utime(target, ns=(before.st_atime_ns, before.st_mtime_ns))
    if replace:
        target.replace(s.path)
    original = store.json.loads
    parsed = []
    def loads(value, *args, **kwargs):
        if isinstance(value, bytes) and b'"messages"' in value:
            parsed.append(value)
        return original(value, *args, **kwargs)
    monkeypatch.setattr(store.json, 'loads', loads)
    s.save(skip_index=True)
    assert parsed == [raw]


def test_metadata_save_refusal_precedes_draft_changes(store):
    s = session(store)
    stub = store.Session.load_metadata_only(s.session_id)
    before = s.path.read_bytes()
    stub.composer_draft = {'text': 'stale'}
    with pytest.raises(RuntimeError, match='metadata-only'):
        stub.save(skip_index=True)
    assert s.path.read_bytes() == before
    assert not s.draft_path.exists()


def test_parallel_full_saves_preserve_larger_snapshot(store):
    from concurrent.futures import ThreadPoolExecutor
    import threading
    s = session(store)
    other = store.Session.load(s.session_id)
    s.messages.append({'role': 'assistant', 'content': 'large'})
    other.messages.pop()
    barrier = threading.Barrier(2)
    def save(item):
        barrier.wait(timeout=5)
        item.save(skip_index=True)
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(save, item) for item in (s, other)]
        for future in futures:
            future.result(timeout=5)
    live = json.loads(s.path.read_bytes())
    backup = json.loads(s.path.with_suffix('.json.bak').read_bytes())
    assert max(len(live['messages']), len(backup['messages'])) == 5
    assert not list(s.path.parent.glob('*.tmp.*'))


def test_repeated_external_race_fails_closed(store, monkeypatch):
    s = session(store)
    s.messages.pop()
    raw = s.path.read_bytes()
    original = store._safe_replace
    def replace(src, dst):
        result = original(src, dst)
        if str(dst).endswith('.bak'):
            s.path.write_bytes(raw)
        return result
    monkeypatch.setattr(store, '_safe_replace', replace)
    with pytest.raises(RuntimeError, match='changed repeatedly'):
        s.save(skip_index=True)
    assert s.path.read_bytes() == raw
    assert not list(s.path.parent.glob('*.tmp.*'))
