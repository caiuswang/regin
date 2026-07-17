"""Content-drift detection: notice when a topic's code moved out from under
its wiki, and emit a human-gated refresh proposal.

Phase 0 fingerprints each topic ref at wiki-write time (`TopicRefDigest`).
Here we compare those digests to the files as they are now. A hash-changed
ref is only a *candidate*; materiality is judged by the first applicable
tier:

  * **wiki anchors (primary)** — when the digest row carries a non-empty
    set of wiki-cited identifier tokens that grounded to this ref at capture
    (`lib/topics/wiki_anchors.py`), the drift is material only if one of
    them vanished from the file. Every claim still grounding → spared,
    however much the bytes changed. An *empty* stored set (the wiki cites
    nothing in this ref) decides nothing — it falls through, or the ref
    would be permanently exempt from drift.
  * **cosine filter** — rows the anchor tier didn't decide, with BOTH a
    stored and a freshly-computed embedding: a high cosine
    (≥ `content_drift_cosine`) spares the change as trivial.
  * **bare hash change (fallback)** — rows with neither signal (captured
    before anchors existed, topic had no wiki) flag on the hash alone.

A drifted topic does not get its `status` mutated — `"stale"` isn't a valid
topic status, and the approved graph is human-approved. The drift signal lives in
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
from lib.topics.core import NON_DRIFTING_REF_TIERS
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


def _missing_anchors(content: str,
                     digest: dict[str, Any]) -> Optional[list[str]]:
    """The wiki-cited anchors stored on this digest that no longer appear in
    the ref's current content as whole identifiers (token-exact, mirroring
    `anchors_in_content` at capture — a substring survivor like `foo` inside
    `foo_v2` counts as vanished). None when the row predates anchor capture
    or the topic had no wiki then — the caller falls through to the older
    signals; an empty list means every wiki claim still grounds."""
    from lib.topics.wiki_anchors import content_identifier_tokens

    anchors = digest.get("anchors")
    if anchors is None:
        return None
    present = content_identifier_tokens(content)
    return [a for a in anchors if a not in present]


def _ref_is_material_drift(content: str, digest: dict[str, Any],
                           embedder, threshold: float) -> bool:
    """Whether a hash-changed ref is a *material* drift. The hash already
    differs (caller checked). When both a stored vector and an embedder are
    available, a high cosine spares it as a trivial edit; otherwise the
    hash-change alone stands. The stored vector is parsed lazily from its
    JSON here — most rows never reach this tier, so `digests_for_topic`
    doesn't pay to materialize every embedding up front."""
    import json

    raw_vec = digest.get("embedding_json")
    if (raw_vec is None or embedder is None
            or getattr(embedder, "model_id", None) is None):
        return True
    vecs = embedder.embed([content])
    if not vecs:
        return True
    return _cosine(list(vecs[0]), json.loads(raw_vec)) < threshold


def _judge_ref(repo_root: Path, ref: Any, stored: dict[str, dict[str, Any]],
               embedder, threshold: float
               ) -> Optional[tuple[str, Optional[list[str]]]]:
    """`(path, missing_anchors)` when one ref materially drifted, else None.
    A ref whose digest stores a non-empty anchor set is decided by that set
    alone: material iff a wiki-cited anchor vanished. An empty stored set
    (the wiki cites nothing grounding here) decides nothing — sparing on it
    would permanently exempt the ref — and falls through, like anchor-less
    legacy rows, to the cosine filter and then the bare hash change
    (`missing_anchors` is None for those)."""
    changed = _changed_content(repo_root, ref, stored)
    if changed is None:
        return None
    path, content, digest = changed
    missing = _missing_anchors(content, digest)
    if missing:
        return path, missing
    if missing is not None and digest.get("anchors"):
        return None                               # every cited claim grounds
    if _ref_is_material_drift(content, digest, embedder, threshold):
        return path, None
    return None


def _changed_content(repo_root: Path, ref: Any,
                     stored: dict[str, dict[str, Any]]
                     ) -> Optional[tuple[str, str, dict[str, Any]]]:
    """`(path, content, digest)` for a judgeable, hash-changed ref; None when
    the ref is reference-tier, undigested, unchanged, or deleted (deletion is
    Phase 2's cascade, not content drift)."""
    if not isinstance(ref, dict) or ref.get("tier") in NON_DRIFTING_REF_TIERS:
        return None
    path = ref.get("path")
    digest = stored.get(path)
    if not path or digest is None:
        return None
    content = _read(repo_root, path)
    if content is None:
        return None
    if _content_hash(content) == digest["content_hash"]:
        return None
    return path, content, digest


