"""Atomic apply for a topic-graph diff.

`apply_diff` is the single write path that accept / merge / replace
(and, in later phases, the new `/apply` endpoint) all route through.
It commits the new state to two stores in one logical transaction:

  1. ORM: insert `GraphSnapshot` (with `is_latest=1`) + `TopicAudit`
     provenance rows, after flipping the prior snapshot to
     `is_latest=0`.
  2. Filesystem: rewrite `topic.json` (and per-topic wiki files when
     Phase C+ produces them) atomically.

Cross-store atomicity protocol:

- Open SQL transaction; flip prior snapshot to `is_latest=0` and
  `flush()` so the partial unique index doesn't reject the new
  `is_latest=1` insert. Then insert the new snapshot and provenance.
- `flush()` again — at this point everything is staged in SQL but
  not committed.
- Run the atomic disk write (write-temp + fsync + rename). If it
  fails, the SQL TX rolls back; readers continue to see the prior
  consistent state.
- SQL commits LAST. If the process dies after a successful disk
  write but before the SQL commit, the next read sees disk-ahead-of-SQL.
  `graph_io.reconcile_if_drifted` re-exports from SQL on demand —
  but Phase A keeps `topic.json` as the source of truth, so the
  divergence simply means the disk has slightly fresher data than the
  ORM. That's a no-op for Phase A readers.

Provenance: every alias/ref add (or, for `replace`, removal) gets a
`TopicAudit(kind="provenance")` row tagged with the triggering run
and proposed-topic id. The bulk-fix tool in Phase F queries these to
answer "show me everything proposal Z contributed".
"""

from __future__ import annotations

import copy
import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from sqlalchemy import update as sa_update
from sqlmodel import Session, select

from lib.orm import SessionLocal
from lib.orm.models import GraphSnapshot, ProposalTopic, Repo, TopicAudit
from lib.topics.core import normalize as _normalize_alias
from lib.topics.diff import GraphDiff, TopicDelta, compute_topic_delta, serialize_topic_delta
from lib.topics.graph_io import export_overlay_to_disk, repo_path_for
from lib.settings import settings
from lib.topics.snapshots import latest_snapshot, prune_snapshots
from lib.topics.validation import audit_graph


log = logging.getLogger(__name__)


# Strategy precondition error codes emitted by `diff_against_graph`.
# These describe a structurally impossible op (replacing a topic that
# doesn't exist, creating one that already does, etc.) — `audit_graph`
# never produces them, so `resolve_diff_with_options` has to carry them
# forward by code or they get silently dropped.
_STRATEGY_PRECONDITION_CODES = frozenset({
    "topic.id_collides_with_approved",
    "topic.replace_target_missing",
    "topic.merge_target_required",
    "topic.merge_target_missing",
})


def _best_effort_prune(repo_id: int, *, session: Optional[Session]) -> None:
    keep = settings.topic_snapshot_keep
    if keep <= 0:
        return
    try:
        prune_snapshots(repo_id, keep=keep, session=session)
    except Exception as exc:  # noqa: BLE001 — apply must not regress on prune
        log.warning(
            "snapshot prune best-effort failed (repo_id=%s, keep=%s): %s",
            repo_id, keep, exc,
        )


@dataclass(frozen=True)
class ApplyOptions:
    """Caller toggles for diff-mutation before apply.

    Defaults are calibrated to match legacy behaviour:
    - `prune_orphan_edges=True` — `_approved_topic_from_proposal` already
      silently dropped edges whose target wasn't in the graph; the new
      diff layer surfaces them as `graph.orphan_edge_target` errors, so
      we drop them by default to keep accept/merge flows working.
    - `drop_dead_refs=False` — legacy `validate()` blocked accept on dead
      refs, and the refactor's goal is to make that pain *one click to
      resolve*, not zero-click. The UI defaults the checkbox to checked
      in Phase C; the diff layer's job is to make damage visible.
    - `dedupe_aliases=False` — within-topic normalize-duplicates are
      always collapsed at the shape layer (`diff._approved_shape`), so
      they never reach the graph regardless of this flag. Turning it on
      additionally filters *cross-topic* collisions (an alias a sibling
      topic already owns) — that's destructive (you lose a meaningful
      alias), so it stays opt-in.
    """

    prune_orphan_edges: bool = True
    drop_dead_refs: bool = False
    dedupe_aliases: bool = False


