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
import pytest

from lib.topics import TopicGraphError
from lib.topics.content_drift import (
    detect_drifted_topics,
    dismiss_content_drift,
    emit_refresh_proposal,
    run_content_evolution,
)
from lib.topics.core import topic_path
from lib.topics.proposals import (
    dismiss_content_drift_thread,
    ignore_proposed_topic,
    list_proposal_runs,
    load_proposal,
    set_proposal_feedback_thread_resolution,
)
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


# ── detection: tier excludes reference-only refs ──────────────


def test_reference_tier_ref_does_not_drift(fake_git_repo):
    repo = fake_git_repo
    (repo / "a.py").write_text("original\n")
    _write_graph(repo, {"t1": _topic([{"path": "a.py", "tier": "reference"}])})
    _register(repo)
    capture_ref_digests(repo, "t1")
    (repo / "a.py").write_text("MUTATED CONTENT\n")   # hash differs, but tier excludes

    assert detect_drifted_topics(repo) == []


def test_primary_tier_ref_still_drifts(fake_git_repo):
    repo = fake_git_repo
    (repo / "a.py").write_text("original\n")
    _write_graph(repo, {"t1": _topic([{"path": "a.py", "tier": "primary"}])})
    _register(repo)
    capture_ref_digests(repo, "t1")
    (repo / "a.py").write_text("MUTATED CONTENT\n")

    assert detect_drifted_topics(repo) == [{"topic_id": "t1", "drifted_paths": ["a.py"]}]


def test_untagged_tier_behaves_as_primary(fake_git_repo):
    """Regression guard for checklist item 2: an unset tier is unchanged from
    today — it drifts exactly like an explicit `primary`."""
    repo = fake_git_repo
    (repo / "a.py").write_text("original\n")
    _write_graph(repo, {"t1": _topic([{"path": "a.py"}])})   # no tier
    _register(repo)
    capture_ref_digests(repo, "t1")
    (repo / "a.py").write_text("MUTATED CONTENT\n")

    assert detect_drifted_topics(repo) == [{"topic_id": "t1", "drifted_paths": ["a.py"]}]


def test_mixed_tiers_flags_only_non_reference(fake_git_repo):
    repo = fake_git_repo
    (repo / "a.py").write_text("a\n")
    (repo / "b.py").write_text("b\n")
    _write_graph(repo, {"t1": _topic([
        {"path": "a.py"},                          # untagged → primary → drifts
        {"path": "b.py", "tier": "reference"},     # excluded
    ])})
    _register(repo)
    capture_ref_digests(repo, "t1")
    (repo / "a.py").write_text("a changed\n")
    (repo / "b.py").write_text("b changed\n")

    assert detect_drifted_topics(repo) == [{"topic_id": "t1", "drifted_paths": ["a.py"]}]


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


# ── refresh routed to the origin proposal run ─────────────────


def _full_topic(topic_id: str) -> dict:
    return {"id": topic_id, "label": "T", "aliases": [], "intent": "i",
            "status": "active", "refs": [], "edges": [], "commands": [],
            "include_globs": [], "exclude_globs": [], "evidence_paths": []}


def _seed_origin_run(repo: Path, repo_id: int, run_id: str, topic_id: str) -> None:
    """A completed proposal run that 'created' `topic_id`, plus the provenance
    audit row that `orm_find_origin_proposal_run_for_topic` reads."""
    from lib.orm import SessionLocal
    from lib.orm.models import TopicAudit
    from lib.topics.proposal_orm.runs import orm_save_proposal

    orm_save_proposal(str(repo), run_id, {
        "provider": "external-agent", "scope": "all", "status": "applied",
        "topics": [_full_topic(topic_id)], "metadata": {},
    }, wiki="original wiki narrative")
    with SessionLocal() as s:
        s.add(TopicAudit(
            repo_id=repo_id, kind="provenance", recorded_at="2024-01-01T00:00:00Z",
            severity="info", code="topic_create", message="m",
            topic_ids_json=json.dumps([topic_id]), paths_json="[]",
            aliases_json="[]", triggering_run_id=run_id,
            triggering_proposal_topic_id=None))
        s.commit()


def _drift_threads(repo: Path, run_id: str) -> list[dict]:
    from lib.topics.proposals.feedback import list_proposal_feedback_threads
    return [t for t in list_proposal_feedback_threads(repo, run_id)
            if t.get("kind") == "content_drift"]


