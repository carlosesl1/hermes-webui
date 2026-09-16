"""Bounded, fail-closed archival of native WebUI sidecar metadata.

The sidecar remains the authority. Never save a cached Session here: even a
full cache can lag the transcript on disk. No GET/list handler calls the sweep.
"""
from __future__ import annotations

from contextlib import closing, contextmanager, ExitStack
import codecs
import json
import logging
import math
import os
from pathlib import Path
import sqlite3
import stat
import sys
import tempfile
import threading
import time

from api import config

logger = logging.getLogger(__name__)
MAX_FILE_BYTES = 8 * 1024 * 1024
MAX_PREFIX_BYTES = 64 * 1024
BATCH_SIZE = 25
TICK_BUDGET_SECONDS = 1.0
INTERVAL_SECONDS = 60


def _timestamp(value):
    return type(value) in (int, float) and math.isfinite(value) and value > 0


def _native(meta, sid):
    required = {'session_id', 'created_at', 'updated_at', 'pinned', 'archived',
                'profile', 'is_cli_session', 'source_tag', 'raw_source',
                'session_source', 'read_only', 'parent_session_id',
                'pre_compression_snapshot', 'worktree_path', 'worktree_branch',
                'worktree_repo_root', 'active_stream_id', 'pending_user_message',
                'pending_attachments', 'pending_started_at', 'message_count',
                'anchor_scene_index'}
    return (required <= meta.keys() and meta['session_id'] == sid
            and (meta['profile'] is None or isinstance(meta['profile'], str))
            and meta['is_cli_session'] is False and meta['read_only'] is False
            and all(meta[k] in (None, '', 'webui') for k in
                    ('source_tag', 'raw_source', 'session_source')))


def _native_provenance(meta):
    """Confirm historical source from its own profile DB; never guess from prose."""
    if any(meta.get(k) == 'webui' for k in ('source_tag', 'raw_source', 'session_source')):
        return True
    from api import profiles
    profile = meta.get('profile')
    if profile and not profiles._is_root_profile(profile) and not profiles._PROFILE_ID_RE.fullmatch(profile):
        return False
    if profiles._is_isolated_profile_mode() and profile and not profiles._profiles_match(profile, profiles._isolated_profile_name()):
        return False
    path = profiles._resolve_profile_home_for_name(profile) / 'state.db'
    if path.is_symlink() or not path.is_file():
        return False
    try:
        with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True, timeout=0)) as db:
            deadline = time.monotonic() + 0.02
            db.set_progress_handler(lambda: time.monotonic() > deadline, 100)
            row = db.execute('SELECT source FROM sessions WHERE id=?', (meta['session_id'],)).fetchone()
            return bool(row and row[0] == 'webui')
    except (OSError, sqlite3.Error):
        return False


def _eligible(meta, now, days):
    if any(meta.get(k) for k in ('archived', 'pinned', 'imported',
            'pre_compression_snapshot', 'worktree_path', 'worktree_branch',
            'worktree_repo_root', 'worktree_created_at', 'active_stream_id',
            'pending_user_message', 'pending_attachments', 'pending_started_at',
            'pending_user_source', 'compression_recovery', 'process_wakeup_pause')):
        return False
    times = [meta.get('created_at'), meta.get('updated_at')]
    grace = meta.get('auto_archive_restored_at')
    if grace is not None:
        times.append(grace)
    return (all(_timestamp(t) for t in times) and max(times) < now - days * 86400
            and _native_provenance(meta))


def _prefix(file):
    """Parse only complete top-level metadata values, never a message array."""
    raw = file.read(MAX_PREFIX_BYTES)
    text = codecs.getincrementaldecoder('utf-8')().decode(raw, final=False)
    decoder = json.JSONDecoder()
    pos = 0
    while pos < len(text) and text[pos].isspace(): pos += 1
    if text[pos:pos+1] != '{': raise ValueError('not an object')
    pos += 1
    meta, spans = {}, {}
    while True:
        while pos < len(text) and text[pos].isspace(): pos += 1
        key_start = pos
        key, pos = decoder.raw_decode(text, pos)
        if not isinstance(key, str) or key in meta: raise ValueError('duplicate key')
        while pos < len(text) and text[pos].isspace(): pos += 1
        if text[pos:pos+1] != ':': raise ValueError('missing colon')
        pos += 1
        while pos < len(text) and text[pos].isspace(): pos += 1
        if key == 'messages':
            if text[pos:pos+1] != '[': raise ValueError('not native messages')
            prefix = text[:key_start]
            file.seek(len(prefix.encode('utf-8')))
            return meta, spans, prefix
        start = pos
        value, pos = decoder.raw_decode(text, pos)
        meta[key] = value
        spans[key] = (start, pos)
        while pos < len(text) and text[pos].isspace(): pos += 1
        if text[pos:pos+1] != ',': raise ValueError('missing metadata delimiter')
        pos += 1