@dataclass(frozen=True)
class DroppedItems:
    """What `resolve_diff_with_options` filtered out of a diff.

    Sibling to GraphDiff (not embedded) so the diff stays focused on
    structural state. The /diff and /apply endpoints return both so the
    UI can render a "X items silently dropped" banner.
    """

    orphan_edges: tuple[tuple[str, str, str], ...] = ()  # (topic_id, target, type)
    dead_refs: tuple[tuple[str, str, str], ...] = ()      # (topic_id, path, role)
    duplicate_aliases: tuple[tuple[str, str], ...] = ()   # (topic_id, alias)

    @property
    def is_empty(self) -> bool:
        return not (self.orphan_edges or self.dead_refs or self.duplicate_aliases)

    def to_json(self) -> dict[str, Any]:
        return {
            "orphan_edges": [
                {"topic_id": t, "target": tgt, "type": ty}
                for t, tgt, ty in self.orphan_edges
            ],
            "dead_refs": [
                {"topic_id": t, "path": p, "role": r}
                for t, p, r in self.dead_refs
            ],
            "duplicate_aliases": [
                {"topic_id": t, "alias": a}
                for t, a in self.duplicate_aliases
            ],
        }


@dataclass
class ApplyResult:
    snapshot_id: int
    snapshot: GraphSnapshot
    applied_diff: GraphDiff
    provenance_count: int
    exported_path: Optional[str]


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _flip_prior_to_not_latest(session: Session, repo_id: int) -> Optional[GraphSnapshot]:
    """Step 1 of the protocol: clear the existing `is_latest=1` row.

    Returns the prior snapshot (or None on first apply for a repo). The
    caller MUST flush after this and BEFORE inserting the new latest —
    the partial unique index fires on INSERT, not on commit, so an
    in-flight session with two `is_latest=1` rows will fail.
    """
    prior = latest_snapshot(repo_id, session=session)
    # Demote EVERY is_latest=1 row in a single statement, not just `prior`.
    # A transient second latest row can appear when snapshots are written
    # in rapid succession across short-lived sessions; clearing them all
    # here keeps the following INSERT from colliding with the partial
    # unique index `ux_graph_snapshots_repo_latest`.
    session.execute(
        sa_update(GraphSnapshot)
        .where(GraphSnapshot.repo_id == repo_id)
        .where(GraphSnapshot.is_latest == 1)
        .values(is_latest=0)
    )
    session.flush()
    return prior


def _summarize_diff(diff: GraphDiff) -> dict[str, Any]:
    """Short human-readable summary for `GraphSnapshot.diff_summary_json`."""
    deltas = [serialize_topic_delta(d) for d in diff.topic_deltas]
    return {
        "strategy": diff.strategy,
        "target_topic_id": diff.target_topic_id,
        "proposed_topic_id": diff.proposed_topic_id,
        "topic_count": len(deltas),
        "alias_adds": sum(len(d["alias_adds"]) for d in deltas),
        "ref_adds": sum(len(d["ref_adds"]) for d in deltas),
        "edge_adds": sum(len(d["edge_adds"]) for d in deltas),
    }


