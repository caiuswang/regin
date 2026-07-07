"""Rotation + prune tests for lib.activity_log.

loguru's own rotation is library-tested upstream; we don't re-verify
that. These tests cover the `prune()` helper that powers
`regin logs prune` for forced cleanup outside loguru's `retention=`.

After the single-stream switch, archives match `regin.*` (e.g.
`regin.2024-01-01_00-00-00_000000.log`) instead of per-feature names.
"""

from __future__ import annotations

import os
import threading
import time
from pathlib import Path

import pytest

from lib import activity_log


@pytest.fixture
def tmp_log_dir(monkeypatch, tmp_path) -> Path:
    monkeypatch.setenv("REGIN_ACTIVITY_LOG_DIR", str(tmp_path))
    monkeypatch.setattr(activity_log, "_CONFIGURED", False)
    monkeypatch.setattr(activity_log, "_HANDLER_ID", None)
    monkeypatch.setattr(activity_log, "_WARNED_FEATURES", set())
    activity_log.configure_activity_log(
        log_dir=tmp_path, enqueue=False, force=True,
    )
    return tmp_path


def _make_rotated_archive(dir_: Path, age_days: float, suffix: str = "") -> Path:
    """Create a fake rotated archive of regin.log with a stale mtime."""
    name = f"regin.2024-01-01_12-00-00_000000{suffix}.log"
    path = dir_ / name
    path.write_text("rotated content\n")
    mtime = time.time() - age_days * 86400
    os.utime(path, (mtime, mtime))
    return path


# ── Cutoff ────────────────────────────────────────────────

def test_prune_deletes_archives_older_than_cutoff(tmp_log_dir):
    activity_log.get_activity_logger("patterns").write("seed")  # creates regin.log
    old = _make_rotated_archive(tmp_log_dir, age_days=30)
    deleted = activity_log.prune(older_than_days=14)
    assert old in deleted
    assert not old.exists()


def test_prune_preserves_archives_inside_cutoff(tmp_log_dir):
    activity_log.get_activity_logger("patterns").write("seed")
    fresh = _make_rotated_archive(tmp_log_dir, age_days=3)
    deleted = activity_log.prune(older_than_days=14)
    assert fresh not in deleted
    assert fresh.exists()


def test_prune_never_touches_active_log(tmp_log_dir):
    log = activity_log.get_activity_logger("patterns")
    log.write("important")
    active = tmp_log_dir / "regin.log"
    # Backdate the active file to look ancient.
    stale = time.time() - 365 * 86400
    os.utime(active, (stale, stale))
    deleted = activity_log.prune(older_than_days=14)
    assert active not in deleted
    assert active.exists()


def test_prune_never_touches_lock_file(tmp_log_dir):
    """`regin.log.lock`'s mtime is only stamped at creation (nothing ever
    touches it again), so it looks "stale" under any real retention cutoff.
    Deleting it would let a process still holding a flock on the old inode
    race a process that reopens the freshly recreated path — exactly the
    cross-process rotation bug this module exists to close."""
    log = activity_log.get_activity_logger("patterns")
    log.write("important")
    lock = tmp_log_dir / "regin.log.lock"
    assert lock.exists()
    stale = time.time() - 365 * 86400
    os.utime(lock, (stale, stale))
    deleted = activity_log.prune(older_than_days=14)
    assert lock not in deleted
    assert lock.exists()


# ── Dry run ───────────────────────────────────────────────

def test_prune_dry_run_lists_without_deleting(tmp_log_dir):
    activity_log.get_activity_logger("patterns").write("seed")
    old = _make_rotated_archive(tmp_log_dir, age_days=30)
    deleted = activity_log.prune(older_than_days=14, dry_run=True)
    assert old in deleted
    assert old.exists()


# ── Multiple archives ─────────────────────────────────────

def test_prune_handles_multiple_archives(tmp_log_dir):
    activity_log.get_activity_logger("patterns").write("seed")
    a = _make_rotated_archive(tmp_log_dir, age_days=30, suffix="_a")
    b = _make_rotated_archive(tmp_log_dir, age_days=30, suffix="_b")
    deleted = activity_log.prune(older_than_days=14)
    assert set(deleted) == {a, b}
    assert not a.exists()
    assert not b.exists()


# ── Empty/missing dir ─────────────────────────────────────

def test_prune_on_empty_directory_returns_empty_list(tmp_log_dir):
    # No writes, no rotated archives.
    deleted = activity_log.prune(older_than_days=14)
    assert deleted == []


# ── Eager file creation ────────────────────────────────────
#
# loguru's own FileSink opens the destination file synchronously in
# __init__. Under `enqueue=True` (the production default), `write()` runs
# on a background thread, so if `_RotatingSink` deferred file creation to
# `write()` too, a command run immediately after `configure_activity_log()`
# (e.g. `regin logs tail` as the very first CLI invocation ever) could see
# no file at all. Reproduced against the real CLI: `logs tail` on a fresh
# dir with `enqueue=True` exited 1 ("no log yet") before this fix.

def test_rotating_sink_creates_file_on_construction_before_any_write(tmp_path):
    path = tmp_path / "regin.log"
    assert not path.exists()
    activity_log._RotatingSink(path, max_bytes=1000, retention_days=14)
    assert path.exists()


# ── Cross-process rotation race ───────────────────────────
#
# Each regin process (web server, CLI invocation, per-hook-event
# hook_manager subprocess) builds its own `_RotatingSink` pointed at the
# same `regin.log`. Two independent instances hammering the file across
# the rotation threshold reproduces the FileNotFoundError loguru's own
# rotation hit under concurrent processes (see `_RotatingSink`'s
# docstring): the `flock` must serialize the rename so neither sink ever
# stats a path the other just renamed away.

def test_rotating_sink_survives_concurrent_cross_process_style_writes(tmp_path):
    path = tmp_path / "regin.log"
    sink_a = activity_log._RotatingSink(path, max_bytes=500, retention_days=14)
    sink_b = activity_log._RotatingSink(path, max_bytes=500, retention_days=14)
    errors: list[BaseException] = []

    def hammer(sink):
        try:
            for i in range(200):
                sink.write(f"line-{i}\n" * 3)
        except BaseException as exc:
            errors.append(exc)

    t1 = threading.Thread(target=hammer, args=(sink_a,))
    t2 = threading.Thread(target=hammer, args=(sink_b,))
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    assert errors == []
    assert path.exists()
    assert list(tmp_path.glob("regin.*.log"))  # rotation happened under contention


# ── Glob-metacharacter-safe pruning ────────────────────────
#
# `_prune_archives` used to build its glob pattern from the log file's full
# absolute path (`glob.glob(f"{root}.*{ext}")`), so bracket characters in any
# ANCESTOR directory (e.g. pytest's own `tmp_path` fixture routinely produces
# dirs like `test_foo[case1]0`) got parsed as a character class instead of
# literal text, and the pattern silently matched nothing.

def test_rotating_sink_prunes_archives_when_log_dir_has_glob_metacharacters(tmp_path):
    log_dir = tmp_path / "weird[dir]"
    log_dir.mkdir()
    path = log_dir / "regin.log"
    path.write_text("current\n")

    stale_archive = log_dir / "regin.2020-01-01_00-00-00_000000.log"
    stale_archive.write_text("old\n")
    stale = time.time() - 365 * 86400
    os.utime(stale_archive, (stale, stale))

    sink = activity_log._RotatingSink(path, max_bytes=10_000, retention_days=14)
    sink._prune_archives()

    assert not stale_archive.exists()
    assert path.exists()