def test_emit_refresh_routes_to_origin_run_as_open_note(fake_git_repo):
    repo = fake_git_repo
    (repo / "a.py").write_text("x\n")
    _write_graph(repo, {"t1": _topic([{"path": "a.py"}])})
    repo_id = _register(repo)
    _seed_origin_run(repo, repo_id, "origin-run-1", "t1")

    pid = emit_refresh_proposal(repo, "t1", ["a.py"])
    # routed to the origin run, NOT a standalone content-drift proposal
    assert pid == "origin-run-1"
    run_ids = {r["id"] for r in list_proposal_runs(repo)}
    assert "content-drift-t1" not in run_ids

    notes = _drift_threads(repo, "origin-run-1")
    assert len(notes) == 1
    assert notes[0]["resolution_state"] == "open"
    assert notes[0]["created_by"] == "agent"


def test_emit_refresh_origin_note_is_idempotent(fake_git_repo):
    repo = fake_git_repo
    (repo / "a.py").write_text("x\n")
    _write_graph(repo, {"t1": _topic([{"path": "a.py"}])})
    repo_id = _register(repo)
    _seed_origin_run(repo, repo_id, "origin-run-1", "t1")

    assert emit_refresh_proposal(repo, "t1", ["a.py"]) == "origin-run-1"
    # a second detection of the same unresolved drift must not stack a note
    assert emit_refresh_proposal(repo, "t1", ["a.py"]) == "origin-run-1"
    assert len(_drift_threads(repo, "origin-run-1")) == 1


# ── dismiss drift as unrelated (re-baseline + dismiss note) ───


def _seed_drifted_with_note(repo: Path) -> None:
    """Digest a ref, seed the origin run, mutate the ref (hash drifts), and
    open the content-drift note on the origin run — the state a user faces
    when a ref changed but the wiki narrative is unaffected."""
    (repo / "a.py").write_text("x\n")
    _write_graph(repo, {"t1": _topic([{"path": "a.py"}])})
    repo_id = _register(repo)
    capture_ref_digests(repo, "t1")
    _seed_origin_run(repo, repo_id, "origin-run-1", "t1")
    (repo / "a.py").write_text("unrelated formatting only\n")   # hash drifts
    assert emit_refresh_proposal(repo, "t1", ["a.py"]) == "origin-run-1"


def test_dismiss_content_drift_advances_baseline_and_dismisses_note(fake_git_repo):
    repo = fake_git_repo
    _seed_drifted_with_note(repo)
    assert detect_drifted_topics(repo) == [{"topic_id": "t1", "drifted_paths": ["a.py"]}]

    result = dismiss_content_drift(repo, "t1")
    assert result["topic_id"] == "t1"
    assert result["digests_captured"] == 1
    assert len(result["threads_dismissed"]) == 1

    # baseline synced to current code → topic no longer drifts
    assert detect_drifted_topics(repo) == []
    # and the note is dismissed, not merely resolved
    note = _drift_threads(repo, "origin-run-1")[0]
    assert note["resolution_state"] == "dismissed"
    # the origin-run path has no standalone proposal to ignore
    assert result["proposal_ignored"] is False


# ── dismiss drift on the standalone content-drift-<topic> path ─


def _seed_standalone_drift(repo: Path) -> None:
    """A drifted topic with NO origin run → `emit_refresh_proposal` mints the
    standalone `content-drift-<topic>` proposal (no feedback note)."""
    (repo / "a.py").write_text("x\n")
    _write_graph(repo, {"t1": _topic([{"path": "a.py"}])})
    _register(repo)
    capture_ref_digests(repo, "t1")
    (repo / "a.py").write_text("unrelated change\n")   # hash drifts
    assert emit_refresh_proposal(repo, "t1", ["a.py"]) == "content-drift-t1"


def test_human_ignore_content_drift_proposal_rebaselines(fake_git_repo, monkeypatch):
    monkeypatch.setattr(settings.topic_evolution, "evolution_enabled", True)
    repo = fake_git_repo
    _seed_standalone_drift(repo)
    assert detect_drifted_topics(repo) == [{"topic_id": "t1", "drifted_paths": ["a.py"]}]

    # the human /ignore path passes rebaseline_drift=True
    ignore_proposed_topic(repo, "content-drift-t1", "t1", rebaseline_drift=True)

    # ignoring a content-drift refresh advanced the baseline → drift gone
    assert detect_drifted_topics(repo) == []
    # and a full evolve pass does not resurrect it
    assert run_content_evolution(repo)["drifted"] == 0


def test_auto_ignore_content_drift_does_not_rebaseline(fake_git_repo):
    """The default (auto) path — expiry / trivial-dismiss — must NOT silently
    advance the baseline: a genuine drift the user never judged must survive so
    it isn't forgotten with a stale wiki."""
    repo = fake_git_repo
    _seed_standalone_drift(repo)

    # no rebaseline_drift flag → mirrors expire_stale_auto_proposals
    ignore_proposed_topic(repo, "content-drift-t1", "t1")

    # baseline untouched → the real drift is still detectable
    assert detect_drifted_topics(repo) == [{"topic_id": "t1", "drifted_paths": ["a.py"]}]


