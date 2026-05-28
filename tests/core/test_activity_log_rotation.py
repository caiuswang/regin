"""Rotation + prune tests for lib.activity_log.

loguru's own rotation is library-tested upstream; we don't re-verify
that. These tests cover the `prune()` helper that powers
`regin logs prune` for forced cleanup outside loguru's `retention=`.

After the single-stream switch, archives match `regin.*` (e.g.
`regin.2024-01-01_00-00-00_000000.log`) instead of per-feature names.
"""

from __future__ import annotations

import os
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