def _drifted_paths_for_topic(repo_root: Path, repo_id: int, topic_id: str,
                             topic: dict[str, Any], embedder,
                             threshold: float
                             ) -> tuple[list[str], dict[str, list[str]]]:
    """`(drifted_paths, missing_anchors_by_path)` for one topic — the ref
    paths whose content materially drifted from their stored digest. Empty
    when the topic was never digested (can't judge), nothing changed, or
    every change was spared as immaterial."""
    stored = {d["path"]: d for d in digests_for_topic(repo_id, topic_id)}
    if not stored:
        return [], {}
    drifted: list[str] = []
    missing_by_path: dict[str, list[str]] = {}
    for ref in topic.get("refs", []):
        judged = _judge_ref(repo_root, ref, stored, embedder, threshold)
        if judged is None:
            continue
        path, missing = judged
        drifted.append(path)
        if missing:
            missing_by_path[path] = missing
    return drifted, missing_by_path


def detect_drifted_topics(repo_path: str | Path, *,
                          embedder=None) -> list[dict[str, Any]]:
    """Topics whose ref files materially drifted from their stored digests.
    Returns `[{topic_id, drifted_paths}]`, plus a `missing_anchors`
    `{path: [tokens]}` key on rows where the drift was judged by vanished
    wiki-cited anchors; empty when nothing drifted or the repo is
    unregistered. Never raises."""
    try:
        repo_id = repo_id_for_path(repo_path)
        if repo_id is None:
            return []
        graph = load_authoritative_graph(repo_path)
        repo_root = Path(repo_path)
        threshold = settings.topic_evolution.content_drift_cosine
        out: list[dict[str, Any]] = []
        for topic_id, topic in graph.get("topics", {}).items():
            paths, missing = _drifted_paths_for_topic(
                repo_root, repo_id, topic_id, topic, embedder, threshold)
            if paths:
                row = {"topic_id": topic_id, "drifted_paths": paths}
                if missing:
                    row["missing_anchors"] = missing
                out.append(row)
        return out
    except Exception:  # noqa: BLE001 - detection must not break the caller
        log.error("content_drift_detect_failed", exc_info=True)
        return []


def _refresh_proposal_id(topic_id: str) -> str:
    """Deterministic id so re-emitting a refresh upserts the same proposal
    instead of stacking a new one each evolve pass."""
    return f"{REFRESH_PROVIDER}-{topic_id}"


def _drift_note_body(topic_id: str, drifted_paths: list[str],
                     missing_anchors: Optional[dict[str, list[str]]] = None
                     ) -> str:
    missing_anchors = missing_anchors or {}
    lines = []
    for p in drifted_paths:
        gone = missing_anchors.get(p)
        if gone:
            cited = ", ".join(f"`{a}`" for a in gone)
            lines.append(f"- `{p}` — the wiki cites {cited}, no longer present")
        else:
            lines.append(f"- `{p}`")
    listed = "\n".join(lines) or "- (this topic's refs)"
    return (
        f"The code under **{topic_id}** changed since its wiki was last "
        f"written. Re-check that the wiki's existing explanation still matches "
        f"these files and correct only what drifted. Keep the page's scope and "
        f"length — revise in place; do not add new file-by-file detail for the "
        f"changed files.\n\nChanged files:\n{listed}"
    )