def _provenance_rows_for_delta(
    repo_id: int,
    delta: TopicDelta,
    *,
    triggering_run_id: Optional[str],
    triggering_proposal_topic_id: Optional[int],
) -> list[TopicAudit]:
    """Provenance rows for one delta's add/remove sets.

    Codes:
      `topic_<kind>`          — baseline row per delta (always emitted)
      `alias_added_by_<kind>` / `alias_removed_by_<kind>`
      `ref_added_by_<kind>`   / `ref_removed_by_<kind>`
      `edge_added_by_<kind>`  / `edge_removed_by_<kind>`

    The `<kind>` suffix lets the bulk-fix tool filter by the operation
    that introduced each artefact ("show me everything `replace` added").
    The baseline `topic_<kind>` row exists so the
    downgrade origin-lookup (orm_find_origin_proposal_run_for_topic) can
    locate the proposal even for topics created with no aliases / refs /
    edges. Without it, a content-empty create produced zero rows.
    """
    rows: list[TopicAudit] = []
    kind = delta.kind  # "create" | "replace" | "merge"

    def _row(code: str, message: str, *, aliases=(), paths=()) -> TopicAudit:
        return TopicAudit(
            repo_id=repo_id,
            kind="provenance",
            recorded_at=_utc_now(),
            severity="info",
            code=code,
            message=message,
            topic_ids_json=json.dumps([delta.topic_id]),
            paths_json=json.dumps(list(paths)),
            aliases_json=json.dumps(list(aliases)),
            triggering_run_id=triggering_run_id,
            triggering_proposal_topic_id=triggering_proposal_topic_id,
        )

    rows.append(_row(
        f"topic_{kind}",
        f"topic {delta.topic_id} touched via {kind}",
    ))

    for alias in delta.alias_adds:
        rows.append(_row(
            f"alias_added_by_{kind}",
            f"alias {alias!r} added to {delta.topic_id} via {kind}",
            aliases=(alias,),
        ))
    for alias in delta.alias_removes:
        rows.append(_row(
            f"alias_removed_by_{kind}",
            f"alias {alias!r} removed from {delta.topic_id} via {kind}",
            aliases=(alias,),
        ))
    for path, role, tier in delta.ref_adds:
        rows.append(_row(
            f"ref_added_by_{kind}",
            f"ref {path!r} (role={role}, tier={tier}) added to {delta.topic_id} via {kind}",
            paths=(path,),
        ))
    for path, role, tier in delta.ref_removes:
        rows.append(_row(
            f"ref_removed_by_{kind}",
            f"ref {path!r} (role={role}, tier={tier}) removed from {delta.topic_id} via {kind}",
            paths=(path,),
        ))
    for target, etype in delta.edge_adds:
        rows.append(_row(
            f"edge_added_by_{kind}",
            f"edge -> {target} ({etype}) added to {delta.topic_id} via {kind}",
        ))
    for target, etype in delta.edge_removes:
        rows.append(_row(
            f"edge_removed_by_{kind}",
            f"edge -> {target} ({etype}) removed from {delta.topic_id} via {kind}",
        ))
    return rows


def _filter_orphan_edges(
    cleaned_topic: dict,
    new_topics: dict,
    topic_id: str,
) -> list[tuple[str, str, str]]:
    """Mutates `cleaned_topic['edges']`; returns dropped (topic_id, target, type)."""
    dropped: list[tuple[str, str, str]] = []
    kept: list[dict] = []
    for e in cleaned_topic.get("edges", []) or []:
        if not isinstance(e, dict):
            continue
        target = e.get("target")
        if isinstance(target, str) and target in new_topics:
            kept.append(e)
        else:
            dropped.append((topic_id, target or "", e.get("type", "related")))
    cleaned_topic["edges"] = kept
    return dropped


def _filter_dead_refs(
    cleaned_topic: dict,
    repo_path_obj: Path,
    topic_id: str,
) -> list[tuple[str, str, str]]:
    """Mutates `cleaned_topic['refs']`; returns dropped (topic_id, path, role)."""
    dropped: list[tuple[str, str, str]] = []
    kept: list[dict] = []
    for r in cleaned_topic.get("refs", []) or []:
        if not isinstance(r, dict):
            continue
        path = r.get("path")
        if isinstance(path, str) and (repo_path_obj / path).exists():
            kept.append(r)
        else:
            dropped.append((topic_id, path or "", r.get("role", "")))
    cleaned_topic["refs"] = kept
    return dropped


def _sibling_alias_keys(new_topics: dict, exclude_topic_id: str) -> set[str]:
    keys: set[str] = set()
    for tid, t in new_topics.items():
        if tid == exclude_topic_id:
            continue
        for a in t.get("aliases", []) or []:
            if isinstance(a, str) and a:
                keys.add(_normalize_alias(a))
    return keys


def _filter_duplicate_aliases(
    cleaned_topic: dict,
    new_topics: dict,
    topic_id: str,
) -> list[tuple[str, str]]:
    """Mutates `cleaned_topic['aliases']`; returns dropped (topic_id, alias).

    Drops both within-topic duplicates and cross-topic collisions with
    siblings already in the prospective graph.
    """
    sibling_keys = _sibling_alias_keys(new_topics, topic_id)
    seen_local: set[str] = set()
    dropped: list[tuple[str, str]] = []
    kept: list[str] = []
    for a in cleaned_topic.get("aliases", []) or []:
        if not isinstance(a, str) or not a:
            continue
        key = _normalize_alias(a)
        if key in seen_local or key in sibling_keys:
            dropped.append((topic_id, a))
            continue
        seen_local.add(key)
        kept.append(a)
    cleaned_topic["aliases"] = kept
    return dropped


