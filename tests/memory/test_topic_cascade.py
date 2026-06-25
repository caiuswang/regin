"""Phase 2 — topic→memory cascade (bidirectional link).

Genuine staleness (a deleted ref, or an explicit cascade) demotes linked
memories' veracity true→unknown; a rename does NOT (relocation ≠ staleness);
a topic refresh restores the memories the cascade demoted. Orthogonal axes:
importance is never touched.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from lib.memory import get_store
from lib.memory.models import MemoryInput
from lib.memory.topic_cascade import cascade_topic_stale, restore_topic_memories
from lib.settings import settings
from lib.topics import drift
from lib.topics.core import topic_path


def _linked_mem(store, *, veracity: str = "true", topic: str = "t1",
                importance: float = 0.5) -> str:
    mid = store.remember(MemoryInput(
        body="A lesson.", title="m", kind="lesson", veracity=veracity,
        importance=importance, scope="repo:demo"))
    store.link_authoritative_topic(mid, topic, source="manual")
    return mid


def _commit(repo: Path, msg: str) -> None:
    subprocess.check_call(["git", "-C", str(repo), "add", "-A"])
    subprocess.check_call(["git", "-C", str(repo), "commit", "-q", "-m", msg])


def _write_graph(repo: Path, topics: dict) -> None:
    p = topic_path(repo)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"version": 1, "repo": repo.name, "topics": topics}))


def _topic(refs: list[dict]) -> dict:
    return {
        "label": "T", "intent": "t", "status": "active", "aliases": [],
        "refs": refs, "edges": [], "commands": [],
        "include_globs": [], "exclude_globs": [],
    }


# ── cascade staleness ─────────────────────────────────────────


def test_cascade_demotes_true_keeps_importance():
    store = get_store()
    mid = _linked_mem(store, veracity="true", importance=0.7)

    assert cascade_topic_stale(store, "t1", reason="ref_deleted") == 1
    row = store.get(mid)
    assert row.veracity == "unknown"
    assert row.importance == 0.7          # orthogonal: strength untouched


def test_cascade_skips_non_true():
    store = get_store()
    _linked_mem(store, veracity="unknown")
    _linked_mem(store, veracity="false")
    assert cascade_topic_stale(store, "t1", reason="stale") == 0


def test_cascade_is_idempotent():
    store = get_store()
    _linked_mem(store, veracity="true")
    assert cascade_topic_stale(store, "t1", reason="stale") == 1
    assert cascade_topic_stale(store, "t1", reason="stale") == 0


def test_cascade_unlinked_topic_noop():
    store = get_store()
    _linked_mem(store, veracity="true", topic="t1")
    assert cascade_topic_stale(store, "other-topic", reason="stale") == 0


# ── recovery on refresh ───────────────────────────────────────


def test_restore_only_memories_we_demoted():
    store = get_store()
    drifted = _linked_mem(store, veracity="true")
    cascade_topic_stale(store, "t1", reason="stale")        # drifted → unknown
    other = _linked_mem(store, veracity="unknown")          # unknown, NOT by us

    assert restore_topic_memories(store, "t1") == 1
    assert store.get(drifted).veracity == "true"            # restored
    assert store.get(other).veracity == "unknown"           # left alone


def test_restore_is_topic_scoped():
    # A memory linked to TWO topics, demoted by t1. Refreshing the OTHER topic
    # (t2) must NOT restore it — only the topic that actually demoted it can.
    store = get_store()
    mid = store.remember(MemoryInput(body="x", title="m", kind="lesson",
                                     veracity="true", scope="repo:demo"))
    store.link_authoritative_topic(mid, "t1", source="manual")
    store.link_authoritative_topic(mid, "t2", source="manual")
    cascade_topic_stale(store, "t1", reason="ref_deleted")

    assert restore_topic_memories(store, "t2") == 0          # wrong topic
    assert store.get(mid).veracity == "unknown"
    assert restore_topic_memories(store, "t1") == 1          # the demoting topic
    assert store.get(mid).veracity == "true"


# ── drift orchestrator: deletion cascades, rename does not ────


def test_deletion_cascades_to_linked_memories(fake_git_repo, monkeypatch):
    monkeypatch.setattr(settings.topic_evolution, "mechanical_autoapply", True)
    repo = fake_git_repo
    (repo / "gone.py").write_text("payload\n")
    _commit(repo, "add gone")
    subprocess.check_call(["git", "-C", str(repo), "rm", "-q", "gone.py"])
    _commit(repo, "delete gone")
    _write_graph(repo, {"t1": _topic([{"path": "gone.py"}])})
    store = get_store()
    mid = _linked_mem(store, veracity="true")

    result = drift.run_mechanical_drift(repo)
    assert result["memories_staled"] == 1
    assert store.get(mid).veracity == "unknown"


def test_rename_does_not_cascade(fake_git_repo, monkeypatch):
    monkeypatch.setattr(settings.topic_evolution, "mechanical_autoapply", True)
    repo = fake_git_repo
    (repo / "old.py").write_text("payload\n")
    _commit(repo, "add old")
    subprocess.check_call(["git", "-C", str(repo), "mv", "old.py", "new.py"])
    _commit(repo, "rename")
    _write_graph(repo, {"t1": _topic([{"path": "old.py"}])})
    store = get_store()
    mid = _linked_mem(store, veracity="true")

    result = drift.run_mechanical_drift(repo)
    assert result["memories_staled"] == 0          # relocation ≠ staleness
    assert store.get(mid).veracity == "true"


def test_restore_fires_through_replace_refresh_path(stub_proposal_provider,
                                                    fake_git_repo, monkeypatch):
    # A drift cascade demotes memories of an EXISTING approved topic; the
    # reachable way to refresh that same id is replace_approved_topic. Restore
    # must fire there (not only on brand-new accepts) when evolution is on.
    from lib.topics import bootstrap, load_graph, save_graph
    from lib.topics.proposals import (create_proposal_run, load_proposal,
                                       replace_approved_topic, save_proposal)
    monkeypatch.setattr(settings.topic_evolution, "evolution_enabled", True)
    repo = fake_git_repo
    bootstrap(repo)
    graph = load_graph(repo)
    graph["topics"]["alpha"] = {
        "label": "Alpha", "aliases": [], "intent": "old.", "status": "active",
        "refs": [], "edges": [], "commands": [],
        "include_globs": [], "exclude_globs": [],
    }
    save_graph(repo, graph)

    store = get_store()
    mid = _linked_mem(store, veracity="true", topic="alpha")
    cascade_topic_stale(store, "alpha", reason="ref_deleted")
    assert store.get(mid).veracity == "unknown"

    create_proposal_run(repo, run_id="run1")
    proposal = load_proposal(repo, "run1")
    proposal["topics"] = [{
        "id": "alpha", "label": "Alpha", "aliases": [], "intent": "refreshed.",
        "status": "active", "refs": [], "edges": [], "commands": [],
        "include_globs": [], "exclude_globs": [], "evidence_paths": [],
    }]
    save_proposal(repo, "run1", proposal)

    replace_approved_topic(repo, "run1", "alpha")
    assert store.get(mid).veracity == "true"     # refresh restored it


def test_drift_disabled_does_not_cascade(fake_git_repo, monkeypatch):
    monkeypatch.setattr(settings.topic_evolution, "mechanical_autoapply", False)
    repo = fake_git_repo
    (repo / "gone.py").write_text("p\n")
    _commit(repo, "add")
    subprocess.check_call(["git", "-C", str(repo), "rm", "-q", "gone.py"])
    _commit(repo, "del")
    _write_graph(repo, {"t1": _topic([{"path": "gone.py"}])})
    store = get_store()
    mid = _linked_mem(store, veracity="true")

    result = drift.run_mechanical_drift(repo)
    assert result["enabled"] is False
    assert store.get(mid).veracity == "true"
