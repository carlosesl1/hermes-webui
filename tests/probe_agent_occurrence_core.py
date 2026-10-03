"""Optional no-provider integration gate against an explicitly selected Hermes core.

Run with a core-compatible Python and HERMES_OCCURRENCE_CORE_DIR set. With -I -S,
HERMES_OCCURRENCE_DEPENDENCIES may list existing ABI-compatible site-packages.
No real HOME/state is accessed and no dependency installation is allowed.
"""
import copy
import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace


def main():
    core = Path(os.environ['HERMES_OCCURRENCE_CORE_DIR']).resolve()
    assert (core / 'hermes_state.py').is_file(), 'Select an existing core checkout'
    webui = Path(__file__).resolve().parents[1]
    sys.dont_write_bytecode = True
    sys.path.extend(path for path in os.environ.get('HERMES_OCCURRENCE_DEPENDENCIES', '').split(os.pathsep) if path)
    with tempfile.TemporaryDirectory(prefix='occurrence-core-', dir=os.environ.get('TMPDIR')) as tmp:
        root = Path(tmp)
        for name, suffix in {
            'HOME': 'home', 'HERMES_HOME': 'hermes', 'HERMES_BASE_HOME': 'hermes',
            'HERMES_CONFIG_PATH': 'hermes/config.yaml', 'HERMES_WEBUI_STATE_DIR': 'state',
        }.items():
            os.environ[name] = str(root / suffix)
        os.environ['HERMES_WEBUI_AGENT_DIR'] = str(core)
        os.environ['HERMES_DISABLE_LAZY_INSTALLS'] = '1'
        sys.path[:0] = [str(webui), str(core)]
        from hermes_state import SessionDB
        from agent.message_metadata import without_persistence_fields
        from agent.session_persistence import _db_flush_collect, _db_flush_write
        from api import models, streaming

        db_path = root / 'hermes/state.db'
        db_path.parent.mkdir(parents=True, exist_ok=True)
        assert models._active_state_db_path().resolve() == db_path.resolve()
        models.SESSION_DIR.mkdir(parents=True, exist_ok=True)
        report = {'core_source': str(core), 'webui_source': str(webui), 'cases': []}
        db = SessionDB(db_path)
        try:
            for eager in (False, True):
                sid = 'eager' if eager else 'deferred'
                db.create_session(sid, 'webui', model='offline-no-provider')
                # The repeated question is a second real occurrence, not replay.
                previous = [
                    {'role': 'user', 'content': 'Continue', 'timestamp': 100.0},
                    {'role': 'assistant', 'content': 'Earlier answer', 'timestamp': 110.0},
                ]
                current = [
                    {'role': 'user', 'content': 'Continue', 'timestamp': 201.0},
                    {'role': 'assistant', 'content': 'Current answer', 'timestamp': 210.0},
                ]
                db.append_messages_batch(sid, previous + current)
                checkpoint = {'role': 'user', 'content': 'Continue', 'timestamp': 200.0,
                              '_active_turn_token': sid}
                identity = {'session_id': sid, 'token': sid, 'text': 'Continue', 'timestamp': 200.0,
                            'source': 'webui', 'checkpoint': copy.deepcopy(checkpoint),
                            'current_turn_user_idx': 2, 'turn_id': sid,
                            'agent_turn_boundary_resolved': True}
                display = copy.deepcopy(previous) + ([copy.deepcopy(checkpoint)] if eager else [])
                session = models.Session(session_id=sid, messages=display)
                returned = copy.deepcopy(previous + current)
                streaming._settle_result_messages(session, display, copy.deepcopy(previous),
                                                  returned, 'Continue', 'webui', identity)
                expected_uids = [row['message_uid'] for row in previous + current]
                assert [row.get('message_uid') for row in session.messages] == expected_uids
                assert [row.get('message_uid') for row in session.context_messages] == expected_uids
                history = streaming._sanitize_messages_for_agent(session.context_messages)
                for row in history:
                    wire = without_persistence_fields(row)
                    assert 'message_uid' not in wire and 'timestamp' not in wire
                # Real core compression and its subsequent incremental flush.
                db.archive_and_compact(sid, history[-2:], tail_count=2)
                agent = SimpleNamespace(session_id=sid, _session_db=db, _last_flushed_db_idx=0)
                rows, messages = _db_flush_collect(agent, history[-2:], None)
                _db_flush_write(agent, rows, messages, history[-2:])
                active = db.get_messages(sid)
                assert len(active) == 2
                assert [row['message_uid'] for row in active] == expected_uids[-2:]
                session.messages = models.merge_session_messages_append_only(
                    session.messages, models.get_state_db_session_messages(sid))
                assert len(session.messages) == 4
                session.save(skip_index=True)
                loaded = models.Session.load(sid)
                assert loaded is not None
                assert len(loaded.messages) == 4
                assert [row.get('message_uid') for row in loaded.messages] == expected_uids
                assert sum(row['role'] == 'user' and row['content'] == 'Continue'
                           for row in loaded.messages) == 2
                assert sum(row['content'] == 'Current answer' for row in loaded.messages) == 1
                report['cases'].append({'mode': sid, 'before': 4, 'after_reload': len(loaded.messages),
                                        'active_core_rows': len(active), 'genuine_repeat_count': 2,
                                        'current_answer_count': 1, 'uid_preserved': True})
        finally:
            db.close()
        print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