@dataclass
class _DeltaResolution:
    new_delta: TopicDelta
    orphan_edges: list[tuple[str, str, str]]
    dead_refs: list[tuple[str, str, str]]
    duplicate_aliases: list[tuple[str, str]]


def _resolve_one_delta(
    delta: TopicDelta,
    options: ApplyOptions,
    new_topics: dict,
    repo_path_obj: Optional[Path],
) -> _DeltaResolution:
    if delta.after is None:
        return _DeltaResolution(delta, [], [], [])
    cleaned = copy.deepcopy(delta.after)
    orphans = (
        _filter_orphan_edges(cleaned, new_topics, delta.topic_id)
        if options.prune_orphan_edges else []
    )
    dead = (
        _filter_dead_refs(cleaned, repo_path_obj, delta.topic_id)
        if options.drop_dead_refs and repo_path_obj is not None else []
    )
    dups = (
        _filter_duplicate_aliases(cleaned, new_topics, delta.topic_id)
        if options.dedupe_aliases else []
    )
    new_topics[delta.topic_id] = cleaned
    new_delta = compute_topic_delta(
        topic_id_after=delta.topic_id,
        kind=delta.kind,
        before=delta.before,
        after=cleaned,
    )
    return _DeltaResolution(new_delta, orphans, dead, dups)


def _recompute_post_resolution_issues(
    diff: GraphDiff,
    new_graph: dict,
    repo_path_obj: Optional[Path],
) -> tuple[tuple, tuple]:
    """Re-audit the resolved graph and partition issues into (warnings, errors).

    `pre_keys = {i.identity for i in diff.graph_warnings}` is a subset
    of the true pre-state — it only carries forward issues that survived
    the original diff's apply. If resolution accidentally re-introduces
    an issue the original op resolved, that gets misclassified as
    introduced_error rather than graph_warning. Unlikely for the three
    flags resolve_diff_with_options exposes; flagged for any future
    filter that touches the same space.

    Strategy precondition errors (`topic.replace_target_missing` etc.)
    are synthesised by `diff_against_graph` — they never appear in
    `audit_graph` output. We carry them forward explicitly, otherwise a
    replace against a missing target / create over a colliding id would
    silently apply with zero deltas while still stamping the proposal
    as accepted.
    """
    pre_keys = {i.identity for i in diff.graph_warnings}
    post_issues = audit_graph(new_graph, repo_path=repo_path_obj)
    new_warnings = tuple(i for i in post_issues if i.identity in pre_keys)
    carried_pre_errors = tuple(
        i for i in diff.introduced_errors
        if i.code in _STRATEGY_PRECONDITION_CODES
    )
    new_errors = carried_pre_errors + tuple(
        i for i in post_issues
        if i.identity not in pre_keys and i.severity == "error"
    )
    return new_warnings, new_errors


def resolve_diff_with_options(
    diff: GraphDiff,
    options: ApplyOptions,
    *,
    repo_path: Optional[str] = None,
) -> tuple[GraphDiff, DroppedItems]:
    """Rewrite a raw diff into one that respects the resolution flags.

    Three filters, all opt-in via `options`:
      - `prune_orphan_edges`: drop edges whose target isn't in the
        prospective graph.
      - `drop_dead_refs`: drop refs whose path doesn't exist on disk.
        No-op when `repo_path is None`.
      - `dedupe_aliases`: drop aliases that collide with a sibling
        topic in the prospective graph (and within-topic duplicates).

    Returns `(resolved_diff, dropped_items)`. The resolved diff's
    `prospective_graph` reflects the post-resolution state and its
    `introduced_errors` are recomputed against it.
    """
    if not (options.prune_orphan_edges or options.drop_dead_refs or options.dedupe_aliases):
        return diff, DroppedItems()
    if diff.prospective_graph is None:
        return diff, DroppedItems()

    new_graph = copy.deepcopy(diff.prospective_graph)
    new_topics = new_graph.setdefault("topics", {})
    repo_path_obj = Path(repo_path) if repo_path else None

    orphans: list[tuple[str, str, str]] = []
    dead_refs: list[tuple[str, str, str]] = []
    dup_aliases: list[tuple[str, str]] = []
    new_deltas: list[TopicDelta] = []

    for delta in diff.topic_deltas:
        res = _resolve_one_delta(delta, options, new_topics, repo_path_obj)
        new_deltas.append(res.new_delta)
        orphans.extend(res.orphan_edges)
        dead_refs.extend(res.dead_refs)
        dup_aliases.extend(res.duplicate_aliases)

    new_warnings, new_errors = _recompute_post_resolution_issues(
        diff, new_graph, repo_path_obj,
    )

    resolved = GraphDiff(
        topic_deltas=tuple(new_deltas),
        graph_warnings=new_warnings,
        introduced_errors=new_errors,
        valid_strategies_by_topic=dict(diff.valid_strategies_by_topic),
        strategy=diff.strategy,
        target_topic_id=diff.target_topic_id,
        proposed_topic_id=diff.proposed_topic_id,
        prospective_graph=new_graph,
    )
    return resolved, DroppedItems(
        orphan_edges=tuple(orphans),
        dead_refs=tuple(dead_refs),
        duplicate_aliases=tuple(dup_aliases),
    )


