"""Cursor replay resource regressions using real, bounded synthetic journals."""
import hashlib
import json
import os
import threading
import time
import tracemalloc
from concurrent.futures import ThreadPoolExecutor

import pytest

from api import run_journal


def _append(root, text, *, seq=None):
    return run_journal.append_run_event(
        "session", "run", "token", {"text": text}, session_dir=root, seq=seq,
    )


def _path(root):
    return root / "_run_journal" / "session" / "run.jsonl"


@pytest.mark.parametrize("after_seq,max_seq,expected", [
    (1198, None, [1199, 1200]),
    (1, 3, [2, 3]),
])
def test_cursor_replay_does_not_retain_discarded_payloads(tmp_path, after_seq, max_seq, expected):
    # About 10 MB on disk, created through the production writer. Only reader
    # allocations are traced; timing is reported, never used as a flaky gate.
    text = "x" * 8192
    for _ in range(1200):
        _append(tmp_path, text)
    path = _path(tmp_path)
    before = hashlib.sha256(path.read_bytes()).digest()
    tracemalloc.start()
    started = time.perf_counter()
    try:
        replay = run_journal.read_run_events(
            "session", "run", session_dir=tmp_path,
            after_seq=after_seq, max_seq=max_seq,
        )
        elapsed = time.perf_counter() - started
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    print(f"replay rows=1200 bytes={path.stat().st_size} after={after_seq} "
          f"max={max_seq} returned={len(replay['events'])} peak={peak} seconds={elapsed:.6f}")
    assert [event["seq"] for event in replay["events"]] == expected
    assert all(event["payload"]["text"] == text for event in replay["events"])
    assert replay["malformed"] == []
    assert hashlib.sha256(path.read_bytes()).digest() == before
    assert peak < 2_000_000, "cursor replay retained the discarded history prefix/suffix"


def test_cursor_filter_preserves_out_of_order_rows_and_all_malformed_diagnostics(tmp_path):
    _append(tmp_path, "first", seq=1)
    path = _path(tmp_path)
    with path.open("a", encoding="utf-8") as fh:
        fh.write("\n{bad-prefix}\n")
    high = _append(tmp_path, "outside", seq=8)
    wanted = _append(tmp_path, "selected", seq=3)
    with path.open("a", encoding="utf-8") as fh:
        fh.write("[\"bad-shape\"]\n{bad-suffix}\n")
    before = path.read_bytes()
    replay = run_journal.read_run_events(
        "session", "run", session_dir=tmp_path, after_seq=1, max_seq=3,
    )
    assert replay["events"] == [wanted]  # Do not break early at seq=8.
    assert replay["malformed"] == [
        {"line": 3, "raw": "{bad-prefix}"},
        {"line": 6, "raw": '["bad-shape"]'},
        {"line": 7, "raw": "{bad-suffix}"},
    ]
    full = run_journal.read_run_events("session", "run", session_dir=tmp_path)
    assert full["events"][1:] == [high, wanted]
    assert path.read_bytes() == before


def test_cursor_reads_are_fresh_after_append_rewrite_replace_delete_and_recreate(tmp_path):
    first = _append(tmp_path, "old")
    path = _path(tmp_path)

    def read():
        return run_journal.read_run_events("session", "run", session_dir=tmp_path, after_seq=0)

    assert read()["events"] == [first]
    second = _append(tmp_path, "two")
    assert read()["events"] == [first, second]
    stat = path.stat()
    path.write_bytes(path.read_bytes().replace(b'"old"', b'"new"'))
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    assert read()["events"][0]["payload"]["text"] == "new"
    replacement = path.with_suffix(".replacement")
    replacement.write_bytes(path.read_bytes().replace(b'"new"', b'"end"'))
    replacement.replace(path)
    assert read()["events"][0]["payload"]["text"] == "end"
    other_root = tmp_path / "other-profile"
    other = _append(other_root, "other")
    assert run_journal.read_run_events(
        "session", "run", session_dir=other_root, after_seq=0,
    )["events"] == [other]
    assert read()["events"][0]["payload"]["text"] == "end"
    saved = path.read_bytes()
    path.unlink()
    assert read()["events"] == []
    path.write_bytes(saved)
    assert read()["events"][0]["payload"]["text"] == "end"


@pytest.mark.parametrize("newline", [b"\n", b"\r\n", b"\r"])
def test_legacy_line_endings_utf8_and_unterminated_tail(tmp_path, newline):
    first = _append(tmp_path, "ação 中文")
    second = _append(tmp_path, "second")
    path = _path(tmp_path)
    path.write_bytes(newline.join(path.read_bytes().splitlines()) + newline + b"{partial")
    before = path.read_bytes()
    replay = run_journal.read_run_events(
        "session", "run", session_dir=tmp_path, after_seq=0,
    )
    assert replay["events"] == [first, second]
    assert replay["malformed"] == [{"line": 3, "raw": "{partial"}]
    assert path.read_bytes() == before


def test_reader_closes_descriptor_when_cursor_conversion_raises(tmp_path, monkeypatch):
    from pathlib import Path

    _append(tmp_path, "one")
    handles = []
    original_open = Path.open

    def record_open(path, *args, **kwargs):
        fh = original_open(path, *args, **kwargs)
        handles.append(fh)
        return fh

    monkeypatch.setattr(Path, "open", record_open)
    with pytest.raises(ValueError):
        run_journal.read_run_events("session", "run", session_dir=tmp_path, after_seq="bad")
    assert handles and all(handle.closed for handle in handles)


@pytest.mark.parametrize("mutation", ["append", "replace"])
def test_replay_uses_one_open_file_snapshot_during_concurrent_mutation(tmp_path, monkeypatch, mutation):
    first = _append(tmp_path, "one")
    second = _append(tmp_path, "two")
    path = _path(tmp_path)
    replacement = path.with_suffix(".replacement")
    replacement.write_bytes(path.read_bytes().replace(b'"two"', b'"new"'))
    reading = threading.Event()
    mutated = threading.Event()
    original_loads = json.loads
    blocked = False

    def pause_first_decode(*args, **kwargs):
        nonlocal blocked
        if not blocked:
            blocked = True
            reading.set()
            assert mutated.wait(5), "writer never completed"
        return original_loads(*args, **kwargs)

    def mutate():
        assert reading.wait(5), "reader never opened journal"
        try:
            if mutation == "append":
                _append(tmp_path, "three")
            else:
                replacement.replace(path)
        finally:
            mutated.set()

    with monkeypatch.context() as patch:
        patch.setattr(run_journal.json, "loads", pause_first_decode)
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(mutate)
            replay = run_journal.read_run_events(
                "session", "run", session_dir=tmp_path, after_seq=0,
            )
            future.result(timeout=5)
    assert replay["events"] == [first, second]
    refreshed = run_journal.read_run_events(
        "session", "run", session_dir=tmp_path, after_seq=1,
    )["events"]
    assert [event["payload"]["text"] for event in refreshed] == (
        ["two", "three"] if mutation == "append" else ["new"]
    )
