"""Unit tests for lib.trace.file_history — snapshot parsing, version-map
diffing, and the lazy on-disk backup reader.
"""

from __future__ import annotations

import os

from lib.trace.file_history import (
    TrackedBackup,
    backup_path,
    diff_versions,
    parse_snapshot_rows,
    read_backup,
    rolled_back_files,
    version_map_for,
)


def _snap_row(message_id, tracked):
    return {
        "type": "file-history-snapshot",
        "snapshot": {"messageId": message_id, "trackedFileBackups": tracked},
    }


def test_parse_snapshot_rows():
    rows = [
        _snap_row("m1", {
            "/a.py": {"backupFileName": "h1@v1", "version": 1, "backupTime": "t"},
        }),
        _snap_row("m2", {}),  # empty tracked map is valid
        {"type": "other"},     # ignored
        _snap_row("m3", "bad"),  # malformed tracked -> skipped
    ]
    parsed = parse_snapshot_rows(rows)
    assert set(parsed) == {"m1", "m2"}
    assert parsed["m1"]["/a.py"] == TrackedBackup("h1@v1", 1, "t")
    assert parsed["m2"] == {}


def test_version_map_takes_highest_version():
    snapshots = {
        "m1": {"/a.py": TrackedBackup("h1@v1", 1, None)},
        "m2": {"/a.py": TrackedBackup("h1@v3", 3, None),
               "/b.py": TrackedBackup("h2@v1", 1, None)},
    }
    vm = version_map_for(["m1", "m2"], snapshots)
    assert vm["/a.py"].version == 3
    assert vm["/a.py"].backup_file_name == "h1@v3"
    assert vm["/b.py"].version == 1


def test_rolled_back_files_reports_changed_and_created():
    # abandoned branch advanced a.py to v3 and created c.py; b.py untouched.
    abandoned = {
        "/a.py": TrackedBackup("ha@v3", 3, None),
        "/b.py": TrackedBackup("hb@v1", 1, None),
        "/c.py": TrackedBackup("hc@v1", 1, None),
    }
    baseline = {
        "/a.py": TrackedBackup("ha@v1", 1, None),
        "/b.py": TrackedBackup("hb@v1", 1, None),  # same version -> not rolled back
    }
    out = rolled_back_files(abandoned, baseline)
    paths = {d["path"]: d for d in out}
    assert set(paths) == {"/a.py", "/c.py"}
    assert paths["/a.py"] == {"path": "/a.py", "before_ref": "ha@v3", "after_ref": "ha@v1"}
    # c.py existed only on the abandoned branch -> reverted to absent
    assert paths["/c.py"] == {"path": "/c.py", "before_ref": "hc@v1", "after_ref": None}


def test_read_backup_and_diff(tmp_path):
    sid = "sess-1"
    sdir = tmp_path / sid
    sdir.mkdir()
    (sdir / "ha@v1").write_text("line one\n", encoding="utf-8")
    (sdir / "ha@v3").write_text("line one\nline two\n", encoding="utf-8")

    assert backup_path(sid, "ha@v1", base_dir=str(tmp_path)) == os.path.join(
        str(tmp_path), sid, "ha@v1")
    assert read_backup(sid, "ha@v1", base_dir=str(tmp_path)) == "line one\n"
    assert read_backup(sid, None, base_dir=str(tmp_path)) is None
    assert read_backup(sid, "missing@v9", base_dir=str(tmp_path)) is None

    d = diff_versions(
        sid, {"path": "/a.py", "before_ref": "ha@v3", "after_ref": "ha@v1"},
        base_dir=str(tmp_path),
    )
    assert d["before_text"] == "line one\nline two\n"
    assert d["after_text"] == "line one\n"
    assert d["path"] == "/a.py"
