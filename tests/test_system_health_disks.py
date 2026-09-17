from types import SimpleNamespace

from api import system_health as health


def mount_fixture(monkeypatch, tmp_path, lines):
    path = tmp_path / "mountinfo"
    path.write_text("\n".join(lines))
    monkeypatch.setattr(health, "_PROC_MOUNTINFO", path)
    monkeypatch.setattr(health.Path, "is_dir", lambda self: str(self) != "/etc/hosts")


def test_lists_both_disks_without_bind_or_overlay_duplicates(monkeypatch, tmp_path):
    mount_fixture(monkeypatch, tmp_path, [
        "1 0 0:99 / / rw - overlay overlay rw",
        "2 0 8:1 /home/user/app /workspace rw - ext4 /dev/sda1 rw",
        "3 0 8:1 /home/user /home/user rw - ext4 /dev/sda1 rw",
        "4 0 8:17 / /data rw - ext4 /dev/sdb1 rw",
        "5 0 8:17 / /home/user/data rw - ext4 /dev/sdb1 rw",
        "6 0 8:17 /hosts /etc/hosts rw - ext4 /dev/sdb1 rw",
        "7 0 0:50 / /dev rw - tmpfs tmpfs rw",
        "8 0 7:0 / /snap/app rw - squashfs /dev/loop0 ro",
    ])
    calls = []
    def usage(path):
        calls.append(path)
        return SimpleNamespace(total=1000, used=600, free=350)
    monkeypatch.setattr(health.shutil, "disk_usage", usage)
    rows = health._disk_volumes()
    assert len(rows) == 2
    assert {r["device"] for r in rows} == {"/dev/sda1", "/dev/sdb1"}
    assert calls == ["/home/user", "/data"]
    assert all(r["percent"] == 60 and r["free_bytes"] == 350 for r in rows)


def test_mount_escapes_and_per_disk_failure(monkeypatch, tmp_path):
    mount_fixture(monkeypatch, tmp_path, [
        r"1 0 8:1 / /media/My\040disk rw - ext4 /dev/sda1 rw",
        "2 0 8:17 / /offline rw - ext4 /dev/sdb1 rw",
    ])
    def usage(path):
        if path == "/offline":
            raise PermissionError("do not expose this error")
        assert path == "/media/My disk"
        return SimpleNamespace(total=100, used=0, free=100)
    monkeypatch.setattr(health.shutil, "disk_usage", usage)
    rows = health._disk_volumes()
    assert rows[0]["percent"] == 0 and rows[0]["available"]
    assert rows[1]["percent"] is None and not rows[1]["available"]
    assert "do not expose" not in repr(rows)


def test_missing_mountinfo_uses_legacy_fallback(monkeypatch, tmp_path):
    monkeypatch.setattr(health, "_PROC_MOUNTINFO", tmp_path / "missing")
    assert health._disk_volumes() == []


def test_root_mount_preferred_and_bad_rows_ignored(monkeypatch, tmp_path):
    mount_fixture(monkeypatch, tmp_path, [
        "bad row", "1 0 8:1 /home /home rw - ext4 /dev/sda1 rw",
        "2 0 8:1 / / rw - ext4 /dev/sda1 rw",
        "3 0 0:90 / /net rw - nfs server:/secret rw",
    ])
    monkeypatch.setattr(health.shutil, "disk_usage", lambda path: SimpleNamespace(total=100, used=20, free=80))
    assert [d["mountpoint"] for d in health._disk_volumes()] == ["/"]


def test_tries_other_bind_if_preferred_path_disappears(monkeypatch, tmp_path):
    mount_fixture(monkeypatch, tmp_path, [
        "1 0 8:1 / /data rw - ext4 /dev/sda1 rw",
        "2 0 8:1 / /longer/data rw - ext4 /dev/sda1 rw",
    ])
    def usage(path):
        if path == "/data":
            raise FileNotFoundError(path)
        return SimpleNamespace(total=100, used=20, free=80)
    monkeypatch.setattr(health.shutil, "disk_usage", usage)
    assert health._disk_volumes()[0]["mountpoint"] == "/longer/data"


def test_inaccessible_mount_does_not_hide_other_disks(monkeypatch, tmp_path):
    mount_fixture(monkeypatch, tmp_path, [
        "1 0 8:1 / /private rw - ext4 /dev/sda1 rw",
        "2 0 8:1 / /home rw - ext4 /dev/sda1 rw",
        "3 0 8:17 / /data rw - ext4 /dev/sdb1 rw",
    ])
    def is_dir(path):
        if str(path) == "/private":
            raise PermissionError("private")
        return True
    monkeypatch.setattr(health.Path, "is_dir", is_dir)
    monkeypatch.setattr(health.shutil, "disk_usage", lambda path: SimpleNamespace(total=100, used=20, free=80))
    assert len(health._disk_volumes()) == 2
