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
import os
from pathlib import Path
from typing import Any, Optional

from lib.activity_log import get_activity_logger
from lib.settings import settings
from lib.topics.graph_io import load_authoritative_graph
from lib.topics.ref_digest import digests_for_topic, repo_id_for_path

log = get_activity_logger("topics")

REFRESH_PROVIDER = "content-drift"

# Feedback-thread kind used for the agent-authored drift note appended to a
# topic's origin proposal run. The regenerate rail carries open threads into
# the next draft, so this note IS the producer that drives a refresh revision
# onto the original proposal — keeping the topic's whole wiki history in one
# run instead of spawning a divorced `content-drift-<topic>` proposal.
CONTENT_DRIFT_THREAD_KIND = "content_drift"

# Synthetic session the content-drift inbox cards live under (the notify
# bus groups system events here). The card key is repo-scoped so a
# same-named topic in a different repo can't collide — the inbox dedups on
# `(trace_id, msg_key)`, so an unscoped key would drop the second repo's
# drift and mis-point its deep-link.
DRIFT_CARD_TRACE = "wiki-debt"


def drift_card_key(repo_path: str | Path, topic_id: str) -> str:
    """Inbox card key for a topic's content-drift notification, scoped by
    repo basename so two repos with the same topic id don't collide."""
    repo = os.path.basename(os.path.realpath(str(repo_path)))
    return f"content-drift:{repo}:{topic_id}"


def resolve_drift_card(repo_path: str | Path, topic_id: str) -> None:
    """Dismiss the live content-drift inbox card once its drift is handled
    (the refresh was applied, or the drift dismissed as unrelated), so a
    `once`-gated card doesn't linger — and a *later* drift on the same topic
    can surface a fresh card. Best-effort; a no-op when no card is live."""
    from lib.agent_messages import events
    events.resolve(DRIFT_CARD_TRACE, drift_card_key(repo_path, topic_id))


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


def _drift_note_body(topic_id: str, drifted_paths: list[str]) -> str:
    listed = "\n".join(f"- `{p}`" for p in drifted_paths) or "- (this topic's refs)"
    return (
        f"The code under **{topic_id}** changed since its wiki was last "
        f"written. Re-derive the narrative from the current refs.\n\n"
        f"Drifted files:\n{listed}"
    )


def _append_drift_note_to_origin(repo_path: str | Path, origin_run_id: str,
                                 topic_id: str,
                                 drifted_paths: list[str]) -> str:
    """Append (idempotently) an agent-authored content-drift note onto the
    proposal run that originally brought this topic into the graph. The open
    note rides the regenerate rail into the next draft — so the refresh lands
    as a new revision on the *original* proposal, giving the topic wiki a
    coherent revision history. Returns the origin run id.

    Idempotent: while a prior drift note for this topic is still open (not yet
    addressed by a refresh / resolved by a human), re-detecting the same drift
    is a no-op rather than a second stacked note. A `wiki_range` anchor means
    the auto-addressed sweep closes the note once the wiki actually changes, so
    the next genuine drift opens a fresh note → a fresh revision."""
    from lib.topics.proposal_orm import (
        orm_create_feedback_thread,
        orm_open_content_drift_threads,
    )

    existing = orm_open_content_drift_threads(
        repo_path, kind=CONTENT_DRIFT_THREAD_KIND,
        proposal_id=origin_run_id, topic_id=topic_id)
    if existing:
        return origin_run_id
    orm_create_feedback_thread(
        repo_path, origin_run_id,
        proposal_topic_id=topic_id,
        kind=CONTENT_DRIFT_THREAD_KIND,
        anchor_kind="wiki_range",
        anchor={"topic_id": topic_id, "section": "wiki-preview"},
        quoted_text=None,
        body=_drift_note_body(topic_id, drifted_paths),
        created_by="agent",
        metadata={"drifted_paths": drifted_paths},
    )
    log.write("content_drift_note_appended", repo_path=str(repo_path),
              proposal_id=origin_run_id, topic_id=topic_id,
              drifted=len(drifted_paths))
    return origin_run_id


def emit_refresh_proposal(repo_path: str | Path, topic_id: str,
                          drifted_paths: list[str]) -> Optional[str]:
    """Record a content-drift refresh for a drifted topic; returns the
    proposal-run id that now carries it, or None when the topic is unknown.

    Prefers appending an agent-authored drift **note** onto the topic's origin
    proposal run (found via provenance) — that note drives a refresh *revision*
    on the original proposal, so the topic wiki keeps a single revision
    history (mirrors the downgrade-into-origin path). Falls back to the
    standalone, idempotent `content-drift-<topic>` proposal only when the topic
    has no origin run (legacy snapshots, or the origin run was deleted).
    Single-topic by design (a multi-topic proposal loses forward edges on
    per-doc apply)."""
    from lib.topics.proposal_orm import orm_find_origin_proposal_run_for_topic
    from lib.topics.proposal_orm.runs import orm_save_proposal

    graph = load_authoritative_graph(repo_path)
    topic = graph.get("topics", {}).get(topic_id)
    if not topic:
        return None

    origin_run_id = orm_find_origin_proposal_run_for_topic(repo_path, topic_id)
    if origin_run_id is not None:
        return _append_drift_note_to_origin(
            repo_path, origin_run_id, topic_id, drifted_paths)

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


