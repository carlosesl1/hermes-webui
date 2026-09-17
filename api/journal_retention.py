"""Opt-in, offline-only journal maintenance. Never imported by runtime startup.

This module intentionally does not import models or resolve configured state.
Runtime journal locks are process-local: an operator-enforced outage, including
all workers and automatic restarts, is a prerequisite for apply and restore.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import re
import stat
import time
import uuid

ROOT = "_run_journal"
SAFE_ID = re.compile(r"[A-Za-z0-9_-][A-Za-z0-9_.-]{0,199}\Z")
DEFAULT_DAYS = 30
MIN_RECONNECT_DAYS = 7
MAX_FILE_BYTES = 64 * 1024 * 1024
MAX_TOTAL_BYTES = 256 * 1024 * 1024
MAX_ENTRIES = 1000


class Unsafe(ValueError):
    """Fail-closed reason, safe to include in the manifest."""


def identity(st):
    return (st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns, st.st_ctime_ns)


def absolute(path):
    p = Path(path)
    if not p.is_absolute() or ".." in p.parts:
        raise Unsafe("absolute_non_traversing_path_required")
    return p


@contextmanager
def directory(path):
    """Walk every component via retained descriptors, never following links."""
    path = absolute(path)
    fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in path.parts[1:]:
            nxt = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = nxt
        yield fd
    finally:
        os.close(fd)


@contextmanager
def child_directory(fd, name):
    child = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
    try:
        yield child
    finally:
        os.close(child)


def read_regular(fd, name, limit=MAX_FILE_BYTES, expected=None):
    handle = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
    with os.fdopen(handle, "rb") as stream:
        before = os.fstat(stream.fileno())
        if expected is not None and identity(before) != tuple(expected):
            raise Unsafe("identity_changed")
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
            raise Unsafe("not_single_link_regular_file")
        if before.st_size > limit:
            raise Unsafe("file_byte_limit")
        raw = stream.read(limit + 1)
        if len(raw) > limit:
            raise Unsafe("file_byte_limit")
        if identity(before) != identity(os.fstat(stream.fileno())):
            raise Unsafe("identity_changed")
        recheck(fd, name, identity(before))
        return raw, identity(before)


def recheck(fd, name, expected):
    st = os.stat(name, dir_fd=fd, follow_symlinks=False)
    if not stat.S_ISREG(st.st_mode) or st.st_nlink != 1 or identity(st) != tuple(expected):
        raise Unsafe("identity_changed")


def strict_json(raw):
    def pairs(items):
        out = {}
        for key, val in items:
            if key in out:
                raise Unsafe("duplicate_json_key")
            out[key] = val
        return out
    try:
        return json.loads(raw, object_pairs_hook=pairs,
                          parse_constant=lambda _: (_ for _ in ()).throw(Unsafe("nonfinite_json")))
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise Unsafe("corrupt_json") from exc


def timestamp(value):
    if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
        raise Unsafe("unknown_timestamp")
    return value


def eligibility(raw, side_raw, sid, rid, cutoff):
    """Only full v1 successful done snapshots with independent persisted proof.

    Errors, cancels, transport-only terminals, compressed/redacted mismatches,
    and partial settlement payloads are deliberately retained.
    """
    if not raw.endswith(b"\n"):
        raise Unsafe("incomplete_journal")
    rows = raw.splitlines()
    if not rows or len(rows) > 100000:
        raise Unsafe("row_limit_or_empty")
    done = None
    first_at = None
    ended = False
    for seq, line in enumerate(rows, 1):
        if ended:
            raise Unsafe("activity_after_stream_end")
        event = strict_json(line)
        if not isinstance(event, dict) or event.get("version") != 1:
            raise Unsafe("unknown_journal_schema")
        if (type(event.get("seq")) is not int or event["seq"] != seq
                or event.get("session_id") != sid or event.get("run_id") != rid
                or event.get("event_id") != f"{rid}:{seq}"):
            raise Unsafe("journal_identity_or_sequence")
        created = timestamp(event.get("created_at"))
        first_at = created if first_at is None else first_at
        if created >= cutoff:
            raise Unsafe("inside_retention_window")
        name = event.get("event")
        if not isinstance(name, str) or event.get("type") != name:
            raise Unsafe("unknown_event_schema")
        if name not in {"token", "thinking", "tool_start", "tool_end", "tool_call", "tool_result",
                        "status", "usage", "metering", "start", "title", "done", "stream_end",
                        "reasoning", "tool", "tool_complete", "title_status", "context_status",
                        "interim_assistant", "state_saved", "cancel", "error", "apperror"}:
            raise Unsafe("unknown_event_schema")
        if event.get("terminal") is False and event.get("terminal_state") is not None:
            raise Unsafe("unknown_terminal")
        if name in {"cancel", "error", "apperror"}:
            raise Unsafe("non_success_terminal")
        if name == "done":
            if done is not None or event.get("terminal") is not True or event.get("terminal_state") != "completed":
                raise Unsafe("ambiguous_terminal")
            done = event
        elif done is not None and name not in {"metering", "title", "title_status", "stream_end"}:
            raise Unsafe("activity_after_done")
        elif event.get("terminal") is not False and name != "stream_end":
            raise Unsafe("unknown_terminal")
        if name == "stream_end" and (event.get("terminal") is not True or event.get("terminal_state") != "completed"):
            raise Unsafe("unknown_terminal")
        ended = name == "stream_end"
    if done is None or name != "stream_end":
        raise Unsafe("not_finished")
    side = strict_json(side_raw)
    if not isinstance(side, dict) or side.get("session_id") != sid:
        raise Unsafe("unknown_sidecar")
    # Reject even a different active run: conservative session-wide exclusion.
    if "active_stream_id" not in side or side["active_stream_id"] is not None:
        raise Unsafe("active_or_unknown_stream")
    if side.get("pending_user_message") or side.get("pending_started_at"):
        raise Unsafe("pending_turn")
    payload = done.get("payload")
    snapshot = payload.get("session") if isinstance(payload, dict) else None
    if not isinstance(snapshot, dict) or snapshot.get("session_id") != sid:
        raise Unsafe("missing_final_snapshot")
    messages = snapshot.get("messages")
    persisted = side.get("messages")
    if not isinstance(messages, list) or not messages or not isinstance(persisted, list):
        raise Unsafe("missing_persisted_result")
    final = messages[-1]
    if (not isinstance(final, dict) or final.get("role") != "assistant"
            or not final.get("content") or final.get("tool_calls")
            or any(final.get(flag) for flag in ("is_error", "error", "_error", "_partial", "_cancelled"))):
        raise Unsafe("missing_final_assistant")
    ts = timestamp(final.get("timestamp"))
    if not first_at <= ts <= done["created_at"]:
        raise Unsafe("unproven_result_owner")
    if persisted[:len(messages)] != messages:
        raise Unsafe("persisted_result_mismatch")


def outside(source, target):
    source, target = absolute(source), absolute(target)
    if source == target or source in target.parents or target in source.parents:
        raise Unsafe("backup_must_be_outside_session_tree")


@contextmanager
def offline(marker, source, root_fd):
    if marker is None:
        raise Unsafe("offline_marker_required")
    marker = absolute(marker)
    outside(source, marker)
    with directory(marker.parent) as parent:
        fd = os.open(marker.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
        try:
            st = os.fstat(fd)
            if not stat.S_ISREG(st.st_mode) or st.st_nlink != 1 or st.st_uid != os.getuid() or st.st_mode & 0o077:
                raise Unsafe("unsafe_offline_marker")
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            data, sig = read_regular(parent, marker.name, 4096)
            if identity(st) != sig:
                raise Unsafe("identity_changed")
            record = strict_json(data)
            root_st = os.fstat(root_fd)
            if (not isinstance(record, dict) or record.get("webui_stopped") is not True
                    or record.get("all_workers_stopped") is not True
                    or record.get("automatic_restarts_disabled") is not True
                    or record.get("session_dir") != str(source)
                    or record.get("device") != root_st.st_dev or record.get("inode") != root_st.st_ino
                    or not 0 <= time.time() - timestamp(record.get("created_at")) <= 3600):
                raise Unsafe("invalid_or_expired_offline_marker")
            yield
        finally:
            os.close(fd)


def atomic_write(fd, name, raw):
    temp = f".partial-{uuid.uuid4().hex}"
    handle = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=fd)
    try:
        with os.fdopen(handle, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, name, src_dir_fd=fd, dst_dir_fd=fd)
        os.fsync(fd)
        verified, _ = read_regular(fd, name, max(len(raw), 1))
        if verified != raw:
            raise Unsafe("backup_verification_failed")
    finally:
        try:
            os.unlink(temp, dir_fd=fd)
        except FileNotFoundError:
            pass


def save_manifest(fd, manifest):
    atomic_write(fd, "manifest.json", json.dumps(manifest, indent=2).encode())


def maintain(session_dir, *, days=DEFAULT_DAYS, apply=False, backup_dir=None,
             offline_marker=None, max_entries=MAX_ENTRIES, max_total_bytes=MAX_TOTAL_BYTES):
    source = absolute(session_dir)
    if type(days) not in (int, float) or not math.isfinite(days) or days < MIN_RECONNECT_DAYS:
        raise Unsafe("minimum_seven_day_reconnect_window")
    if not 1 <= max_entries <= MAX_ENTRIES or not 1 <= max_total_bytes <= MAX_TOTAL_BYTES:
        raise Unsafe("invalid_inventory_limits")
    cutoff = time.time() - days * 86400
    manifest = {"version": 1, "session_dir": str(source), "mode": "apply" if apply else "dry-run",
                "days": days, "entries": [], "truncated": False,
                "rollback": "Keep WebUI stopped; use restore() or docs/journal-retention.md. Never overwrite an existing journal."}
    with directory(source) as root_fd:
        if not apply:
            _inventory(root_fd, cutoff, manifest, None, max_entries, max_total_bytes)
        else:
            if backup_dir is None:
                raise Unsafe("backup_directory_required")
            backup = absolute(backup_dir)
            outside(source, backup)
            with offline(offline_marker, source, root_fd), directory(backup) as backup_fd:
                st = os.fstat(backup_fd)
                if st.st_uid != os.getuid() or st.st_mode & 0o077:
                    raise Unsafe("backup_directory_must_be_private")
                # Also serialize invocations using different offline markers.
                fcntl.flock(root_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                bundle = f"retention-{uuid.uuid4().hex}"
                os.mkdir(bundle, 0o700, dir_fd=backup_fd)
                os.fsync(backup_fd)
                manifest["bundle"] = str(backup / bundle)
                with child_directory(backup_fd, bundle) as bundle_fd:
                    save_manifest(bundle_fd, manifest)
                    _inventory(root_fd, cutoff, manifest, bundle_fd, max_entries, max_total_bytes)
                    save_manifest(bundle_fd, manifest)
    return manifest


def _inventory(root_fd, cutoff, manifest, bundle_fd, max_entries, byte_budget):
    used = 0
    visited = 0
    with child_directory(root_fd, ROOT) as journal_fd, os.scandir(journal_fd) as sessions:
        for session in sessions:
            if visited >= max_entries:
                manifest["truncated"] = True
                return
            visited += 1
            sid = session.name
            if not SAFE_ID.fullmatch(sid) or not session.is_dir(follow_symlinks=False):
                manifest["entries"].append({"path": f"{ROOT}/{sid}", "status": "skipped", "reason": "unsafe_session_directory"})
                continue
            with child_directory(journal_fd, sid) as session_fd, os.scandir(session_fd) as files:
                for item in files:
                    if visited >= max_entries:
                        manifest["truncated"] = True
                        return
                    visited += 1
                    name = item.name
                    entry = {"path": f"{ROOT}/{sid}/{name}", "status": "skipped"}
                    manifest["entries"].append(entry)
                    try:
                        if not name.endswith(".jsonl") or not SAFE_ID.fullmatch(name[:-6]):
                            raise Unsafe("not_run_journal")
                        rid = name[:-6]
                        st = item.stat(follow_symlinks=False)
                        if not stat.S_ISREG(st.st_mode) or st.st_nlink != 1:
                            raise Unsafe("not_single_link_regular_file")
                        if st.st_mtime >= cutoff:
                            raise Unsafe("inside_retention_window")
                        side_st = os.stat(f"{sid}.json", dir_fd=root_fd, follow_symlinks=False)
                        if st.st_size > MAX_FILE_BYTES or side_st.st_size > MAX_FILE_BYTES:
                            raise Unsafe("file_byte_limit")
                        cost = st.st_size + side_st.st_size
                        if used + cost > byte_budget:
                            manifest["truncated"] = True
                            raise Unsafe("total_byte_limit")
                        used += cost
                        raw, sig = read_regular(session_fd, name, expected=identity(st))
                        side, side_sig = read_regular(root_fd, f"{sid}.json", expected=identity(side_st))
                        eligibility(raw, side, sid, rid, cutoff)
                        entry.update(status="eligible", bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest(), identity=sig)
                        if bundle_fd is not None:
                            blob = f"{visited}.jsonl"
                            atomic_write(bundle_fd, blob, raw)
                            entry.update(status="backed_up", backup=blob)
                            # Durable recovery record exists BEFORE any source unlink.
                            save_manifest(bundle_fd, manifest)
                            # Reopen every directory without following symlinks: retained
                            # descriptors must still designate the selected source tree.
                            with directory(manifest["session_dir"]) as check_root:
                                with child_directory(check_root, ROOT) as check_journals:
                                    with child_directory(check_journals, sid) as check_session:
                                        for held, current in ((root_fd, check_root), (journal_fd, check_journals),
                                                              (session_fd, check_session)):
                                            if identity(os.fstat(held))[:2] != identity(os.fstat(current))[:2]:
                                                raise Unsafe("directory_identity_changed")
                            recheck(root_fd, f"{sid}.json", side_sig)
                            recheck(session_fd, name, sig)
                            os.unlink(name, dir_fd=session_fd)
                            os.fsync(session_fd)
                            entry["status"] = "removed"
                            save_manifest(bundle_fd, manifest)
                    except (OSError, Unsafe) as exc:
                        # A backed-up entry remains restorable after an unlink/fsync failure.
                        entry["status"] = "skipped" if "backup" not in entry else "backed_up_check_source"
                        entry["reason"] = str(exc) if isinstance(exc, Unsafe) else type(exc).__name__
    manifest["bytes_inspected"] = used


def restore(session_dir, bundle, *, offline_marker):
    """Restore verified journal bytes only; refuse overwrite, traversal or links."""
    source, bundle = absolute(session_dir), absolute(bundle)
    outside(source, bundle)
    restored = []
    with directory(source) as root_fd, offline(offline_marker, source, root_fd), directory(bundle) as bundle_fd:
        fcntl.flock(root_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        raw, _ = read_regular(bundle_fd, "manifest.json", 2 * 1024 * 1024)
        manifest = strict_json(raw)
        if manifest.get("version") != 1 or manifest.get("session_dir") != str(source):
            raise Unsafe("restore_source_mismatch")
        entries = manifest.get("entries")
        if not isinstance(entries, list) or len(entries) > MAX_ENTRIES:
            raise Unsafe("invalid_restore_inventory")
        with child_directory(root_fd, ROOT) as journals:
            for entry in entries:
                if "backup" not in entry:
                    continue
                parts = entry["path"].split("/")
                blob = entry["backup"]
                if (len(parts) != 3 or parts[0] != ROOT or not SAFE_ID.fullmatch(parts[1])
                        or not parts[2].endswith(".jsonl") or not SAFE_ID.fullmatch(parts[2][:-6])
                        or not re.fullmatch(r"[0-9]+\.jsonl", blob)):
                    raise Unsafe("unsafe_restore_path")
                data, _ = read_regular(bundle_fd, blob)
                if len(data) != entry["bytes"] or hashlib.sha256(data).hexdigest() != entry["sha256"]:
                    raise Unsafe("backup_verification_failed")
                with child_directory(journals, parts[1]) as target:
                    # Create temporary, fsync and verify before atomic NO-REPLACE publication.
                    temp = f".restore-{uuid.uuid4().hex}"
                    try:
                        atomic_write(target, temp, data)
                        os.link(temp, parts[2], src_dir_fd=target, dst_dir_fd=target, follow_symlinks=False)
                        os.fsync(target)
                    finally:
                        try:
                            os.unlink(temp, dir_fd=target)
                        except FileNotFoundError:
                            pass
                    os.fsync(target)
                    restored.append(entry["path"])
    return restored


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--session-dir", required=True, help="Explicit absolute sidecar directory; never auto-discovered")
    parser.add_argument("--days", type=float, default=DEFAULT_DAYS, help="Default 30; minimum 7 days")
    parser.add_argument("--apply", action="store_true", help="OFFLINE ONLY; requires backup directory and attestation marker")
    parser.add_argument("--backup-dir")
    parser.add_argument("--offline-marker")
    parser.add_argument("--restore-bundle", help="Explicit offline rollback; requires --apply")
    args = parser.parse_args(argv)
    try:
        if args.restore_bundle:
            if not args.apply:
                raise Unsafe("restore_requires_apply")
            result = {"restored": restore(args.session_dir, args.restore_bundle, offline_marker=args.offline_marker)}
        else:
            result = maintain(args.session_dir, days=args.days, apply=args.apply,
                              backup_dir=args.backup_dir, offline_marker=args.offline_marker)
        print(json.dumps(result, indent=2))
        return 0
    except (Unsafe, OSError) as exc:
        print(json.dumps({"error": str(exc) if isinstance(exc, Unsafe) else type(exc).__name__}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
