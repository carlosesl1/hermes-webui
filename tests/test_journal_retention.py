"""Synthetic temporary state only; real writer with an explicit temporary root."""
import hashlib
import json
import os
from pathlib import Path
import time

import pytest

from api import journal_retention as retention
from api import run_journal


@pytest.fixture
def state(tmp_path, monkeypatch):
    root = tmp_path / "sessions"
    folder = root / "_run_journal" / "session"
    folder.mkdir(parents=True)
    backup = tmp_path / "backups"
    backup.mkdir(mode=0o700)
    now = time.time()
    old = now - 40 * 86400
    messages = [{"role": "user", "content": "question", "timestamp": old},
                {"role": "assistant", "content": "answer", "timestamp": old + 1}]
    side = {"session_id": "session", "active_stream_id": None, "messages": messages}
    sidecar = root / "session.json"
    sidecar.write_text(json.dumps(side))
    journal = folder / "run.jsonl"
    # Never synthesize the envelope: exercise the same writer as live streaming.
    writer = run_journal.RunJournalWriter("session", "run", session_dir=root)
    with monkeypatch.context() as clock:
        for seq, name in enumerate(["token", "done", "stream_end"], 1):
            clock.setattr(run_journal.time, "time", lambda seq=seq: old + seq - 1)
            writer.append_sse_event(name, {"session": side} if name == "done" else {})
    assert run_journal.latest_run_summary("session", "run", session_dir=root)["terminal_state"] == "completed"
    os.utime(journal, (old, old))
    marker = tmp_path / "offline.json"
    marker.write_text(json.dumps({"webui_stopped": True, "all_workers_stopped": True, "automatic_restarts_disabled": True,
                                 "session_dir": str(root), "device": root.stat().st_dev,
                                 "inode": root.stat().st_ino, "created_at": now}))
    marker.chmod(0o600)
    return root, journal, sidecar, backup, marker


def run(state, **kwargs):
    root, _, _, backup, marker = state
    return retention.maintain(root, backup_dir=backup, offline_marker=marker, **kwargs)


def entry(result):
    return next(e for e in result["entries"] if e["path"].endswith("run.jsonl"))


def change_rows(state, edit):
    journal = state[1]
    old = journal.stat().st_mtime
    rows = [json.loads(line) for line in journal.read_text().splitlines()]
    edit(rows)
    journal.write_text("".join(json.dumps(row) + "\n" for row in rows))
    os.utime(journal, (old, old))


def test_default_dry_run_never_writes(state):
    before = {p: p.read_bytes() for p in state[0].rglob("*") if p.is_file()}
    result = run(state)
    assert result["mode"] == "dry-run" and result["days"] == 30
    assert entry(result)["status"] == "eligible"
    assert {p: p.read_bytes() for p in before} == before
    assert list(state[3].iterdir()) == []


def test_apply_verified_backup_and_restore_preserves_transcript(state):
    root, journal, sidecar, _, marker = state
    original, transcript = journal.read_bytes(), sidecar.read_bytes()
    db = root / "state.db"
    db.write_bytes(b"synthetic database sentinel")
    result = run(state, apply=True)
    assert entry(result)["status"] == "removed" and not journal.exists()
    bundle = Path(result["bundle"])
    manifest = json.loads((bundle / "manifest.json").read_text())
    saved = entry(manifest)
    assert (bundle / saved["backup"]).read_bytes() == original
    assert saved["sha256"] == hashlib.sha256(original).hexdigest()
    assert sidecar.read_bytes() == transcript and db.read_bytes() == b"synthetic database sentinel"
    assert retention.restore(root, bundle, offline_marker=marker) == [saved["path"]]
    assert journal.read_bytes() == original
    with pytest.raises(FileExistsError):
        retention.restore(root, bundle, offline_marker=marker)
    assert journal.read_bytes() == original
    assert not list(journal.parent.glob(".restore-*"))


