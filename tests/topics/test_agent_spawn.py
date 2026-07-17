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
from lib.topics.core import write_split_graph
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


class _StubReviewer:
    """Stub the agentic materiality triage LLM with a fixed answer. Records
    every `cwd` it was called with, so tests can assert the triage call was
    scoped to the target repo, not left to inherit the host process's cwd.
    Also records every `surface_id`, so tests can assert triage passes its
    OWN surface id rather than silently inheriting the reviewer's."""

    def __init__(self, answer):
        self._answer = answer
        self.seen_cwds: list = []
        self.seen_surface_ids: list = []

    def complete(self, prompt, *, max_tokens=1024, cwd=None, surface_id=None):
        del prompt, max_tokens
        self.seen_cwds.append(cwd)
        self.seen_surface_ids.append(surface_id)
        return self._answer


def _set_triage(monkeypatch, answer) -> _StubReviewer:
    """Install (and return) a single shared stub instance, so callers can
    inspect `.seen_cwds` after the code under test runs."""
    reviewer = _StubReviewer(answer)
    monkeypatch.setattr("lib.memory.adapters.resolve_proposal_reviewer",
                        lambda: reviewer)
    return reviewer


def _configure(monkeypatch, *, spawn: bool, configured: bool):
    monkeypatch.setattr(settings.topic_evolution, "auto_spawn_agents", spawn)
    monkeypatch.setattr(
        "lib.topics.proposal_external.external_agent_configured",
        lambda: configured)
    # Default: triage judges MATERIAL so spawn-path tests are deterministic and
    # never shell out to a real agent. Triage tests override with _set_triage.
    _set_triage(monkeypatch, "VERDICT: MATERIAL")


def _topic(refs: list[dict]) -> dict:
    return {
        "label": "T", "intent": "t", "status": "active", "aliases": [],
        "refs": refs, "edges": [], "commands": [],
        "include_globs": [], "exclude_globs": [],
    }


def _seed(repo: Path, topics: dict) -> None:
    write_split_graph(repo, {"version": 1, "repo": repo.name,
                            "updated_at": "2026-01-01T00:00:00Z", "topics": topics})
    resolve_or_create_repo(str(repo))


def _write_wiki(repo: Path, topic_id: str) -> None:
    """A per-topic wiki on disk — triage needs a narrative to judge the drift
    against; with no wiki it is skipped (fail open to spawn)."""
    from lib.topics.wiki import wiki_dir
    wd = wiki_dir(repo)
    wd.mkdir(parents=True, exist_ok=True)
    (wd / f"{topic_id}.md").write_text(f"# {topic_id}\n\nnarrative\n")


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


# ── agentic materiality triage ────────────────────────────────


def test_triage_trivial_dismisses_without_spawn(fake_git_repo, monkeypatch, spy):
    _configure(monkeypatch, spawn=True, configured=True)
    _set_triage(monkeypatch, "Only whitespace moved.\nVERDICT: TRIVIAL")
    repo = fake_git_repo
    (repo / "a.py").write_text("x\n")
    _seed(repo, {"t1": _topic([{"path": "a.py"}])})
    _write_wiki(repo, "t1")            # triage needs a wiki to judge against
    emit_refresh_proposal(repo, "t1", ["a.py"])

    # trivial → no draft spawned, and the stub leaves the review queue
    assert maybe_spawn_refresh_agents(repo) == 0
    assert spy == []
    assert load_proposal(repo, "content-drift-t1")["status"] != "pending_review"


def test_triage_passes_repo_path_as_cwd(fake_git_repo, monkeypatch, spy):
    """The materiality triage must inspect the proposal's own repo, not
    wherever the host process happens to be running from (regression: it
    used to default to `cwd=None`, so a drift triage for another repo got
    judged against the wrong tree)."""
    _configure(monkeypatch, spawn=True, configured=True)
    reviewer = _set_triage(monkeypatch, "Only whitespace moved.\nVERDICT: TRIVIAL")
    repo = fake_git_repo
    (repo / "a.py").write_text("x\n")
    _seed(repo, {"t1": _topic([{"path": "a.py"}])})
    _write_wiki(repo, "t1")
    emit_refresh_proposal(repo, "t1", ["a.py"])

    maybe_spawn_refresh_agents(repo)

    # Two judged calls — the batched judge (whose triage-format answer parses
    # to nothing → fallback), then the per-item triage — both repo-scoped.
    assert reviewer.seen_cwds == [repo, repo]


def test_triage_passes_its_own_surface_id(fake_git_repo, monkeypatch, spy):
    """Triage must pass ITS OWN surface id, not silently inherit whatever the
    reviewer LLM was constructed with (regression: it used to call `.complete()`
    with no `surface_id` override at all, so triage sessions were traced —
    and agent-bound — as `topic-proposal-review` runs)."""
    from lib.prompts.surfaces.triage import JUDGE_BATCH_SURFACE_ID
    from lib.prompts.surfaces.triage import SURFACE_ID as TRIAGE_SURFACE_ID

    _configure(monkeypatch, spawn=True, configured=True)
    reviewer = _set_triage(monkeypatch, "Only whitespace moved.\nVERDICT: TRIVIAL")
    repo = fake_git_repo
    (repo / "a.py").write_text("x\n")
    _seed(repo, {"t1": _topic([{"path": "a.py"}])})
    _write_wiki(repo, "t1")
    emit_refresh_proposal(repo, "t1", ["a.py"])

    maybe_spawn_refresh_agents(repo)

    # Batched judge first (its own surface id), then — its triage-format
    # answer parses to nothing — the per-item triage under its own id.
    assert reviewer.seen_surface_ids == [JUDGE_BATCH_SURFACE_ID,
                                         TRIAGE_SURFACE_ID]


