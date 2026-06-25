"""Phase 3 — content-drift detection + refresh proposals.

A topic ref whose content changed since it was digested drifts (hash path);
the cosine filter spares a trivial change when embeddings are present; a
drifted topic yields a single-topic, idempotent, human-gated refresh proposal
and cascades staleness onto its linked memories. All gated off by default.
"""

from __future__ import annotations

import json
from pathlib import Path

from lib.memory import get_store
from lib.memory.models import MemoryInput
from lib.settings import settings
from lib.topics.content_drift import (
    detect_drifted_topics,
    emit_refresh_proposal,
    run_content_evolution,
)
from lib.topics.core import topic_path
from lib.topics.proposals import load_proposal
from lib.topics.ref_digest import capture_ref_digests
from lib.topics.snapshots import resolve_or_create_repo


class _FixedEmbedder:
    """Returns a fixed vector regardless of input — used to simulate
    'embedding unchanged' (cosine 1.0) vs 'embedding moved' (orthogonal)."""

    model_id = "fake-v1"

    def __init__(self, vec):
        self._vec = vec

    def embed(self, texts):
        return [list(self._vec) for _ in texts]


def _topic(refs: list[dict]) -> dict:
    return {
        "label": "T", "intent": "t", "status": "active", "aliases": [],
        "refs": refs, "edges": [], "commands": [],
        "include_globs": [], "exclude_globs": [],
    }


def _write_graph(repo: Path, topics: dict) -> None:
    p = topic_path(repo)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"version": 1, "repo": repo.name, "topics": topics}))


def _register(repo: Path) -> int:
    return resolve_or_create_repo(str(repo)).id


# ── detection: hash path ──────────────────────────────────────


def test_unchanged_ref_does_not_drift(fake_git_repo):
    repo = fake_git_repo
    (repo / "a.py").write_text("original\n")
    _write_graph(repo, {"t1": _topic([{"path": "a.py", "role": "implementation"}])})
    _register(repo)
    capture_ref_digests(repo, "t1")

    assert detect_drifted_topics(repo) == []


def test_changed_ref_drifts(fake_git_repo):
    repo = fake_git_repo
    (repo / "a.py").write_text("original\n")
    _write_graph(repo, {"t1": _topic([{"path": "a.py"}])})
    _register(repo)
    capture_ref_digests(repo, "t1")
    (repo / "a.py").write_text("MUTATED CONTENT\n")   # hash now differs

    drifted = detect_drifted_topics(repo)
    assert drifted == [{"topic_id": "t1", "drifted_paths": ["a.py"]}]


def test_topic_without_digest_is_skipped(fake_git_repo):
    repo = fake_git_repo
    (repo / "a.py").write_text("x\n")
    _write_graph(repo, {"t1": _topic([{"path": "a.py"}])})
    _register(repo)
    # never captured → can't judge
    assert detect_drifted_topics(repo) == []


def test_deleted_ref_is_not_content_drift(fake_git_repo):
    repo = fake_git_repo
    (repo / "a.py").write_text("x\n")
    _write_graph(repo, {"t1": _topic([{"path": "a.py"}])})
    _register(repo)
    capture_ref_digests(repo, "t1")
    (repo / "a.py").unlink()                          # deletion → Phase 2

    assert detect_drifted_topics(repo) == []


# ── detection: cosine filter ──────────────────────────────────


def test_cosine_spares_trivial_change(fake_git_repo):
    repo = fake_git_repo
    (repo / "a.py").write_text("v1\n")
    _write_graph(repo, {"t1": _topic([{"path": "a.py"}])})
    _register(repo)
    emb = _FixedEmbedder([1.0, 0.0, 0.0])
    capture_ref_digests(repo, "t1", embedder=emb)     # stores [1,0,0]
    (repo / "a.py").write_text("v1 + trivial tweak\n")  # hash changes

    # same embedder → cosine 1.0 ≥ 0.6 → spared despite the hash change
    assert detect_drifted_topics(repo, embedder=emb) == []