def test_dismiss_content_drift_ignores_standalone_proposal(fake_git_repo):
    repo = fake_git_repo
    _seed_standalone_drift(repo)

    result = dismiss_content_drift(repo, "t1")
    assert result["proposal_ignored"] is True
    assert result["digests_captured"] == 1
    assert detect_drifted_topics(repo) == []
    proposal = load_proposal(repo, "content-drift-t1")
    assert proposal["topics"][0]["review_status"] == "ignored"


def test_ignore_non_drift_proposal_does_not_touch_baseline(fake_git_repo):
    """The re-baseline is scoped to content-drift refresh proposals: ignoring a
    plain proposal for one topic must not clear a real drift on another."""
    repo = fake_git_repo
    (repo / "a.py").write_text("x\n")
    _write_graph(repo, {"t1": _topic([{"path": "a.py"}])})
    repo_id = _register(repo)
    capture_ref_digests(repo, "t1")
    (repo / "a.py").write_text("real drift\n")
    # a plain (non content-drift) proposal proposing some other topic
    _seed_origin_run(repo, repo_id, "plain-run", "t2")

    ignore_proposed_topic(repo, "plain-run", "t2")

    # t1's genuine drift is untouched — ignore didn't fire a global re-baseline
    assert detect_drifted_topics(repo) == [{"topic_id": "t1", "drifted_paths": ["a.py"]}]


def test_dismiss_content_drift_stops_resurrection(fake_git_repo, monkeypatch):
    monkeypatch.setattr(settings.topic_evolution, "evolution_enabled", True)
    repo = fake_git_repo
    _seed_drifted_with_note(repo)

    dismiss_content_drift(repo, "t1")

    # a full evolve pass finds nothing to re-flag and opens no fresh note
    result = run_content_evolution(repo)
    assert result["drifted"] == 0
    assert result["proposals"] == 0
    open_notes = [n for n in _drift_threads(repo, "origin-run-1")
                  if n["resolution_state"] == "open"]
    assert open_notes == []


def test_plain_dismiss_without_rebaseline_resurrects(fake_git_repo, monkeypatch):
    """The bug this feature fixes: dismissing the note WITHOUT advancing the
    baseline leaves the digest stale, so the next evolve pass opens a fresh
    note — the drift resurrects."""
    monkeypatch.setattr(settings.topic_evolution, "evolution_enabled", True)
    repo = fake_git_repo
    _seed_drifted_with_note(repo)

    note = _drift_threads(repo, "origin-run-1")[0]
    set_proposal_feedback_thread_resolution(
        repo, "origin-run-1", note["id"], resolution_state="dismissed")

    run_content_evolution(repo)   # baseline still stale → re-detects
    open_notes = [n for n in _drift_threads(repo, "origin-run-1")
                  if n["resolution_state"] == "open"]
    assert len(open_notes) == 1   # resurrected, exactly what dismiss_content_drift prevents


def test_dismiss_content_drift_no_open_note_still_rebaselines(fake_git_repo):
    """With no open note (topic drifted but never emitted), the call is a
    safe no-op on the thread side yet still advances the baseline."""
    repo = fake_git_repo
    (repo / "a.py").write_text("x\n")
    _write_graph(repo, {"t1": _topic([{"path": "a.py"}])})
    _register(repo)
    capture_ref_digests(repo, "t1")
    (repo / "a.py").write_text("changed\n")
    assert detect_drifted_topics(repo) == [{"topic_id": "t1", "drifted_paths": ["a.py"]}]

    result = dismiss_content_drift(repo, "t1")
    assert result["threads_dismissed"] == []
    assert result["digests_captured"] == 1
    assert detect_drifted_topics(repo) == []


def test_dismiss_content_drift_thread_wrapper(fake_git_repo):
    repo = fake_git_repo
    _seed_drifted_with_note(repo)
    note = _drift_threads(repo, "origin-run-1")[0]

    result = dismiss_content_drift_thread(repo, "origin-run-1", note["id"])
    assert result["topic_id"] == "t1"
    assert detect_drifted_topics(repo) == []

    # the note is no longer open → re-dismissing it raises, not silently rebaselines
    with pytest.raises(TopicGraphError):
        dismiss_content_drift_thread(repo, "origin-run-1", note["id"])
    # unknown thread id raises too
    with pytest.raises(TopicGraphError):
        dismiss_content_drift_thread(repo, "origin-run-1", 999999)


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