def test_triage_material_spawns(fake_git_repo, monkeypatch, spy):
    _configure(monkeypatch, spawn=True, configured=True)
    _set_triage(monkeypatch, "The public API changed.\nVERDICT: MATERIAL")
    _seed(fake_git_repo, {"t1": _topic([{"path": "a.py"}])})
    _write_wiki(fake_git_repo, "t1")
    emit_refresh_proposal(fake_git_repo, "t1", ["a.py"])

    assert maybe_spawn_refresh_agents(fake_git_repo) == 1
    assert len(spy) == 1
    assert load_proposal(fake_git_repo, "content-drift-t1")["status"] == "pending_review"


def test_triage_fails_open_on_empty_answer(fake_git_repo, monkeypatch, spy):
    # No agent / empty answer must NOT silently dismiss a possibly-real drift.
    _configure(monkeypatch, spawn=True, configured=True)
    _set_triage(monkeypatch, "")
    _seed(fake_git_repo, {"t1": _topic([{"path": "a.py"}])})
    _write_wiki(fake_git_repo, "t1")
    emit_refresh_proposal(fake_git_repo, "t1", ["a.py"])

    assert maybe_spawn_refresh_agents(fake_git_repo) == 1
    assert len(spy) == 1


# ── folded into evolve ────────────────────────────────────────


# ── origin-run refresh via regenerate ─────────────────────────


@pytest.fixture
def regen_spy(monkeypatch):
    """Capture start_external_regenerate_run calls; never run a real agent."""
    calls: list[dict] = []

    def _fake(repo_path, proposal_id):
        calls.append({"repo": str(repo_path), "run_id": proposal_id})
        return {"dir": Path(repo_path)}

    monkeypatch.setattr(
        "lib.topics.proposals.external_jobs.start_external_regenerate_run", _fake)
    return calls


def _seed_origin_run(repo: Path, run_id: str, topic_id: str) -> None:
    from lib.orm import SessionLocal
    from lib.orm.models import TopicAudit
    from lib.topics.proposal_orm.runs import orm_save_proposal
    repo_obj = resolve_or_create_repo(str(repo))
    orm_save_proposal(str(repo), run_id, {
        "provider": "external-agent", "scope": "all", "status": "applied",
        "topics": [{"id": topic_id, "label": "T", "aliases": [], "intent": "i",
                    "status": "active", "refs": [], "edges": [], "commands": [],
                    "include_globs": [], "exclude_globs": [], "evidence_paths": []}],
        "metadata": {},
    }, wiki="original wiki")
    with SessionLocal() as s:
        s.add(TopicAudit(
            repo_id=repo_obj.id, kind="provenance",
            recorded_at="2024-01-01T00:00:00Z", severity="info",
            code="topic_create", message="m",
            topic_ids_json=json.dumps([topic_id]), paths_json="[]",
            aliases_json="[]", triggering_run_id=run_id,
            triggering_proposal_topic_id=None))
        s.commit()


def _open_drift_threads(repo: Path, run_id: str) -> list[dict]:
    from lib.topics.proposals.feedback import list_proposal_feedback_threads
    return [t for t in list_proposal_feedback_threads(repo, run_id)
            if t.get("kind") == "content_drift"
            and t.get("resolution_state") == "open"]


def test_origin_drift_note_regenerates_origin_run(fake_git_repo, monkeypatch,
                                                  spy, regen_spy):
    _configure(monkeypatch, spawn=True, configured=True)
    repo = fake_git_repo
    _seed(repo, {"t1": _topic([{"path": "a.py"}])})
    _seed_origin_run(repo, "origin-run-1", "t1")
    _write_wiki(repo, "t1")
    # routes to the origin run as an open drift note (not a standalone proposal)
    assert emit_refresh_proposal(repo, "t1", ["a.py"]) == "origin-run-1"

    assert maybe_spawn_refresh_agents(repo) == 1
    assert spy == []                                  # no fresh draft
    assert len(regen_spy) == 1                        # a regenerate instead
    assert regen_spy[0]["run_id"] == "origin-run-1"


def test_origin_drift_trivial_dismisses_note(fake_git_repo, monkeypatch,
                                             spy, regen_spy):
    _configure(monkeypatch, spawn=True, configured=True)
    _set_triage(monkeypatch, "Whitespace only.\nVERDICT: TRIVIAL")
    repo = fake_git_repo
    _seed(repo, {"t1": _topic([{"path": "a.py"}])})
    _seed_origin_run(repo, "origin-run-1", "t1")
    _write_wiki(repo, "t1")
    emit_refresh_proposal(repo, "t1", ["a.py"])
    assert len(_open_drift_threads(repo, "origin-run-1")) == 1

    assert maybe_spawn_refresh_agents(repo) == 0
    assert regen_spy == []
    # the note is resolved, so it stops riding into the next regenerate
    assert _open_drift_threads(repo, "origin-run-1") == []


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