def test_cosine_below_threshold_flags(fake_git_repo):
    repo = fake_git_repo
    (repo / "a.py").write_text("v1\n")
    _write_graph(repo, {"t1": _topic([{"path": "a.py"}])})
    _register(repo)
    capture_ref_digests(repo, "t1", embedder=_FixedEmbedder([1.0, 0.0, 0.0]))
    (repo / "a.py").write_text("totally rewritten\n")
    # current embedding orthogonal to stored → cosine 0 < 0.6 → material drift
    drifted = detect_drifted_topics(repo, embedder=_FixedEmbedder([0.0, 1.0, 0.0]))
    assert drifted == [{"topic_id": "t1", "drifted_paths": ["a.py"]}]


# ── refresh proposal ──────────────────────────────────────────


def test_emit_refresh_proposal_is_single_topic_and_idempotent(fake_git_repo):
    repo = fake_git_repo
    (repo / "a.py").write_text("x\n")
    _write_graph(repo, {"t1": _topic([{"path": "a.py"}])})
    _register(repo)

    pid = emit_refresh_proposal(repo, "t1", ["a.py"])
    assert pid == "content-drift-t1"
    proposal = load_proposal(repo, pid)
    assert proposal["status"] == "pending_review"
    assert proposal["provider"] == "content-drift"
    assert len(proposal["topics"]) == 1
    assert proposal["topics"][0]["id"] == "t1"

    # re-emit upserts the same id, does not stack a second proposal
    assert emit_refresh_proposal(repo, "t1", ["a.py"]) == "content-drift-t1"


# ── orchestrator ──────────────────────────────────────────────


def test_run_content_evolution_disabled_is_noop(fake_git_repo, monkeypatch):
    monkeypatch.setattr(settings.topic_evolution, "evolution_enabled", False)
    repo = fake_git_repo
    (repo / "a.py").write_text("x\n")
    _write_graph(repo, {"t1": _topic([{"path": "a.py"}])})
    _register(repo)
    capture_ref_digests(repo, "t1")
    (repo / "a.py").write_text("changed\n")

    result = run_content_evolution(repo)
    assert result["enabled"] is False
    assert result["proposals"] == 0


def test_run_content_evolution_cascades_and_proposes(fake_git_repo, monkeypatch):
    monkeypatch.setattr(settings.topic_evolution, "evolution_enabled", True)
    repo = fake_git_repo
    (repo / "a.py").write_text("x\n")
    _write_graph(repo, {"t1": _topic([{"path": "a.py"}])})
    _register(repo)
    capture_ref_digests(repo, "t1")
    (repo / "a.py").write_text("changed\n")
    store = get_store()
    mid = store.remember(MemoryInput(body="lesson", title="m", kind="lesson",
                                     veracity="true", scope="repo:demo"))
    store.link_authoritative_topic(mid, "t1", source="manual")

    result = run_content_evolution(repo)
    assert result["drifted"] == 1
    assert result["proposals"] == 1
    assert result["memories_staled"] == 1
    assert store.get(mid).veracity == "unknown"
    assert load_proposal(repo, "content-drift-t1")["status"] == "pending_review"


def test_run_content_evolution_respects_batch_cap(fake_git_repo, monkeypatch):
    monkeypatch.setattr(settings.topic_evolution, "evolution_enabled", True)
    monkeypatch.setattr(settings.topic_evolution, "drift_proposal_batch_max", 1)
    repo = fake_git_repo
    (repo / "a.py").write_text("x\n")
    (repo / "b.py").write_text("y\n")
    _write_graph(repo, {
        "t1": _topic([{"path": "a.py"}]),
        "t2": _topic([{"path": "b.py"}]),
    })
    _register(repo)
    capture_ref_digests(repo, "t1")
    capture_ref_digests(repo, "t2")
    (repo / "a.py").write_text("changed a\n")
    (repo / "b.py").write_text("changed b\n")

    result = run_content_evolution(repo)
    assert result["drifted"] == 2          # both detected
    assert result["proposals"] == 1        # but only one emitted (cap)
    assert result["capped"] == 1           # and the drop is reported, not silent
