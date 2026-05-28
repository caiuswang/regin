"""Smoke tests for `regin logs ...` CLI subcommands.

After the single-stream switch, `--feature` is a filter (not a
positional arg); `tail`/`grep`/`prune`/`path` operate on the one
`regin.log` file.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from typer.testing import CliRunner

from lib import activity_log
from cli.commands.logs import logs_app


@pytest.fixture
def configured_logs(monkeypatch, tmp_path) -> Path:
    monkeypatch.setenv("REGIN_ACTIVITY_LOG_DIR", str(tmp_path))
    monkeypatch.setenv("REGIN_LOG_LEVEL", "DEBUG")
    monkeypatch.setattr(activity_log, "_CONFIGURED", False)
    monkeypatch.setattr(activity_log, "_HANDLER_ID", None)
    monkeypatch.setattr(activity_log, "_WARNED_FEATURES", set())
    activity_log.configure_activity_log(log_dir=tmp_path, enqueue=False, force=True)
    return tmp_path


def _seed(feature: str, message: str, **fields) -> None:
    activity_log.get_activity_logger(feature).write(message, **fields)


def _seed_error(feature: str, message: str, **fields) -> None:
    activity_log.get_activity_logger(feature).error(message, **fields)


# ── list ────────────────────────────────────────────────────

def test_list_empty_dir_reports_no_logs(monkeypatch, tmp_path):
    monkeypatch.setenv("REGIN_ACTIVITY_LOG_DIR", str(tmp_path))
    monkeypatch.setattr(activity_log, "_CONFIGURED", False)
    monkeypatch.setattr(activity_log, "_HANDLER_ID", None)
    activity_log.configure_activity_log(log_dir=tmp_path, enqueue=False, force=True)
    result = CliRunner().invoke(logs_app, ["list"])
    assert result.exit_code == 0
    assert "no activity logs yet" in result.stdout


def test_list_shows_per_feature_counts(configured_logs):
    _seed("patterns", "imported", slug="p")
    _seed("patterns", "imported", slug="q")
    _seed("hooks", "dispatched", handler="h")
    _seed_error("hooks", "handler_failed")
    result = CliRunner().invoke(logs_app, ["list"])
    assert result.exit_code == 0
    # Header columns
    assert "feature" in result.stdout
    assert "events" in result.stdout
    assert "errors" in result.stdout
    # Both features present with right counts
    assert "patterns" in result.stdout
    assert "hooks" in result.stdout
    # Find the hooks row and confirm event/error counts (column split is
    # whitespace-tolerant since the table uses ljust padding).
    import re as _re
    hooks_line = next(ln for ln in result.stdout.splitlines() if ln.startswith("hooks"))
    cells = _re.split(r"\s{2,}", hooks_line.rstrip())
    assert cells[0] == "hooks"
    assert cells[1] == "2"   # events
    assert cells[2] == "1"   # errors


# ── tail ────────────────────────────────────────────────────

def test_tail_pretty_prints_last_n(configured_logs):
    for i in range(10):
        _seed("patterns", "imported", seq=i)
    result = CliRunner().invoke(logs_app, ["tail", "-n", "3"])
    assert result.exit_code == 0
    assert result.stdout.count("imported") == 3


def test_tail_raw_emits_json(configured_logs):
    _seed("patterns", "imported", slug="p1")
    result = CliRunner().invoke(logs_app, ["tail", "-n", "1", "--raw"])
    assert result.exit_code == 0
    record = json.loads(result.stdout.strip())["record"]
    assert record["message"] == "imported"


def test_tail_filter_by_feature(configured_logs):
    _seed("patterns", "imported", slug="p1")
    _seed("hooks", "dispatched", handler="h1")
    _seed("patterns", "imported", slug="p2")
    result = CliRunner().invoke(logs_app, ["tail", "--feature", "patterns", "-n", "50"])
    assert result.exit_code == 0
    assert result.stdout.count("imported") == 2
    assert "dispatched" not in result.stdout


def test_tail_filter_by_level(configured_logs):
    _seed("patterns", "imported", slug="p1")
    _seed_error("patterns", "import_failed", slug="p2")
    result = CliRunner().invoke(logs_app, ["tail", "--level", "ERROR", "-n", "50"])
    assert result.exit_code == 0
    assert "import_failed" in result.stdout
    assert "imported" not in result.stdout


def test_tail_missing_log_exits_nonzero(configured_logs):
    log = configured_logs / "regin.log"
    if log.exists():
        log.unlink()
    result = CliRunner().invoke(logs_app, ["tail"])
    assert result.exit_code == 1


# ── grep ────────────────────────────────────────────────────

def test_grep_matches_substring(configured_logs):
    _seed("patterns", "imported", slug="match-me")
    _seed("patterns", "imported", slug="other")
    result = CliRunner().invoke(logs_app, ["grep", "match-me"])
    assert result.exit_code == 0
    assert "match-me" in result.stdout
    assert "other" not in result.stdout


def test_grep_filter_by_feature(configured_logs):
    _seed("patterns", "imported", slug="hunt")
    _seed("hooks", "dispatched", handler="hunt")
    result = CliRunner().invoke(logs_app, ["grep", "hunt", "--feature", "patterns"])
    assert result.exit_code == 0
    assert "patterns" in result.stdout
    assert "hooks" not in result.stdout


def test_grep_across_all_features_by_default(configured_logs):
    _seed("patterns", "imported", slug="hunt")
    _seed("hooks", "dispatched", handler="hunt")
    result = CliRunner().invoke(logs_app, ["grep", "hunt"])
    assert result.exit_code == 0
    # Both feature tags should appear in the pretty-printed output.
    assert "[patterns]" in result.stdout
    assert "[hooks]" in result.stdout


def test_grep_invalid_regex_exits_with_usage_error(configured_logs):
    _seed("patterns", "x")
    result = CliRunner().invoke(logs_app, ["grep", "(unclosed"])
    assert result.exit_code == 2


def test_grep_no_match_exits_nonzero(configured_logs):
    _seed("patterns", "imported", slug="alpha")
    result = CliRunner().invoke(logs_app, ["grep", "zzz"])
    assert result.exit_code == 1


# ── prune ───────────────────────────────────────────────────

def test_prune_dry_run_lists_and_keeps(configured_logs):
    _seed("patterns", "seed")
    archive = configured_logs / "regin.2024-01-01_00-00-00_000000.log"
    archive.write_text("old\n")
    os.utime(archive, (0, 0))  # epoch — definitely older than 14 days
    result = CliRunner().invoke(logs_app, ["prune", "--dry-run"])
    assert result.exit_code == 0
    assert "would delete" in result.stdout
    assert archive.exists()


def test_prune_actually_deletes(configured_logs):
    _seed("patterns", "seed")
    archive = configured_logs / "regin.2024-01-01_00-00-00_000000.log"
    archive.write_text("old\n")
    os.utime(archive, (0, 0))
    result = CliRunner().invoke(logs_app, ["prune", "--older-than-days", "1"])
    assert result.exit_code == 0
    assert "deleted" in result.stdout
    assert not archive.exists()


def test_prune_nothing_to_do(configured_logs):
    _seed("patterns", "seed")
    result = CliRunner().invoke(logs_app, ["prune"])
    assert result.exit_code == 0
    assert "nothing to prune" in result.stdout


# ── path ────────────────────────────────────────────────────

def test_path_prints_single_log_path(configured_logs):
    result = CliRunner().invoke(logs_app, ["path"])
    assert result.exit_code == 0
    assert result.stdout.strip() == str(configured_logs / "regin.log")