def _runtime_idle(sid):
    """Called with nonblocking registry locks held; uncertainty means busy.

    Global activity conservatively pauses maintenance, including cancelled
    workers, registered subprocesses and pending wakeups, not only SSE.
    """
    for name in ('ACTIVE_RUNS', 'STREAMS', 'SESSION_WRITEBACK_OWNERS',
                 'DEFERRED_PROCESS_WAKEUPS',
                 'PENDING_BG_TASK_COMPLETIONS', 'PENDING_GOAL_CONTINUATION'):
        if getattr(config, name):
            return False
    if config.PROCESS_SESSION_INDEX:
        module = sys.modules.get('tools.process_registry')
        registry = getattr(module, 'process_registry', None)
        if registry is None or not callable(getattr(registry, 'count_running', None)):
            return False
        try:
            if registry.count_running():
                return False
        except Exception:
            return False
    cached = config.SESSIONS.get(sid)
    if cached and any(getattr(cached, k, None) for k in
            ('pinned', 'active_stream_id', 'pending_user_message', 'pending_attachments', 'pending_started_at')):
        return False
    from api import background
    if background._BTW_TRACKING:
        return False
    routes = sys.modules.get('api.routes')
    if routes and any(not isinstance(j, dict) or j.get('status') not in
            ('done', 'error', 'cancelled') for j in routes._MANUAL_COMPRESSION_JOBS.values()):
        return False
    # Do not invoke background._store(): a read must not initialize/migrate it.
    path = background.STATE_DIR / 'background_tasks.sqlite3'
    if path.is_symlink():
        return False
    if path.exists():
        try:
            with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True, timeout=0)) as db:
                deadline = time.monotonic() + 0.01
                db.set_progress_handler(lambda: time.monotonic() > deadline, 100)
                if db.execute("SELECT 1 FROM background_tasks WHERE status NOT IN "
                              "('done','error','no_response','cancelled','interrupted') LIMIT 1").fetchone():
                    return False
        except (OSError, sqlite3.Error):
            return False
    return True


@contextmanager
def _idle_guard(sid):
    from api import background
    locks = [config.LOCK, config.ACTIVE_RUNS_LOCK, config.STREAMS_LOCK,
             config.SESSION_WRITEBACK_OWNERS_LOCK, config.PROCESS_SESSION_INDEX_LOCK,
             config.DEFERRED_PROCESS_WAKEUPS_LOCK, background._lock]
    routes = sys.modules.get('api.routes')
    if routes:
        locks.append(routes._MANUAL_COMPRESSION_JOBS_LOCK)
    registry = getattr(sys.modules.get('tools.process_registry'), 'process_registry', None)
    process_lock = getattr(registry, '_lock', None)
    if process_lock is not None:
        locks.append(process_lock)
    with ExitStack() as stack:
        for lock in locks:
            if not lock.acquire(blocking=False):
                yield False
                return
            stack.callback(lock.release)
        yield _runtime_idle(sid)


def _signature(st):
    return st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns, st.st_ctime_ns