@pytest.mark.parametrize("mutate,reason", [
    (lambda r: r.clear(), "incomplete_journal"),
    (lambda r: r.__delitem__(slice(1, None)), "not_finished"),
    (lambda r: r[1].update(version=99), "unknown_journal_schema"),
    (lambda r: r[1].update(seq=7), "journal_identity_or_sequence"),
    (lambda r: r[1].update(payload={}), "missing_final_snapshot"),
    (lambda r: r[1].update(event="cancel", type="cancel"), "non_success_terminal"),
    (lambda r: r[2].update(created_at=time.time()), "inside_retention_window"),
])
def test_running_unknown_and_new_excluded(state, mutate, reason):
    change_rows(state, mutate)
    result = run(state, apply=True)
    assert entry(result)["reason"] == reason
    assert state[1].exists()


@pytest.mark.parametrize("value", ["run", "other-run", {}, ""])
def test_any_active_or_unknown_stream_retained(state, value):
    side = json.loads(state[2].read_text())
    side["active_stream_id"] = value
    state[2].write_text(json.dumps(side))
    assert entry(run(state, apply=True))["reason"] == "active_or_unknown_stream"
    assert state[1].exists()


def test_persisted_final_must_match(state):
    side = json.loads(state[2].read_text())
    side["messages"][-1]["content"] = "not saved"
    state[2].write_text(json.dumps(side))
    assert entry(run(state))["reason"] == "persisted_result_mismatch"


def test_corrupt_and_new_file(state):
    os.utime(state[1], None)
    assert entry(run(state))["reason"] == "inside_retention_window"
    state[1].write_bytes(b"not json\n")
    old = time.time() - 40 * 86400
    os.utime(state[1], (old, old))
    assert entry(run(state))["reason"] == "corrupt_json"


def test_file_replaced_between_backup_and_unlink(state, monkeypatch):
    original = retention.atomic_write
    replacement = b"new writer content must survive\n"
    def swap(fd, name, raw):
        original(fd, name, raw)
        if name.endswith(".jsonl"):
            temp = state[1].with_suffix(".replacement")
            temp.write_bytes(replacement)
            temp.replace(state[1])
    monkeypatch.setattr(retention, "atomic_write", swap)
    assert entry(run(state, apply=True))["reason"] == "identity_changed"
    assert state[1].read_bytes() == replacement


def test_sidecar_replaced_between_backup_and_unlink(state, monkeypatch):
    original = retention.atomic_write
    def swap(fd, name, raw):
        original(fd, name, raw)
        if name.endswith(".jsonl"):
            state[2].write_bytes(b"{}")
    monkeypatch.setattr(retention, "atomic_write", swap)
    assert entry(run(state, apply=True))["reason"] == "identity_changed"
    assert state[1].exists()


def test_backup_failure_keeps_source(state, monkeypatch):
    original = retention.atomic_write
    raw = state[1].read_bytes()
    def fail(fd, name, data):
        if name.endswith(".jsonl"):
            raise OSError("synthetic full disk")
        return original(fd, name, data)
    monkeypatch.setattr(retention, "atomic_write", fail)
    assert entry(run(state, apply=True))["reason"] == "OSError"
    assert state[1].read_bytes() == raw


@pytest.mark.parametrize("target", [1, 2])
def test_symlink_files_retained(state, target):
    path = state[target]
    real = path.with_suffix(".real")
    path.rename(real)
    path.symlink_to(real)
    result = run(state, apply=True)
    assert entry(result)["status"] == "skipped"
    assert path.is_symlink() and real.exists() and state[1].exists()


def test_symlink_ancestor_rejected(state, tmp_path):
    alias = tmp_path / "alias"
    alias.symlink_to(state[0], target_is_directory=True)
    with pytest.raises(OSError):
        retention.maintain(alias)


def test_bounds_offline_and_backup_confinement(state):
    root, journal, _, backup, marker = state
    with pytest.raises(retention.Unsafe, match="offline_marker_required"):
        retention.maintain(root, apply=True, backup_dir=backup)
    with pytest.raises(retention.Unsafe, match="outside_session_tree"):
        retention.maintain(root, apply=True, backup_dir=root / "backup", offline_marker=marker)
    with pytest.raises(retention.Unsafe, match="seven_day"):
        run(state, days=1)
    assert run(state, max_entries=1)["truncated"]
    assert entry(run(state, max_total_bytes=1))["reason"] == "total_byte_limit"
    assert journal.exists()