def _append_drift_note_to_origin(repo_path: str | Path, origin_run_id: str,
                                 topic_id: str, drifted_paths: list[str],
                                 missing_anchors: Optional[dict[str, list[str]]] = None
                                 ) -> str:
    """Append (idempotently) an agent-authored content-drift note onto the
    proposal run that originally brought this topic into the graph. The open
    note rides the regenerate rail into the next draft — so the refresh lands
    as a new revision on the *original* proposal, giving the topic wiki a
    coherent revision history. Returns the origin run id.

    Idempotent: while a prior drift note for this topic is still open (not yet
    handed to a regenerate / resolved by a human), re-detecting the same drift
    is a no-op rather than a second stacked note. The evolve sweep retires the
    note (dismissed, baseline advanced) as soon as its regenerate is handed
    off; the `wiki_range` anchor keeps the auto-addressed sweep as the fallback
    close for a note whose wiki changes via a manual regenerate, so the next
    genuine drift opens a fresh note → a fresh revision either way."""
    from lib.topics.proposal_orm import (
        orm_create_feedback_thread,
        orm_open_content_drift_threads,
    )

    existing = orm_open_content_drift_threads(
        repo_path, kind=CONTENT_DRIFT_THREAD_KIND,
        proposal_id=origin_run_id, topic_id=topic_id)
    if existing:
        return origin_run_id
    metadata: dict[str, Any] = {"drifted_paths": drifted_paths}
    if missing_anchors:
        metadata["missing_anchors"] = missing_anchors
    orm_create_feedback_thread(
        repo_path, origin_run_id,
        proposal_topic_id=topic_id,
        kind=CONTENT_DRIFT_THREAD_KIND,
        anchor_kind="wiki_range",
        anchor={"topic_id": topic_id, "section": "wiki-preview"},
        quoted_text=None,
        body=_drift_note_body(topic_id, drifted_paths, missing_anchors),
        created_by="agent",
        metadata=metadata,
    )
    log.write("content_drift_note_appended", repo_path=str(repo_path),
              proposal_id=origin_run_id, topic_id=topic_id,
              drifted=len(drifted_paths))
    return origin_run_id


def emit_refresh_proposal(repo_path: str | Path, topic_id: str,
                          drifted_paths: list[str],
                          missing_anchors: Optional[dict[str, list[str]]] = None
                          ) -> Optional[str]:
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
            repo_path, origin_run_id, topic_id, drifted_paths, missing_anchors)

    proposal_id = _refresh_proposal_id(topic_id)
    snapshot = dict(topic)
    snapshot["id"] = topic_id
    snapshot.setdefault("status", "active")
    metadata: dict[str, Any] = {"kind": "refresh",
                                "drifted_paths": drifted_paths}
    if missing_anchors:
        metadata["missing_anchors"] = missing_anchors
    proposal = {
        "provider": REFRESH_PROVIDER,
        "scope": "all",
        "status": "pending_review",
        "topics": [snapshot],
        "metadata": metadata,
    }
    orm_save_proposal(repo_path, proposal_id, proposal,
                      wiki=_drift_note_body(topic_id, drifted_paths,
                                            missing_anchors))
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


def judge_note_drift(repo_path: str | Path, topic_id: str, note: str, *,
                     author_kind: str = "agent") -> int:
    """Append a drift-judge review note to every open content-drift thread
    for the topic, leaving them open — the regenerate carry-forward rail then
    surfaces the note to the redrafting agent, and the evolve sweep dismisses
    the thread once that handoff happens. Returns the number of threads
    commented (0 when none are open, e.g. a standalone stub with no origin
    run)."""
    from lib.topics.proposal_orm import (orm_add_feedback_comment,
                                         orm_open_content_drift_threads)

    commented = 0
    for thread in orm_open_content_drift_threads(
            repo_path, kind=CONTENT_DRIFT_THREAD_KIND, topic_id=topic_id):
        if orm_add_feedback_comment(
                repo_path, thread["run_id"], thread["thread_id"],
                body=note, author_kind=author_kind) is not None:
            commented += 1
    if commented:
        log.write("content_drift_noted", topic_id=topic_id,
                  threads_commented=commented)
    return commented


def judge_dismiss_drift(repo_path: str | Path, topic_id: str,
                        reason: str = "", *,
                        author_kind: str = "agent") -> dict[str, Any]:
    """Dismiss a topic's content drift *with its reason on the record*: the
    reason lands as a comment on every open drift thread first, then
    `dismiss_content_drift` retires both surfaces and advances the baseline.
    Idempotent — once the threads are closed a second call comments nothing
    and re-dismissing is a no-op."""
    commented = judge_note_drift(repo_path, topic_id, reason,
                                 author_kind=author_kind) if reason else 0
    result = dismiss_content_drift(repo_path, topic_id)
    result["threads_commented"] = commented
    return result


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
            if emit_refresh_proposal(repo_path, tid, item["drifted_paths"],
                                     missing_anchors=item.get("missing_anchors")):
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
           "judge_dismiss_drift", "judge_note_drift",
           "emit_refresh_proposal", "run_content_evolution",
           "CONTENT_DRIFT_THREAD_KIND"]
