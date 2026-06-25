"""Phase 4b — gated auto-spawn of the external drafting agent.

Spy-based: the real external-agent runner is never invoked. We assert the
double gate (auto_spawn_agents AND external_agent_configured), idempotency
(status.json already present → skip), pending-only filtering, the batch cap,
and that run_content_evolution folds it in. Defaults-off → zero spawns.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from lib.settings import settings
from lib.topics import topic_dir
from lib.topics.agent_spawn import maybe_spawn_refresh_agents
from lib.topics.content_drift import emit_refresh_proposal
from lib.topics.core import topic_path
from lib.topics.proposals import load_proposal
from lib.topics.snapshots import resolve_or_create_repo


@pytest.fixture
def spy(monkeypatch):
    """Capture start_external_proposal_run calls; never run a real agent."""
    calls: list[dict] = []

    def _fake(repo_path, *, run_id=None, topic_request=None, **kw):
        calls.append({"repo": str(repo_path), "run_id": run_id,
                      "topic_request": topic_request})
        return {"dir": Path(repo_path)}

    monkeypatch.setattr(
        "lib.topics.proposals.external_jobs.start_external_proposal_run", _fake)
    return calls


def _configure(monkeypatch, *, spawn: bool, configured: bool):
    monkeypatch.setattr(settings.topic_evolution, "auto_spawn_agents", spawn)
    monkeypatch.setattr(
        "lib.topics.proposal_external.external_agent_configured",
        lambda: configured)


def _topic(refs: list[dict]) -> dict:
    return {
        "label": "T", "intent": "t", "status": "active", "aliases": [],
        "refs": refs, "edges": [], "commands": [],
        "include_globs": [], "exclude_globs": [],
    }


def _seed(repo: Path, topics: dict) -> None:
    p = topic_path(repo)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"version": 1, "repo": repo.name, "topics": topics}))
    resolve_or_create_repo(str(repo))


# ── the double gate ───────────────────────────────────────────


def test_disabled_does_not_spawn(fake_git_repo, monkeypatch, spy):
    _configure(monkeypatch, spawn=False, configured=True)
    _seed(fake_git_repo, {"t1": _topic([{"path": "a.py"}])})
    emit_refresh_proposal(fake_git_repo, "t1", ["a.py"])

    assert maybe_spawn_refresh_agents(fake_git_repo) == 0
    assert spy == []


def test_not_configured_does_not_spawn(fake_git_repo, monkeypatch, spy):
    _configure(monkeypatch, spawn=True, configured=False)
    _seed(fake_git_repo, {"t1": _topic([{"path": "a.py"}])})
    emit_refresh_proposal(fake_git_repo, "t1", ["a.py"])

    assert maybe_spawn_refresh_agents(fake_git_repo) == 0
    assert spy == []


def test_spawns_pending_content_drift(fake_git_repo, monkeypatch, spy):
    _configure(monkeypatch, spawn=True, configured=True)
    _seed(fake_git_repo, {"t1": _topic([{"path": "a.py"}])})
    emit_refresh_proposal(fake_git_repo, "t1", ["a.py"])

    assert maybe_spawn_refresh_agents(fake_git_repo) == 1
    assert len(spy) == 1
    assert spy[0]["run_id"] == "content-drift-t1"
    assert "t1" in spy[0]["topic_request"]
    assert "a.py" in spy[0]["topic_request"]


# ── idempotency + filtering + cap ─────────────────────────────


def test_already_spawned_is_skipped(fake_git_repo, monkeypatch, spy):
    _configure(monkeypatch, spawn=True, configured=True)
    _seed(fake_git_repo, {"t1": _topic([{"path": "a.py"}])})
    emit_refresh_proposal(fake_git_repo, "t1", ["a.py"])
    # a prior run already left an on-disk status.json under the proposal dir
    out_dir = topic_dir(fake_git_repo) / "proposals" / "content-drift-t1"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "status.json").write_text(json.dumps({"state": "running"}))

    assert maybe_spawn_refresh_agents(fake_git_repo) == 0
    assert spy == []


def test_non_pending_proposal_is_skipped(fake_git_repo, monkeypatch, spy):
    from lib.topics.proposal_orm.runs import orm_save_proposal
    _configure(monkeypatch, spawn=True, configured=True)
    _seed(fake_git_repo, {"t1": _topic([{"path": "a.py"}])})
    orm_save_proposal(str(fake_git_repo), "content-drift-t1", {
        "provider": "content-drift", "scope": "all", "status": "applied",
        "topics": [{"id": "t1", "label": "T", "aliases": [], "intent": "i",
                    "status": "active", "refs": [], "edges": [], "commands": [],
                    "include_globs": [], "exclude_globs": [],
                    "evidence_paths": []}],
        "metadata": {},
    }, wiki="w")

    assert maybe_spawn_refresh_agents(fake_git_repo) == 0
    assert spy == []


def test_batch_cap_limits_spawns(fake_git_repo, monkeypatch, spy):
    _configure(monkeypatch, spawn=True, configured=True)
    monkeypatch.setattr(settings.topic_evolution, "drift_proposal_batch_max", 1)
    _seed(fake_git_repo, {
        "t1": _topic([{"path": "a.py"}]),
        "t2": _topic([{"path": "b.py"}]),
    })
    emit_refresh_proposal(fake_git_repo, "t1", ["a.py"])
    emit_refresh_proposal(fake_git_repo, "t2", ["b.py"])

    assert maybe_spawn_refresh_agents(fake_git_repo) == 1
    assert len(spy) == 1


# ── folded into evolve ────────────────────────────────────────


def test_evolve_folds_in_spawn(fake_git_repo, monkeypatch, spy):
    from lib.topics.content_drift import run_content_evolution
    from lib.topics.ref_digest import capture_ref_digests
    monkeypatch.setattr(settings.topic_evolution, "evolution_enabled", True)
    _configure(monkeypatch, spawn=True, configured=True)
    repo = fake_git_repo
    (repo / "a.py").write_text("v1\n")
    _seed(repo, {"t1": _topic([{"path": "a.py"}])})
    capture_ref_digests(repo, "t1")
    (repo / "a.py").write_text("changed\n")           # drift → refresh proposal

    result = run_content_evolution(repo)
    assert result["proposals"] == 1
    assert result["spawned"] == 1
    assert len(spy) == 1