def test_recovery_rejects_corrupt_backup(state):
    root, _, _, _, marker = state
    result = run(state, apply=True)
    bundle = Path(result["bundle"])
    (bundle / entry(result)["backup"]).write_bytes(b"corrupt")
    with pytest.raises(retention.Unsafe, match="backup_verification_failed"):
        retention.restore(root, bundle, offline_marker=marker)
    assert not state[1].exists()


def test_cli_defaults_and_apply_denied_without_marker(state, capsys):
    assert retention.main(["--session-dir", str(state[0])]) == 0
    assert json.loads(capsys.readouterr().out)["mode"] == "dry-run"
    assert retention.main(["--session-dir", str(state[0]), "--apply", "--backup-dir", str(state[3])]) == 2
    assert json.loads(capsys.readouterr().out)["error"] == "offline_marker_required"
    assert state[1].exists()


def test_unknown_event_retained(state):
    change_rows(state, lambda rows: rows[0].update(event="future_event", type="future_event"))
    assert entry(run(state))["reason"] == "unknown_event_schema"


def test_hardlink_excluded(state, tmp_path):
    os.link(state[1], tmp_path / "duplicate")
    assert entry(run(state, apply=True))["reason"] == "not_single_link_regular_file"
    assert state[1].exists()


@pytest.mark.parametrize("edit", [
    lambda m: m.update(webui_stopped=False),
    lambda m: m.update(automatic_restarts_disabled=False),
    lambda m: m.update(created_at=time.time() - 7200),
    lambda m: m.update(inode=0),
])
def test_invalid_offline_attestation_denies_apply(state, edit):
    marker = state[4]
    value = json.loads(marker.read_text())
    edit(value)
    marker.write_text(json.dumps(value))
    with pytest.raises(retention.Unsafe, match="invalid_or_expired"):
        run(state, apply=True)
    assert state[1].exists() and not list(state[3].iterdir())


def test_directory_replaced_before_unlink(state, monkeypatch):
    original = retention.atomic_write
    old_parent = state[1].parent.with_name("old-parent")
    def swap(fd, name, raw):
        original(fd, name, raw)
        if name.endswith(".jsonl"):
            state[1].parent.rename(old_parent)
            state[1].parent.mkdir()
            state[1].write_bytes(b"replacement")
    monkeypatch.setattr(retention, "atomic_write", swap)
    assert entry(run(state, apply=True))["reason"] == "directory_identity_changed"
    assert state[1].read_bytes() == b"replacement"
    assert (old_parent / "run.jsonl").exists()


def test_restore_manifest_traversal_rejected(state):
    result = run(state, apply=True)
    bundle = Path(result["bundle"])
    manifest = json.loads((bundle / "manifest.json").read_text())
    entry(manifest)["path"] = "_run_journal/../state.db.jsonl"
    (bundle / "manifest.json").write_text(json.dumps(manifest))
    with pytest.raises(retention.Unsafe, match="unsafe_restore_path"):
        retention.restore(state[0], bundle, offline_marker=state[4])

@pytest.mark.parametrize("events,eligible", [
    (["token", "done", "metering", "title_status", "title", "stream_end"], True),
    (["reasoning", "tool", "tool_complete", "token", "done", "stream_end"], True),
    (["token"], False),
    (["token", "done"], False),
    (["token", "stream_end"], False),
    (["token", "cancel"], False),
    (["token", "error", "stream_end"], False),
    (["token", "apperror"], False),
    (["token", "done", "stream_end", "title_status"], False),
])
def test_actual_writer_lifecycle(state, monkeypatch, events, eligible):
    root, journal, sidecar, _, _ = state
    side = json.loads(sidecar.read_text())
    old = journal.stat().st_mtime
    assert run_journal.delete_run_journal("session", session_dir=root)
    writer = run_journal.RunJournalWriter("session", "run", session_dir=root)
    with monkeypatch.context() as clock:
        for seq, event in enumerate(events):
            clock.setattr(run_journal.time, "time", lambda seq=seq: old + seq)
            writer.append_sse_event(event, {"session": side} if event == "done" else {})
    original = journal.read_bytes()
    os.utime(journal, (old, old))
    result = run(state, apply=True)
    assert (entry(result)["status"] == "removed") is eligible
    if not eligible:
        assert journal.read_bytes() == original