def _ignore_standalone_refresh(repo_path: str | Path, topic_id: str) -> bool:
    """Dismiss the standalone `content-drift-<topic>` refresh proposal (the
    fallback surface for a drifted topic with no origin run) by marking its
    single topic ignored. Returns whether one was dismissed.

    A no-op for the common case: a topic whose drift routed to an origin-run
    note has no standalone proposal, so `load_proposal` misses (caught) and this
    returns False. The `ignore_proposed_topic` call runs only after the
    existence / `pending_review` / un-reviewed guards pass, so it does not raise
    here (default `rebaseline_drift=False`; the digest was already advanced by
    the `capture_ref_digests` at the top of `dismiss_content_drift`)."""
    from lib.topics.proposals import ignore_proposed_topic, load_proposal

    proposal_id = _refresh_proposal_id(topic_id)
    try:
        proposal = load_proposal(repo_path, proposal_id)
    except Exception:  # noqa: BLE001 - no standalone proposal → nothing to dismiss
        return False
    if proposal.get("status") != "pending_review":
        return False
    topic = next((t for t in proposal.get("topics", [])
                  if t.get("id") == topic_id), None)
    if topic is None or topic.get("review_status"):
        return False
    ignore_proposed_topic(repo_path, proposal_id, topic_id)
    return True


def dismiss_content_drift(repo_path: str | Path, topic_id: str, *,
                          embedder=None) -> dict[str, Any]:
    """Mark a topic's content drift as *unrelated to its wiki*: advance the
    drift baseline (re-fingerprint the topic's refs so detection stops flagging
    them) and dismiss the drift signal on **both** surfaces — every open
    content-drift note (origin-run path) *and* the standalone
    `content-drift-<topic>` refresh proposal (no-origin-run fallback path).

    This is the escape hatch for a ref edit that didn't change what the wiki
    documents. Dismissing the signal alone doesn't stick: the stored
    `TopicRefDigest.content_hash` stays stale, so the next `run_content_evolution`
    pass re-detects the same hash mismatch and — because `emit_refresh_proposal`
    only skips *open* notes — opens a fresh one, resurrecting the drift forever.
    Re-capturing the digest here is what actually retires it.

    Ungated and best-effort (the low-level `capture_ref_digests` never raises):
    the whole point of the action is to sync the baseline to current code on
    demand, so it must work regardless of `evolution_enabled`. Returns
    `{topic_id, digests_captured, threads_dismissed, proposal_ignored}` —
    `threads_dismissed` is the dismissed note ids (empty when none were open),
    `proposal_ignored` whether a standalone refresh proposal was dismissed."""
    from lib.topics.proposal_orm import (
        orm_open_content_drift_threads,
        orm_set_feedback_thread_resolution,
    )
    from lib.topics.ref_digest import capture_ref_digests

    captured = capture_ref_digests(repo_path, topic_id, embedder=embedder)
    dismissed: list[int] = []
    for thread in orm_open_content_drift_threads(
            repo_path, kind=CONTENT_DRIFT_THREAD_KIND, topic_id=topic_id):
        updated = orm_set_feedback_thread_resolution(
            repo_path, thread["run_id"], thread["thread_id"],
            resolution_state="dismissed")
        if updated is not None:
            dismissed.append(thread["thread_id"])
    proposal_ignored = _ignore_standalone_refresh(repo_path, topic_id)
    resolve_drift_card(repo_path, topic_id)
    log.write("content_drift_dismissed", topic_id=topic_id,
              digests_captured=captured, threads_dismissed=len(dismissed),
              proposal_ignored=proposal_ignored)
    return {"topic_id": topic_id, "digests_captured": captured,
            "threads_dismissed": dismissed, "proposal_ignored": proposal_ignored}


def run_content_evolution(repo_path: str | Path, *,
                          embedder=None) -> dict[str, Any]:
    """One content-drift evolve pass: detect drifted topics, cascade staleness
    onto their linked memories, and emit a refresh proposal per topic — capped
    at `drift_proposal_batch_max` so one big change can't flood the queue.
    Gated on `evolution_enabled` (no-op dict when off) and best-effort."""
    if not settings.topic_evolution.evolution_enabled:
        return {"enabled": False, "drifted": 0, "proposals": 0,
                "memories_staled": 0, "capped": 0, "spawned": 0, "expired": 0}
    try:
        from lib.memory import get_store
        from lib.memory.topic_cascade import cascade_topic_stale
        from lib.topics.proposal_expiry import expire_stale_auto_proposals

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
        # Optionally hand the fresh refresh proposals to the drafting agent
        # (doubly gated, off by default), then prune the stale end of the
        # queue so it can't rot.
        from lib.topics.agent_spawn import maybe_spawn_refresh_agents
        spawned = maybe_spawn_refresh_agents(repo_path)
        expired = expire_stale_auto_proposals(repo_path)
        log.write("content_evolution_run", drifted=len(drifted),
                  proposed=proposals, memories_staled=staled,
                  capped=len(drifted) - len(batch), spawned=spawned,
                  expired=expired)
        return {"enabled": True, "drifted": len(drifted), "proposals": proposals,
                "memories_staled": staled, "capped": len(drifted) - len(batch),
                "spawned": spawned, "expired": expired}
    except Exception:  # noqa: BLE001 - evolve must never break a cron/CLI caller
        log.error("content_evolution_failed", exc_info=True)
        return {"enabled": True, "drifted": 0, "proposals": 0,
                "memories_staled": 0, "capped": 0, "spawned": 0, "expired": 0,
                "error": True}


__all__ = ["detect_drifted_topics", "dismiss_content_drift",
           "emit_refresh_proposal", "run_content_evolution",
           "CONTENT_DRIFT_THREAD_KIND"]
