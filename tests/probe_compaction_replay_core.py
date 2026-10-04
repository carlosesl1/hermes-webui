"""Optional real-core, no-provider compaction replay roundtrip gate.

Select an existing core via HERMES_OCCURRENCE_CORE_DIR and ABI-matched existing
site-packages via HERMES_OCCURRENCE_DEPENDENCIES (os.pathsep separated). Launch
with -I -S, an empty environment, HOME/state/XDG/TMPDIR already under an empty
HERMES_PROBE_SANDBOX, HERMES_DISABLE_LAZY_INSTALLS=1 and
PYTHONDONTWRITEBYTECODE=1. No constructor/provider/compress() inference runs:
ContextCompressor.__new__ exercises its real replay method instead.

Isolation/runtime pattern: tests/probe_agent_occurrence_core.py. Synthetic task
and literal-quote pattern: compaction-visibility/source-probe.py evidence.
Prints JSON evidence; nonzero exit means failed/blocked, never a fabricated pass.
"""
import copy
import hashlib
import inspect
import json
import os
from pathlib import Path
import sys
import traceback
from types import SimpleNamespace


def main():
    root = Path(os.environ['HERMES_PROBE_SANDBOX']).resolve()
    core = Path(os.environ['HERMES_OCCURRENCE_CORE_DIR']).resolve()
    webui = Path(__file__).resolve().parents[1]
    assert sys.flags.isolated and sys.flags.no_site, 'Use -I -S'
    assert os.environ['HERMES_DISABLE_LAZY_INSTALLS'] == '1'
    assert os.environ['PYTHONDONTWRITEBYTECODE'] == '1'
    sys.dont_write_bytecode = True
    for key in ('HOME', 'HERMES_HOME', 'HERMES_BASE_HOME', 'HERMES_CONFIG_PATH',
                'HERMES_WEBUI_STATE_DIR', 'TMPDIR', 'XDG_CACHE_HOME',
                'XDG_CONFIG_HOME', 'XDG_DATA_HOME'):
        assert Path(os.environ[key]).resolve().is_relative_to(root), key
    assert os.environ['HERMES_WEBUI_AGENT_DIR'] == str(core)
    deps = [Path(p).resolve() for p in os.environ.get('HERMES_OCCURRENCE_DEPENDENCIES', '').split(os.pathsep) if p]
    assert all(p.is_dir() for p in deps), 'Existing dependencies only'
    sys.path.extend(map(str, deps))
    sys.path[:0] = [str(webui), str(core)]
    denied = []
    reads = [core, webui, root, *deps, Path(sys.base_prefix).resolve()]

    def safe_path(value, writing=False):
        if not isinstance(value, (str, bytes, os.PathLike)):
            return
        path = Path(os.fsdecode(value)).resolve()
        permitted = path.is_relative_to(root) if writing else any(path.is_relative_to(p) for p in reads)
        if not writing and str(path) in ('/dev/null', '/dev/urandom', '/etc/localtime', '/usr/share/zoneinfo/UTC'):
            permitted = True
        if not path.is_relative_to(root) and path.name in ('.env', 'auth.json', 'credentials.json', 'state.db'):
            permitted = False
        if not permitted:
            denied.append({'operation': 'write' if writing else 'read', 'path': str(path)})
            raise PermissionError('Probe isolation blocked ' + str(path))

    def audit(event, args):
        if event == 'open':
            mode, flags = args[1], args[2]
            writing = bool(isinstance(mode, str) and any(c in mode for c in 'wax+')) or bool(
                isinstance(flags, int) and flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC))
            safe_path(args[0], writing)
        elif event in ('os.mkdir', 'os.remove', 'os.rmdir', 'os.chmod'):
            safe_path(args[0], True)
        elif event in ('os.rename', 'os.link', 'os.symlink'):
            safe_path(args[0], True)
            safe_path(args[1], True)
        elif event == 'sqlite3.connect':
            value = str(args[0])
            if value != ':memory:':
                safe_path(value.removeprefix('file:').split('?', 1)[0], True)
        elif event in ('socket.connect', 'socket.connect_ex', 'socket.bind', 'socket.getaddrinfo',
                       'subprocess.Popen', 'os.system'):
            denied.append({'operation': event})
            raise PermissionError('No network/process execution in probe')
    sys.addaudithook(audit)
    paths = [Path(__file__).resolve(), webui / 'api/compaction_provenance.py',
             webui / 'api/models.py', webui / 'api/streaming.py',
             core / 'agent/context_compressor.py', core / 'agent/message_metadata.py',
             core / 'agent/session_persistence.py', core / 'hermes_state.py',
             core / 'hermes_state_compression.py', core / 'hermes_state_messages.py']
    hashes = lambda: {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}
    before = hashes()
    report = {'status': 'running', 'core_revision': os.environ.get('HERMES_PROBE_CORE_SHA'),
              'webui_revision': os.environ.get('HERMES_PROBE_WEBUI_SHA'),
              'python': sys.version, 'executable': sys.executable, 'dependencies': list(map(str, deps)),
              'command': os.environ.get('HERMES_PROBE_COMMAND'), 'sandbox': str(root),
              'source_hashes_before': before, 'cases': [], 'checks': [], 'blocked_operations': denied,
              'scope': 'Real replay producer and persistence only; no provider calls, full compression, HTTP or browser.'}
    try:
        from agent import context_compressor as cc
        from agent.message_metadata import without_persistence_fields
        from agent.session_persistence import _db_flush_collect, _db_flush_write
        from hermes_state import SessionDB
        from api import models, streaming
        from api.compaction_provenance import (install_compaction_replay_provenance,
                                              is_compaction_replay, display_without_compaction_replays)
        assert Path(inspect.getfile(cc.ContextCompressor)).resolve() == core / 'agent/context_compressor.py'
        report['archive_signature'] = str(inspect.signature(SessionDB.archive_and_compact))
        db_path = root / 'hermes/state.db'
        db_path.parent.mkdir(parents=True, exist_ok=True)
        models.SESSION_DIR.mkdir(parents=True, exist_ok=True)
        assert models._active_state_db_path().resolve() == db_path
        assert models.SESSION_DIR.resolve().is_relative_to(root)
        compressor = cc.ContextCompressor.__new__(cc.ContextCompressor)
        compressor.quiet_mode = True
        control = cc.ContextCompressor.__new__(cc.ContextCompressor)
        control.quiet_mode = True
        original_method = cc.ContextCompressor._reappend_inflight_user_task
        assert install_compaction_replay_provenance(compressor)
        wrapper = compressor._reappend_inflight_user_task
        assert install_compaction_replay_provenance(compressor)
        assert compressor._reappend_inflight_user_task is wrapper
        assert cc.ContextCompressor._reappend_inflight_user_task is original_method
        assert control._reappend_inflight_user_task.__func__ is original_method
        report['checks'].append('real-class __new__; instance-only idempotent installation; class/control unchanged')
        header = cc._INFLIGHT_TASK_REPLAY_HEADER
        quote_content = header + '\nI am quoting this literally.'

        def payload(row):
            return {key: copy.deepcopy(row.get(key)) for key in ('role', 'content', 'display_kind', 'display_metadata')}

        def carrier(role='assistant'):
            return {'role': role, 'content': cc.SUMMARY_PREFIX + '\nSynthetic summary\n' + cc._SUMMARY_END_MARKER,
                    'display_kind': 'context_summary', 'display_metadata': {'version': 1}}

        def same_replay(rows, expected):
            found = [row for row in rows if is_compaction_replay(row)]
            assert found, 'Replay missing'
            assert payload(found[-1]) == expected, (payload(found[-1]), expected)
            return found[-1]

        db = SessionDB(db_path)
        try:
            for mode, content in (
                ('text', 'Finish the synthetic request'),
                ('multimodal', [{'type': 'text', 'text': 'Inspect this synthetic image'},
                                {'type': 'image_url', 'image_url': {'url': 'data:image/png;base64,AA=='}}]),
            ):
                sid = 'probe-compaction-' + mode
                quote = {'role': 'user', 'content': quote_content, 'message_uid': mode + '-quote', 'timestamp': 1.0}
                task = {'role': 'user', 'content': copy.deepcopy(content), 'message_uid': mode + '-task',
                        'timestamp': 3.0, 'api_content': 'stale synthetic sidecar'}
                task_before, quote_before = copy.deepcopy(task), copy.deepcopy(quote)
                initial = [copy.deepcopy(quote), {'role': 'assistant', 'content': 'Quote acknowledged', 'timestamp': 2.0},
                           copy.deepcopy(task), {'role': 'assistant', 'content': 'Starting synthetic task', 'timestamp': 4.0}]
                db.create_session(sid, 'webui', model='offline-no-provider')
                db.append_messages_batch(sid, initial)
                inflight = task
                case = {'mode': mode, 'rounds': []}
                for round_no in (1, 2, 3):
                    compressed = [copy.deepcopy(quote), {'role': 'assistant', 'content': 'Quote acknowledged'},
                                  copy.deepcopy(task), carrier()]
                    prefix = copy.deepcopy(compressed)
                    identities = tuple(compressed)
                    inflight_before = copy.deepcopy(inflight)
                    plain = control._reappend_inflight_user_task(copy.deepcopy(compressed), copy.deepcopy(inflight))
                    result = compressor._reappend_inflight_user_task(compressed, inflight)
                    assert result is compressed and len(result) == len(prefix) + 1
                    assert all(a is b for a, b in zip(identities, result, strict=False))
                    assert result[:-1] == prefix and inflight == inflight_before
                    replay = result[-1]
                    assert replay is not inflight and is_compaction_replay(replay)
                    # Replay is the SOLE surviving current task at a later compression.
                    assert compressor._is_actionable_user_turn(replay), 'Provenance changed core task authority'
                    current = compressor._find_inflight_user_task([carrier(), replay])
                    assert current is not None and current['content'] == replay['content']
                    assert replay['display_metadata'] == {'webui_compaction_replay': {
                        'version': 1, 'source_message_uid': task['message_uid']}}
                    assert not replay.get('display_kind')
                    assert 'timestamp' not in replay and 'api_content' not in replay
                    # Compare to the unwrapped core, including its multimodal header behavior.
                    assert without_persistence_fields(replay) == without_persistence_fields(plain[-1])
                    if mode == 'text':
                        assert replay['content'].count(header) == 1
                    else:
                        assert replay['content'][-1] == content[-1]
                    producer_expected = payload(replay)
                    next_live_replay = copy.deepcopy(replay)
                    # SessionDB intentionally stores multimodal display text (images become
                    # [screenshot]); compare with an unwrapped-core control, not invented
                    # lossless image persistence. Provenance must survive that conversion.
                    control_sid = sid + '-control-' + str(round_no)
                    db.create_session(control_sid, 'webui', model='offline-no-provider')
                    db.archive_and_compact(control_sid, plain, tail_count=0)
                    baseline_rows = db.get_messages(control_sid)
                    db.archive_and_compact(sid, result, tail_count=0)
                    active = db.get_messages(sid)
                    expected = dict(producer_expected, content=baseline_rows[-1]['content'])
                    assert (active[-1]['role'], active[-1]['content']) == (baseline_rows[-1]['role'], baseline_rows[-1]['content'])
                    same_replay(active, expected)
                    live_agent = SimpleNamespace(session_id=sid, _session_db=db, _last_flushed_db_idx=0)
                    count_before = len(active)
                    batch_rows, batch_msgs = _db_flush_collect(live_agent, result, None)
                    _db_flush_write(live_agent, batch_rows, batch_msgs, result)
                    assert len(db.get_messages(sid)) == count_before, 'Compacted rows duplicated by incremental flush'
                    # Append a new durable assistant row using the actual incremental writer.
                    result.append({'role': 'assistant', 'content': 'Synthetic round %d answer' % round_no})
                    batch_rows, batch_msgs = _db_flush_collect(live_agent, result, None)
                    assert len(batch_rows) == 1
                    _db_flush_write(live_agent, batch_rows, batch_msgs, result)
                    active = db.get_messages(sid)
                    assert len(active) == count_before + 1
                    control_agent = SimpleNamespace(session_id=control_sid, _session_db=db, _last_flushed_db_idx=0)
                    control_batch, control_msgs = _db_flush_collect(control_agent, plain, None)
                    _db_flush_write(control_agent, control_batch, control_msgs, plain)
                    plain.append({'role': 'assistant', 'content': 'Synthetic round %d answer' % round_no})
                    control_batch, control_msgs = _db_flush_collect(control_agent, plain, None)
                    _db_flush_write(control_agent, control_batch, control_msgs, plain)
                    persisted_control = db.get_messages(control_sid)
                    expected = dict(expected, content=persisted_control[-2]['content'])
                    same_replay(active, expected)
                    full = models.get_state_db_session_messages(sid)
                    tail = models.get_state_db_session_messages(sid, limit=2)
                    same_replay(full, expected)
                    same_replay(tail, expected)
                    assert len(tail) == 2 and payload(tail[-2]) == expected
                    archive = models.get_state_db_session_messages(sid, include_inactive=True)
                    assert len(archive) > len(full)
                    full_before = copy.deepcopy(full)
                    sanitized = streaming._sanitize_messages_for_agent(full, cfg={})
                    sanitized_replay = same_replay(sanitized, expected)
                    wire = without_persistence_fields(sanitized_replay)
                    assert 'display_kind' not in wire and 'display_metadata' not in wire
                    assert wire['role'] == 'user' and wire['content'] == expected['content']
                    assert full == full_before and payload(sanitized_replay) == expected
                    assert any(row['content'] == quote_content and not is_compaction_replay(row) for row in full)
                    shown = display_without_compaction_replays(full)
                    assert any(row['content'] == quote_content for row in shown)
                    assert not any(is_compaction_replay(row) for row in shown)
                    session = models.Session(session_id=sid, messages=copy.deepcopy(full))
                    session.context_messages = copy.deepcopy(sanitized)
                    session.save(skip_index=True)
                    loaded = models.Session.load(sid)
                    assert loaded is not None
                    same_replay(loaded.messages, expected)
                    same_replay(loaded.context_messages, expected)
                    assert any(row['content'] == quote_content and not is_compaction_replay(row) for row in loaded.messages)
                    assert task == task_before and quote == quote_before
                    case['rounds'].append({'round': round_no, 'active_rows': len(active),
                                           'full_rows': len(full), 'tail_rows': len(tail), 'archive_rows': len(archive),
                                           'save_reload_rows': len(loaded.messages),
                                           'metadata_role_preserved_content_matches_core_storage': True,
                                           'producer_content_type': type(producer_expected['content']).__name__,
                                           'stored_content_type': type(expected['content']).__name__,
                                           'core_storage_changed_content': producer_expected['content'] != expected['content'],
                                           'literal_quote_retained': True,
                                           'incremental_write_rows': len(batch_rows), 'provider_metadata_stripped': True,
                                           'source_inputs_unchanged': True, 'unwrapped_payload_parity': True})
                    restored = copy.deepcopy(loaded.context_messages[-2])
                    assert is_compaction_replay(restored)
                    inflight = next_live_replay if mode == 'multimodal' else restored
                case['checks'] = ['3 replay/persistence rounds', 'archive_and_compact + incremental flush',
                                  'WebUI full + tail reads', 'agent sanitizer + provider projection',
                                  'save/reload messages + context', 'literal human quote stays visible']
                report['cases'].append(case)
            # User-leading carrier is intentionally mutated by core, never marked as standalone replay.
            source = {'role': 'user', 'content': 'Merged synthetic task', 'message_uid': 'merge-task'}
            source_before = copy.deepcopy(source)
            merged, baseline = [carrier('user')], [carrier('user')]
            assert compressor._reappend_inflight_user_task(merged, source) is merged
            control._reappend_inflight_user_task(baseline, copy.deepcopy(source))
            assert merged == baseline and len(merged) == 1 and not is_compaction_replay(merged[0])
            assert source == source_before and header in merged[0]['content']
            report['checks'].append('merged carrier exactly matches core-intended mutation, no replay stamp/source mutation')
            assert not is_compaction_replay({'role': 'user', 'content': quote_content})
            report['checks'].append('literal replay header alone never establishes producer provenance')
        finally:
            db.close()
        after = hashes()
        report['source_hashes_after'] = after
        assert after == before, 'Sources changed during probe'
        report['isolation_note'] = ('All logged operations were denied before execution. '
                                    'Imports may probe process/system/network availability; '
                                    'no provider/full-compression calls were made.')
        report['status'] = 'passed'
    except Exception:
        report['status'] = 'failed'
        report['error'] = traceback.format_exc()
        report['source_hashes_after'] = hashes()
    print(json.dumps(report, indent=2))
    return 0 if report['status'] == 'passed' else 1


if __name__ == '__main__':
    raise SystemExit(main())