def _log_topic_snapshot_applied(
    repo_id: int, reason: str, diff: GraphDiff,
    result: ApplyResult, triggering_run_id: Optional[str],
) -> None:
    from lib.activity_log import get_activity_logger
    get_activity_logger("topics").write(
        "topic_snapshot_applied",
        repo_id=repo_id,
        snapshot_id=result.snapshot_id,
        strategy=diff.strategy,
        reason=reason,
        deltas=len(diff.topic_deltas),
        provenance=result.provenance_count,
        triggering_run_id=triggering_run_id,
    )


def apply_diff(
    repo_id: int,
    diff: GraphDiff,
    *,
    options: Optional[ApplyOptions] = None,
    reason: str = "apply",
    triggering_run_id: Optional[str] = None,
    triggering_proposal_topic_id: Optional[int] = None,
    wiki_pages: Optional[dict[str, str]] = None,
    session: Optional[Session] = None,
) -> ApplyResult:
    """Commit a GraphDiff to ORM + filesystem atomically.

    `wiki_pages` is `{topic_id: markdown_body}`. Phase A always passes
    `None` (== `{}`); Phase C populates it from the new per-topic
    wiki generator.

    `session=None` is the production path: opens an owned session, runs
    the full protocol, commits. Tests pass a session to keep everything
    in their fixture's transaction.
    """
    # `options` is the Phase B contract — `/apply` endpoint will pass
    # the resolution-checkbox state through here. Phase A ignores it.
    if not diff.is_applyable:
        raise ValueError(
            f"diff has unresolved introduced errors: "
            f"{[e.code for e in diff.introduced_errors]}"
        )
    if diff.prospective_graph is None:
        raise ValueError("diff has no prospective_graph — cannot apply")

    def _run(s: Session) -> ApplyResult:
        repo_path = repo_path_for(repo_id, session=s)
        if repo_path is None:
            raise ValueError(f"repo {repo_id} has no Repo row")

        # Step 1: flip the prior snapshot OUT of is_latest, flush.
        prior = _flip_prior_to_not_latest(s, repo_id)

        # Step 2: insert the new snapshot with is_latest=1.
        snapshot = GraphSnapshot(
            repo_id=repo_id,
            taken_at=_utc_now(),
            reason=reason,
            triggering_run_id=triggering_run_id,
            triggering_proposal_topic_id=triggering_proposal_topic_id,
            graph_json=json.dumps(diff.prospective_graph),
            wiki_pages_json=json.dumps(wiki_pages or {}),
            diff_summary_json=json.dumps(_summarize_diff(diff)),
            pinned=0,
            is_latest=1,
        )
        s.add(snapshot)
        s.flush()  # gives us snapshot.id for provenance refs

        # Step 3: provenance rows for each delta.
        provenance: list[TopicAudit] = []
        for delta in diff.topic_deltas:
            rows = _provenance_rows_for_delta(
                repo_id,
                delta,
                triggering_run_id=triggering_run_id,
                triggering_proposal_topic_id=triggering_proposal_topic_id,
            )
            for r in rows:
                r.snapshot_id = snapshot.id
            provenance.extend(rows)
        for r in provenance:
            s.add(r)
        s.flush()

        # Step 4: filesystem write (atomic). The approved graph lands in the
        # gitignored `topic.local.json` overlay — the git-tracked base
        # `topic.json` is never touched by apply. The split keeps
        # `merge(base, overlay)` hash-equal to the snapshot below, so the
        # drift detector stays quiet. If this fails, the surrounding
        # transaction rolls back — readers stay on the prior state.
        exported_path = str(export_overlay_to_disk(
            repo_path, diff.prospective_graph, wiki_pages or {},
        ))

        log.info(
            "apply_diff: repo=%s reason=%s strategy=%s deltas=%d provenance=%d snapshot_id=%s",
            repo_id, reason, diff.strategy, len(diff.topic_deltas),
            len(provenance), snapshot.id,
        )
        _ = prior  # silence unused-var; kept for debugging readability
        return ApplyResult(
            snapshot_id=snapshot.id,
            snapshot=snapshot,
            applied_diff=diff,
            provenance_count=len(provenance),
            exported_path=exported_path,
        )

    if session is not None:
        result = _run(session)
        _best_effort_prune(repo_id, session=session)
        _log_topic_snapshot_applied(repo_id, reason, diff, result, triggering_run_id)
        return result
    with SessionLocal() as s:
        result = _run(s)
        s.commit()
        s.refresh(result.snapshot)
    _log_topic_snapshot_applied(repo_id, reason, diff, result, triggering_run_id)
    # Best-effort prune in its own session AFTER the apply commits, so
    # a pruning hiccup never costs us the apply itself.
    _best_effort_prune(repo_id, session=None)

    # Refresh the wiki dense index in a background thread so the accept
    # HTTP response returns immediately. Cold-start embed-model load is
    # ~10s; we don't want that on the critical path. The user can also
    # force a sync refresh via the "Re-index Wikis" button (WebUI) or
    # `regin wiki index --repo NAME` (CLI). Best-effort: a missing
    # embedding model never affects the accept.
    #
    # We pass `graph=diff.prospective_graph` so the bg thread skips
    # `load_authoritative_graph`. Without it, a second `apply_diff` can
    # run between the bg thread's snapshot read and its disk read; the
    # bg thread then sees `snap_hash != disk_hash` and triggers
    # `_auto_seed_snapshot`, which inserts an extra `is_latest=1` row
    # and breaks the per-repo snapshot-count invariant.
    indexed_graph = diff.prospective_graph
    try:
        import threading
        from lib.patterns.wiki_indexer import index_wikis_best_effort
        with SessionLocal() as s2:
            repo_row = s2.exec(select(Repo).where(Repo.id == repo_id)).first()
        if repo_row is not None:
            def _bg_reindex():
                try:
                    index_wikis_best_effort(
                        repo_row,
                        progress=lambda m: log.debug("wiki indexer: %s", m),
                        graph=indexed_graph,
                    )
                except Exception as e:  # noqa: BLE001
                    log.warning(
                        "background wiki reindex crashed (repo_id=%s): %s",
                        repo_id, e,
                    )
            threading.Thread(
                target=_bg_reindex,
                name=f"wiki-index-{repo_id}",
                daemon=True,
            ).start()
    except Exception as exc:  # noqa: BLE001 — accept must not regress on indexing
        log.warning("wiki reindex scheduling failed (repo_id=%s): %s", repo_id, exc)
    return result


