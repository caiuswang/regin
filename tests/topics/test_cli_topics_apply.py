"""CLI smoke tests for the topic audit shims.

Covers `regin topics audit` and `audit-fix`. The library + endpoint
behavior is already tested elsewhere; these tests pin that the typer
wrappers parse args and dispatch correctly.
"""

from __future__ import annotations

import json
import subprocess
from typer.testing import CliRunner

from cli.commands import topics as topics_cmd
from lib.orm import SessionLocal
from lib.orm.models import Repo
from lib.topics import bootstrap


runner = CliRunner()


def _seed_repo(path) -> Repo:
    with SessionLocal() as s:
        repo = Repo(name=path.name, path=str(path), default_branch="main", is_active=1)
        s.add(repo)
        s.commit()
        s.refresh(repo)
        return repo


def test_cli_audit_reports_empty_clean_graph(fake_git_repo, monkeypatch):
    _seed_repo(fake_git_repo)
    bootstrap(fake_git_repo)
    result = runner.invoke(topics_cmd.topics_app, ["audit", "--repo", str(fake_git_repo)])
    assert result.exit_code == 0, result.stdout
    body = json.loads(result.stdout)
    assert body["error_count"] == 0
    assert "graph.dead_ref" in body["auto_fixable_codes"]


def test_cli_audit_fix_resolves_dead_refs(fake_git_repo, monkeypatch):
    repo = _seed_repo(fake_git_repo)
    bootstrap(fake_git_repo)
    # Plant a dead ref.
    graph_path = fake_git_repo / ".regin/topics/topic.json"
    graph = json.loads(graph_path.read_text())
    graph["topics"]["x"] = {
        "label": "X", "intent": "x", "status": "active",
        "aliases": [], "refs": [{"path": "missing.py", "role": "implementation"}],
        "edges": [], "commands": [], "include_globs": [], "exclude_globs": [],
    }
    graph_path.write_text(json.dumps(graph))

    result = runner.invoke(topics_cmd.topics_app, [
        "audit-fix", "--code", "graph.dead_ref", "--repo", str(fake_git_repo),
    ])
    assert result.exit_code == 0, result.stdout
    body = json.loads(result.stdout)
    assert body["fixed_counts"]["graph.dead_ref"] >= 1
    assert len(body["snapshot_ids"]) >= 1