def test_actual_writer_tool_limit_is_not_success(state, monkeypatch):
    root, journal, sidecar, _, _ = state
    old = journal.stat().st_mtime
    run_journal.delete_run_journal("session", session_dir=root)
    writer = run_journal.RunJournalWriter("session", "run", session_dir=root)
    with monkeypatch.context() as clock:
        clock.setattr(run_journal.time, "time", lambda: old)
        writer.append_sse_event("done", {"session": json.loads(sidecar.read_text()),
                                         "terminal_state": "tool_limit_reached"})
        writer.append_sse_event("stream_end", {})
    os.utime(journal, (old, old))
    assert run_journal.latest_run_summary("session", "run", session_dir=root)["terminal_state"] == "tool_limit_reached"
    assert entry(run(state, apply=True))["reason"] == "ambiguous_terminal"
    assert journal.exists()


@pytest.mark.parametrize("flag", ["_error", "_partial", "_cancelled"])
def test_error_or_partial_message_is_not_persisted_success(state, flag):
    change_rows(state, lambda rows: rows[1]["payload"]["session"]["messages"][-1].update({flag: True}))
    assert entry(run(state, apply=True))["reason"] == "missing_final_assistant"
    assert state[1].exists()


def test_preunlink_manifest_failure_keeps_source(state, monkeypatch):
    original = retention.save_manifest
    source = state[1].read_bytes()
    failed = False
    def fail_once(fd, manifest):
        nonlocal failed
        if not failed and any(e["status"] == "backed_up" for e in manifest["entries"]):
            failed = True
            raise OSError("synthetic manifest fsync failure")
        return original(fd, manifest)
    monkeypatch.setattr(retention, "save_manifest", fail_once)
    result = run(state, apply=True)
    assert failed and entry(result)["status"] == "backed_up_check_source"
    assert state[1].read_bytes() == source


def test_backup_and_manifest_fsynced_before_unlink(state, monkeypatch):
    synced = set()
    original_fsync, original_unlink = os.fsync, os.unlink
    checked = False
    def fsync(fd):
        original_fsync(fd)
        st = os.fstat(fd)
        synced.add((st.st_dev, st.st_ino))
    def unlink(name, *args, **kwargs):
        nonlocal checked
        if name == "run.jsonl":
            bundle = next(state[3].iterdir())
            manifest_path = bundle / "manifest.json"
            manifest = json.loads(manifest_path.read_text())
            saved = entry(manifest)
            assert saved["status"] == "backed_up"
            blob = bundle / saved["backup"]
            assert blob.read_bytes() == state[1].read_bytes()
            for path in (blob, manifest_path, bundle, state[3]):
                st = path.stat()
                assert (st.st_dev, st.st_ino) in synced
            checked = True
        return original_unlink(name, *args, **kwargs)
    monkeypatch.setattr(os, "fsync", fsync)
    monkeypatch.setattr(os, "unlink", unlink)
    assert entry(run(state, apply=True))["status"] == "removed"
    assert checked


def test_missing_all_workers_attestation_rejected(state):
    marker = json.loads(state[4].read_text())
    marker.pop("all_workers_stopped")
    state[4].write_text(json.dumps(marker))
    with pytest.raises(retention.Unsafe, match="invalid_or_expired"):
        run(state, apply=True)
    assert state[1].exists()


@pytest.mark.parametrize("messages", [[], [{"role": "assistant", "content": "bounded"}]])
def test_incompatible_snapshot_retained(state, messages):
    change_rows(state, lambda rows: rows[1]["payload"]["session"].update(messages=messages))
    assert entry(run(state, apply=True))["status"] == "skipped"
    assert state[1].exists()