def mark_proposal_topic_reviewed(  # unused until Phase D's ORM-backed proposals
    proposal_topic_pk: int,
    *,
    review_status: str,
    accepted_topic_id: Optional[str] = None,
    merged_topic_id: Optional[str] = None,
    replaced_existing: bool = False,
    session: Optional[Session] = None,
) -> None:
    """Update a ProposalTopic row after an apply.

    Mirrors what `accept_proposed_topic`/`merge_proposed_topic`/
    `replace_approved_topic` write back to `proposal.json` on disk
    today. Until Phase D the file backend is still authoritative for
    proposals — this writes to the ORM table in parallel so Phase D's
    flip has data to read.
    """
    def _run(s: Session) -> None:
        row = s.get(ProposalTopic, proposal_topic_pk)
        if row is None:
            return
        row.review_status = review_status
        now = _utc_now()
        if review_status == "accepted":
            row.accepted_topic_id = accepted_topic_id
            row.accepted_at = now
        elif review_status == "merged":
            row.merged_topic_id = merged_topic_id
            row.merged_at = now
        elif review_status == "ignored":
            row.ignored_at = now
        if replaced_existing:
            row.replaced_existing = 1
        s.add(row)
        s.flush()

    if session is not None:
        _run(session)
        return
    with SessionLocal() as s:
        _run(s)
        s.commit()


__all__ = [
    "ApplyOptions",
    "ApplyResult",
    "DroppedItems",
    "apply_diff",
    "resolve_diff_with_options",
    "mark_proposal_topic_reviewed",
]
