"""Characterization of `backfill_disk_proposals_to_orm`: which on-disk
proposal directories become `proposal_runs` rows, and with what state."""

from __future__ import annotations

import json

from lib.topics import topic_dir
from lib.topics.proposal_orm import orm_create_proposal_run, orm_list_proposal_runs
from lib.topics.proposals import backfill_disk_proposals_to_orm


def _write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def test_backfill_with_no_proposals_dir_scans_nothing(fake_git_repo):
    assert backfill_disk_proposals_to_orm(fake_git_repo) == {"scanned": 0, "imported": 0}


def test_backfill_imports_each_disk_run_once(fake_git_repo):
    base = topic_dir(fake_git_repo) / "proposals"
    _write(base / "run-a" / "topics.json",
           json.dumps({"provider": "claude", "topics": []}))
    _write(base / "run-a" / "wiki.md", "# wiki\n")
    _write(base / "run-b" / "status.json",
           json.dumps({"agent": "codex", "state": "failed"}))
    _write(base / "run-c" / "topics.json",
           json.dumps({"provider": "claude", "topics": []}))
    _write(base / "run-c" / "status.json", json.dumps({"state": "stopped"}))
    (base / "run-d").mkdir()
    _write(base / "stray.txt", "not a run")
    orm_create_proposal_run(fake_git_repo, "run-d", provider="human", state="queued")

    first = backfill_disk_proposals_to_orm(fake_git_repo)
    second = backfill_disk_proposals_to_orm(fake_git_repo)

    runs = {r["id"]: r.get("state") for r in orm_list_proposal_runs(fake_git_repo)}
    assert (first, second, runs) == (
        {"scanned": 4, "imported": 3},
        {"scanned": 4, "imported": 0},
        {"run-a": "completed", "run-b": "failed", "run-c": "stopped",
         "run-d": "queued"},
    )
