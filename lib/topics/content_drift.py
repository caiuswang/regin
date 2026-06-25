"""Content-drift detection: notice when a topic's code moved out from under
its wiki, and emit a human-gated refresh proposal.

Phase 0 fingerprints each topic ref at wiki-write time (`TopicRefDigest`).
Here we compare those digests to the files as they are now:

  * **hash path (default)** — a ref whose content hash changed since the wiki
    was digested is a drift candidate. Coarse but always available (digests are
    captured hash-only on accept).
  * **cosine filter (refinement)** — when BOTH the stored and a freshly-computed
    embedding exist, a high cosine (≥ `content_drift_cosine`) *spares* a
    hash-changed ref as a trivial edit; only a materially-changed ref
    (cosine below the floor) stays flagged.

A drifted topic does not get its `status` mutated — `"stale"` isn't a valid
topic status, and `topic.json` is human-approved. The drift signal lives in
(a) the emitted single-topic refresh **proposal** (`pending_review`) and
(b) the `topic_drift` markers the Phase 2 cascade stamps on linked memories.

Single-topic proposals only — a multi-topic proposal applied one doc at a time
silently drops forward edges, so we never put more than one topic in a refresh.

Everything is gated on `settings.topic_evolution.evolution_enabled` (off by
default) and best-effort: evolution must never raise into a CLI or cron caller.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, Optional

from lib.activity_log import get_activity_logger
from lib.settings import settings
from lib.topics.graph_io import load_authoritative_graph
from lib.topics.ref_digest import digests_for_topic, repo_id_for_path

log = get_activity_logger("topics")

REFRESH_PROVIDER = "content-drift"


def _content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _read(repo_root: Path, path: str) -> Optional[str]:
    target = repo_root / path
    if not target.is_file():
        return None
    try:
        return target.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def _cosine(a: list[float], b: list[float]) -> float:
    """Cosine similarity of two vectors; 0.0 on degenerate input."""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(y * y for y in b) ** 0.5
    return dot / (na * nb) if na and nb else 0.0


def _ref_is_material_drift(content: str, digest: dict[str, Any],
                           embedder, threshold: float) -> bool:
    """Whether a hash-changed ref is a *material* drift. The hash already
    differs (caller checked). When both a stored vector and an embedder are
    available, a high cosine spares it as a trivial edit; otherwise the
    hash-change alone stands."""
    stored_vec = digest.get("embedding")
    if (stored_vec is None or embedder is None
            or getattr(embedder, "model_id", None) is None):
        return True
    vecs = embedder.embed([content])
    if not vecs:
        return True
    return _cosine(list(vecs[0]), stored_vec) < threshold


def _drifted_paths_for_topic(repo_root: Path, repo_id: int, topic_id: str,
                             topic: dict[str, Any], embedder,
                             threshold: float) -> list[str]:
    """Ref paths of one topic whose content materially drifted from its stored
    digest. Empty when the topic was never digested (can't judge), nothing
    changed, or every change was spared by the cosine filter. Deleted refs are
    skipped — that is Phase 2's deletion cascade, not content drift."""
    stored = {d["path"]: d for d in digests_for_topic(repo_id, topic_id)}
    if not stored:
        return []
    drifted: list[str] = []
    for ref in topic.get("refs", []):
        if not isinstance(ref, dict):
            continue
        path = ref.get("path")
        digest = stored.get(path)
        if not path or digest is None:
            continue
        content = _read(repo_root, path)
        if content is None:                       # deleted → Phase 2, not here
            continue
        if _content_hash(content) == digest["content_hash"]:
            continue                              # unchanged
        if _ref_is_material_drift(content, digest, embedder, threshold):
            drifted.append(path)
    return drifted


def detect_drifted_topics(repo_path: str | Path, *,
                          embedder=None) -> list[dict[str, Any]]:
    """Topics whose ref files materially drifted from their stored digests.
    Returns `[{topic_id, drifted_paths}]`; empty when nothing drifted or the
    repo is unregistered. Never raises."""
    try:
        repo_id = repo_id_for_path(repo_path)
        if repo_id is None:
            return []
        graph = load_authoritative_graph(repo_path)
        repo_root = Path(repo_path)
        threshold = settings.topic_evolution.content_drift_cosine
        out: list[dict[str, Any]] = []
        for topic_id, topic in graph.get("topics", {}).items():
            paths = _drifted_paths_for_topic(
                repo_root, repo_id, topic_id, topic, embedder, threshold)
            if paths:
                out.append({"topic_id": topic_id, "drifted_paths": paths})
        return out
    except Exception:  # noqa: BLE001 - detection must not break the caller
        log.error("content_drift_detect_failed", exc_info=True)
        return []


def _refresh_proposal_id(topic_id: str) -> str:
    """Deterministic id so re-emitting a refresh upserts the same proposal
    instead of stacking a new one each evolve pass."""
    return f"{REFRESH_PROVIDER}-{topic_id}"


def emit_refresh_proposal(repo_path: str | Path, topic_id: str,
                          drifted_paths: list[str]) -> Optional[str]:
    """Emit (idempotent UPSERT) a single-topic, human-gated refresh proposal
    for a drifted topic, snapshotting its current approved entry. Returns the
    proposal id, or None when the topic is unknown. Single-topic by design (a
    multi-topic proposal loses forward edges on per-doc apply)."""
    from lib.topics.proposal_orm.runs import orm_save_proposal

    graph = load_authoritative_graph(repo_path)
    topic = graph.get("topics", {}).get(topic_id)
    if not topic:
        return None
    proposal_id = _refresh_proposal_id(topic_id)
    snapshot = dict(topic)
    snapshot["id"] = topic_id
    snapshot.setdefault("status", "active")
    proposal = {
        "provider": REFRESH_PROVIDER,
        "scope": "all",
        "status": "pending_review",
        "topics": [snapshot],
        "metadata": {"kind": "refresh", "drifted_paths": drifted_paths},
    }
    wiki = (f"The code under **{topic_id}** changed since its wiki was last "
            f"written. Re-derive the narrative from the current refs.\n\n"
            f"Drifted files:\n"
            + "\n".join(f"- `{p}`" for p in drifted_paths))
    orm_save_proposal(repo_path, proposal_id, proposal, wiki=wiki)
    return proposal_id


def run_content_evolution(repo_path: str | Path, *,
                          embedder=None) -> dict[str, Any]:
    """One content-drift evolve pass: detect drifted topics, cascade staleness
    onto their linked memories, and emit a refresh proposal per topic — capped
    at `drift_proposal_batch_max` so one big change can't flood the queue.
    Gated on `evolution_enabled` (no-op dict when off) and best-effort."""
    if not settings.topic_evolution.evolution_enabled:
        return {"enabled": False, "drifted": 0, "proposals": 0,
                "memories_staled": 0, "capped": 0}
    try:
        from lib.memory import get_store
        from lib.memory.topic_cascade import cascade_topic_stale

        drifted = detect_drifted_topics(repo_path, embedder=embedder)
        cap = settings.topic_evolution.drift_proposal_batch_max
        batch = drifted[:cap] if cap and cap > 0 else drifted
        store = get_store()
        proposals = 0
        staled = 0
        for item in batch:
            tid = item["topic_id"]
            staled += cascade_topic_stale(store, tid, reason="content_drift")
            if emit_refresh_proposal(repo_path, tid, item["drifted_paths"]):
                proposals += 1
        log.write("content_evolution_run", drifted=len(drifted),
                  proposed=proposals, memories_staled=staled,
                  capped=len(drifted) - len(batch))
        return {"enabled": True, "drifted": len(drifted), "proposals": proposals,
                "memories_staled": staled, "capped": len(drifted) - len(batch)}
    except Exception:  # noqa: BLE001 - evolve must never break a cron/CLI caller
        log.error("content_evolution_failed", exc_info=True)
        return {"enabled": True, "drifted": 0, "proposals": 0,
                "memories_staled": 0, "capped": 0, "error": True}


__all__ = ["detect_drifted_topics", "emit_refresh_proposal",
           "run_content_evolution"]