def set_archive_metadata(sid, archived, *, now=None, days=None):
    """Return a fresh metadata Session, or None without a write on uncertainty.

    Manual native operations use the same primitive. A restore grants another
    inactivity interval without changing activity time. Legacy/external callers
    retain their existing route fallback; unsupported native files fail closed.
    """
    from api import models
    if not models.is_safe_session_id(sid) or sid.startswith('_'):
        return None
    now = time.time() if now is None else now
    if not _timestamp(now): return None
    lock = config._get_session_agent_lock(sid)
    if not lock.acquire(blocking=False): return None
    tmp = None
    dirfd = None
    try:
        with _idle_guard(sid) as idle:
            if not idle: return None
        directory = Path(models.SESSION_DIR)
        # Pin the directory and reject a substituted/symlink store and sidecar.
        dirfd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        name = sid + '.json'
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=dirfd)
        with os.fdopen(fd, 'rb') as source:
            before = os.fstat(source.fileno())
            if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or before.st_size > MAX_FILE_BYTES:
                return None
            meta, spans, prefix = _prefix(source)
            if not _native(meta, sid): return None
            if days is not None and not _eligible(meta, now, days): return None
            changes = {'archived': bool(archived)}
            if not archived: changes['auto_archive_restored_at'] = now
            elif days is not None: changes['auto_archived_at'] = now
            # Only archive metadata changes; every other byte survives verbatim.
            for key in sorted((k for k in changes if k in spans), key=lambda k: spans[k][0], reverse=True):
                start, end = spans[key]
                prefix = prefix[:start] + json.dumps(changes[key]) + prefix[end:]
            for key, value in changes.items():
                if key not in spans:
                    prefix += json.dumps(key) + ': ' + json.dumps(value) + ',\n  '
            meta.update(changes)
            result = models.Session(**meta)
            result._loaded_metadata_only = True
            tmpfd, tmp = tempfile.mkstemp(prefix='.archive-', dir=directory)
            with os.fdopen(tmpfd, 'wb') as target:
                os.fchmod(target.fileno(), stat.S_IMODE(before.st_mode))
                target.write(prefix.encode('utf-8'))
                while chunk := source.read(64 * 1024): target.write(chunk)
                target.flush()
                os.fsync(target.fileno())
            if days is not None and config.load_settings().get('auto_archive_days') != days:
                return None
            with _idle_guard(sid) as idle:
                if not idle or _signature(os.fstat(source.fileno())) != _signature(before): return None
                if _signature(os.stat(name, dir_fd=dirfd, follow_symlinks=False)) != _signature(before): return None
                if os.stat(directory, follow_symlinks=False).st_ino != os.fstat(dirfd).st_ino: return None
                os.replace(Path(tmp).name, name, src_dir_fd=dirfd, dst_dir_fd=dirfd)
                tmp = None
                cached = config.SESSIONS.get(sid)
                if cached:
                    for key, value in changes.items(): setattr(cached, key, value)
            try:
                os.fsync(dirfd)
            except OSError:
                logger.warning('Archive committed but directory sync failed for %s', sid)
        try:
            models._write_session_index(updates=[result])
        except Exception:
            # Sidecar/cache already committed: never report a false rollback.
            logger.warning('Archive committed; derived index refresh failed for %s', sid, exc_info=True)
        return result
    except (OSError, ValueError, TypeError, KeyError, RecursionError):
        logger.debug('Skipped unsafe/unavailable archive candidate %s', sid, exc_info=True)
        return None
    finally:
        if tmp is not None:
            try: os.unlink(Path(tmp).name, dir_fd=dirfd)
            except OSError: pass
        if dirfd is not None: os.close(dirfd)
        lock.release()


def _publish(profiles):
    from api.session_events import publish_session_list_changed
    for profile in profiles:
        publish_session_list_changed('session_auto_archive', profile=profile)


class AutoArchiveWorker:
    def __init__(self):
        self.stop_event = threading.Event()
        self.thread = None
        self._entries = None
        self._directory = None

    def sweep(self, *, now=None):
        days = config.load_settings().get('auto_archive_days', 0)
        if type(days) is not int or not 1 <= days <= 3650:
            self._close_cursor()
            return 0
        from api import models
        directory = Path(models.SESSION_DIR)
        if directory.is_symlink(): return 0
        if self._entries is None or directory != self._directory:
            self._close_cursor()
            self._entries = os.scandir(directory)
            self._directory = directory
        now = time.time() if now is None else now
        deadline = time.monotonic() + TICK_BUDGET_SECONDS
        count, profiles = 0, set()
        for _ in range(BATCH_SIZE):
            if self.stop_event.is_set() or time.monotonic() >= deadline: break
            entry = next(self._entries, None)
            if entry is None:
                self._close_cursor()
                break
            if not entry.name.endswith('.json') or not entry.is_file(follow_symlinks=False): continue
            result = set_archive_metadata(entry.name[:-5], True, now=now, days=days)
            if result is not None:
                count += 1
                profiles.add(result.profile)
        if profiles: _publish(profiles)
        return count

    def _close_cursor(self):
        if self._entries is not None: self._entries.close()
        self._entries = None

    def _run(self):
        try:
            while not self.stop_event.wait(INTERVAL_SECONDS):
                try: self.sweep()
                except Exception:
                    self._close_cursor()
                    logger.warning('Auto archive sweep failed; will retry next tick', exc_info=True)
        finally:
            self._close_cursor()

    def start(self):
        if self.thread is not None: return
        self.thread = threading.Thread(target=self._run, name='session-auto-archive', daemon=True)
        self.thread.start()

    def stop(self):
        self.stop_event.set()
        if self.thread is not None: self.thread.join(timeout=2)
