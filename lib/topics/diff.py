"""Per-topic graph diff abstraction.

`diff_against_graph` produces a `GraphDiff` for one proposed topic and
a chosen strategy (`create` | `merge` | `replace`). The diff carries
three distinct buckets:

- `topic_deltas` — what actually changes (per-topic add/remove sets for
  aliases, refs, edges, plus scalar field changes).
- `graph_warnings` — issues that already exist in the current graph
  and would still exist after apply (pre-existing rot). Advisory only;
  does NOT block apply. This is the key feature: the diff does not
  refuse work because some unrelated topic has historic dead refs.
- `introduced_errors` — issues this diff WOULD ADD to the graph
  (e.g. a new alias that clashes with an approved topic). These DO
  block apply.

`valid_strategies_by_topic[proposed_topic_id]` lists which strategies
are valid for the topic. The UI uses this to enable/disable buttons.

The function is permissive: it accepts any (strategy, target) tuple
and reflects strategy/precondition failures in `introduced_errors`
rather than raising. The UI can request "the diff if I create" even
when create isn't valid, then read `valid_strategies_by_topic` to
discover which button to actually offer.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from lib.topics.validation import (
    ValidationIssue,
    audit_graph,
    diff_issues,
)
from lib.topics.core import normalize as _normalize_alias


Strategy = str  # "create" | "merge" | "replace"
VALID_STRATEGIES: tuple[str, ...] = ("create", "merge", "replace")


@dataclass(frozen=True)
class TopicDelta:
    """One topic's worth of change.

    `kind` reflects what `apply_diff` will do with it: "create" inserts,
    "replace" overwrites, "merge" mutates the target topic by absorbing
    the proposed one's content. `before` is None on create; `after` is
    None only in restore/undo flows (Phase A doesn't emit those).

    Add/remove sets are computed eagerly so the UI can render them
    without re-walking before/after. Sets are stored as sorted tuples
    for stable serialization.
    """

    topic_id: str  # the topic id IN THE GRAPH after the change (not the proposed id)
    kind: str  # "create" | "replace" | "merge"
    before: Optional[dict[str, Any]] = None
    after: Optional[dict[str, Any]] = None
    alias_adds: tuple[str, ...] = ()
    alias_removes: tuple[str, ...] = ()
    ref_adds: tuple[tuple[str, str, str], ...] = ()  # (path, role, tier)
    ref_removes: tuple[tuple[str, str, str], ...] = ()
    edge_adds: tuple[tuple[str, str], ...] = ()  # (target, type)
    edge_removes: tuple[tuple[str, str], ...] = ()
    scalar_changes: tuple[tuple[str, Any, Any], ...] = ()  # (field, before, after)


@dataclass(frozen=True)
class GraphDiff:
    """A previewable application of one proposed topic against the graph."""

    topic_deltas: tuple[TopicDelta, ...] = ()
    graph_warnings: tuple[ValidationIssue, ...] = ()
    introduced_errors: tuple[ValidationIssue, ...] = ()
    valid_strategies_by_topic: dict[str, tuple[str, ...]] = field(default_factory=dict)
    # The strategy/target the caller asked about, echoed back so
    # downstream code (apply.py, the UI) doesn't have to remember.
    strategy: str = ""
    target_topic_id: Optional[str] = None
    proposed_topic_id: str = ""
    prospective_graph: Optional[dict[str, Any]] = None

    @property
    def is_applyable(self) -> bool:
        """True when no NEW errors would be introduced.

        Pre-existing graph rot does not gate apply — only the issues
        this diff itself adds do. A diff over a graph that already has
        a dead-ref problem on some unrelated topic is still applyable
        as long as the diff doesn't make things worse.
        """
        return not self.introduced_errors


def _ref_key(ref: dict[str, Any]) -> tuple[str, str]:
    return (ref.get("path") or "", ref.get("role") or "")


def _ref_delta_key(ref: dict[str, Any]) -> tuple[str, str, str]:
    """Ref identity for change detection — includes tier, so a ref whose only
    change is its tier (e.g. reference→primary) surfaces as a remove+add in the
    delta. Merge dedup deliberately still keys on `_ref_key` (path, role) so a
    tier difference never duplicates a file into the merged topic."""
    return (ref.get("path") or "", ref.get("role") or "", ref.get("tier") or "primary")


def _edge_key(edge: dict[str, Any]) -> tuple[str, str]:
    return (edge.get("target") or "", edge.get("type") or "related")


def _alias_keys(aliases: list) -> dict[str, str]:
    """normalized_key -> original_alias_text (first wins)."""
    out: dict[str, str] = {}
    for a in aliases or []:
        if isinstance(a, str) and a:
            out.setdefault(_normalize_alias(a), a)
    return out


def _dedup_aliases(aliases: list) -> list[str]:
    """Collapse within-topic normalize-duplicates, order-preserving, first-wins.

    The graph keys aliases by `normalize()`, so two aliases that
    normalize identically (e.g. ``foo-bar`` and ``foo bar``) can only
    ever resolve to one topic — keeping both is zero-information and
    trips `topic.duplicate_alias_local` at audit time, which then blocks
    apply. Collapse them at the shape layer so proposal→graph conversion
    never *introduces* that rot. Cross-topic collisions are a separate
    case the user must resolve explicitly (see ApplyOptions.dedupe_aliases).

    `_alias_keys` already maps normalized_key -> first-original; dict
    insertion order is first-occurrence order, so its values are exactly
    the deduped list we want.
    """
    return list(_alias_keys(aliases).values())


def _scalar_fields() -> tuple[str, ...]:
    return ("label", "intent", "status")


def _keyed_add_remove(before_items, after_items, keyfn):
    """Add/remove key-sets between two dict-item lists, as sorted key tuples."""
    before = {keyfn(i): i for i in before_items if isinstance(i, dict)}
    after = {keyfn(i): i for i in after_items if isinstance(i, dict)}
    adds = tuple(sorted(after.keys() - before.keys()))
    removes = tuple(sorted(before.keys() - after.keys()))
    return adds, removes


def _alias_add_remove(before_aliases, after_aliases):
    """Alias add/remove: diff on normalized key, emit the original alias text."""
    before = _alias_keys(before_aliases)
    after = _alias_keys(after_aliases)
    adds = tuple(sorted(after[k] for k in after.keys() - before.keys()))
    removes = tuple(sorted(before[k] for k in before.keys() - after.keys()))
    return adds, removes


def _scalar_changes(a: dict[str, Any], b: dict[str, Any]) -> tuple[tuple[str, Any, Any], ...]:
    changes = [(f, a.get(f), b.get(f)) for f in _scalar_fields() if a.get(f) != b.get(f)]
    return tuple(changes)


def compute_topic_delta(
    *,
    topic_id_after: str,
    kind: str,
    before: Optional[dict[str, Any]],
    after: Optional[dict[str, Any]],
) -> TopicDelta:
    before = before or None
    after = after or None
    a = before or {}
    b = after or {}

    alias_adds, alias_removes = _alias_add_remove(a.get("aliases", []), b.get("aliases", []))
    ref_adds, ref_removes = _keyed_add_remove(a.get("refs", []), b.get("refs", []), _ref_delta_key)
    edge_adds, edge_removes = _keyed_add_remove(a.get("edges", []), b.get("edges", []), _edge_key)

    return TopicDelta(
        topic_id=topic_id_after,
        kind=kind,
        before=before,
        after=after,
        alias_adds=alias_adds,
        alias_removes=alias_removes,
        ref_adds=ref_adds,
        ref_removes=ref_removes,
        edge_adds=edge_adds,
        edge_removes=edge_removes,
        scalar_changes=_scalar_changes(a, b),
    )


def _approved_shape(proposed: dict[str, Any]) -> dict[str, Any]:
    """Coerce a proposed-topic dict into the approved-graph dict shape.

    Mirrors what `_approved_topic_from_proposal` in `lib/topics/proposals.py`
    does but strips fields that don't belong on a graph topic. The result
    is what the topic will look like AFTER apply.

    Phase B note: this DOES NOT pre-filter orphan edges (edges whose
    target isn't in the graph). The legacy `_approved_topic_from_proposal`
    silently drops them. When the new `/diff` endpoint goes live, those
    edges will surface as `graph.orphan_edge_target` introduced_errors
    — the resolution is either to wire `prune_orphan_edges` in the
    apply options or to add the pre-filter here. Pick before Phase B
    ships.
    """
    return {
        "label": proposed.get("label") or proposed.get("id"),
        "intent": proposed.get("intent", ""),
        "status": proposed.get("status", "active"),
        "aliases": _dedup_aliases(proposed.get("aliases") or []),
        "refs": list(proposed.get("refs") or []),
        "edges": list(proposed.get("edges") or []),
        "commands": list(proposed.get("commands") or []),
        "include_globs": list(proposed.get("include_globs") or []),
        "exclude_globs": list(proposed.get("exclude_globs") or []),
        # Navigation-taxonomy placement (reviewed field, not auto-assigned):
        # null parent_id → `unclassified` bucket backlog; blurb → router card.
        "parent_id": proposed.get("parent_id"),
        "blurb": proposed.get("blurb", ""),
    }


def _is_str_alias(a: Any) -> bool:
    """Alias guard: non-empty string (matches `_alias_keys`)."""
    return isinstance(a, str) and bool(a)


def _is_dict(x: Any) -> bool:
    """ref/edge guard: any dict, including the empty dict."""
    return isinstance(x, dict)


def _union_keyed(
    out: dict[str, Any],
    fname: str,
    proposed: dict[str, Any],
    *,
    guard,
    keyfn,
) -> None:
    """Append `proposed[fname]` items into `out[fname]`, deduped by `keyfn`.

    Only items passing `guard` participate. The output key is materialized
    lazily (via `setdefault`) so an absent field with nothing to add stays
    absent — preserving the original per-field merge semantics.
    """
    existing_items = out.get(fname, [])
    seen = {keyfn(item) for item in existing_items if guard(item)}
    for item in proposed.get(fname, []) or []:
        if guard(item):
            key = keyfn(item)
            if key not in seen:
                out.setdefault(fname, []).append(item)
                seen.add(key)


def _merged_topic(
    target: dict[str, Any], proposed: dict[str, Any],
) -> dict[str, Any]:
    """Compose the merged target = target ∪ proposed for set-like fields.

    Scalar fields (label, intent, status) stay on the target; only the
    set-like fields get unioned. Mirrors the existing merge semantics
    in `lib/topics/proposals.py:merge_proposed_topic`.
    """
    out = copy.deepcopy(target)

    _union_keyed(out, "aliases", proposed, guard=_is_str_alias, keyfn=_normalize_alias)
    _union_keyed(out, "refs", proposed, guard=_is_dict, keyfn=_ref_key)
    _union_keyed(out, "edges", proposed, guard=_is_dict, keyfn=_edge_key)

    # Globs and commands accumulate, dedup-by-string. Unlike the keyed
    # fields above, these are materialized unconditionally (even when
    # empty), so this loop stays verbatim.
    for fname in ("commands", "include_globs", "exclude_globs"):
        existing = list(out.get(fname, []) or [])
        seen = set(existing)
        for v in proposed.get(fname, []) or []:
            if v not in seen:
                existing.append(v)
                seen.add(v)
        out[fname] = existing

    return out


def _valid_strategies_for(
    proposed_topic_id: str,
    current_graph: dict[str, Any],
    target_topic_id: Optional[str],
) -> tuple[str, ...]:
    """Which strategies are precondition-valid for this proposed topic?"""
    topics = current_graph.get("topics", {}) or {}
    collides = proposed_topic_id in topics
    out: list[str] = []
    if not collides:
        out.append("create")
    if collides:
        out.append("replace")
    # Merge is always allowed as long as there is at least one approved
    # topic to merge INTO (target_topic_id must be supplied at apply
    # time). Skip if the graph is empty.
    if topics:
        if target_topic_id is None or target_topic_id in topics:
            out.append("merge")
    return tuple(out)


def _apply_strategy(
    strategy: Strategy,
    *,
    proposed_id: str,
    target_topic_id: Optional[str],
    topics: dict[str, Any],
    prospective_topics: dict[str, Any],
    after_topic_shape: dict[str, Any],
) -> tuple[list[TopicDelta], list[ValidationIssue]]:
    """Apply one strategy, mutating `prospective_topics` to the after-state.

    Returns the per-topic deltas and any precondition errors. Precondition
    failures (e.g. create with a colliding id) are reported as issues rather
    than raised, so callers can preview a strategy without committing.
    """
    deltas: list[TopicDelta] = []
    introduced_pre: list[ValidationIssue] = []

    if strategy == "create":
        if proposed_id in topics:
            introduced_pre.append(ValidationIssue(
                severity="error",
                code="topic.id_collides_with_approved",
                message=f"cannot create: topic id {proposed_id!r} already approved",
                topic_ids=(proposed_id,),
            ))
        else:
            prospective_topics[proposed_id] = after_topic_shape
            deltas.append(compute_topic_delta(
                topic_id_after=proposed_id,
                kind="create",
                before=None,
                after=after_topic_shape,
            ))

    elif strategy == "replace":
        if proposed_id not in topics:
            introduced_pre.append(ValidationIssue(
                severity="error",
                code="topic.replace_target_missing",
                message=f"cannot replace: topic {proposed_id!r} not in approved graph",
                topic_ids=(proposed_id,),
            ))
        else:
            before_topic = copy.deepcopy(topics[proposed_id])
            prospective_topics[proposed_id] = after_topic_shape
            deltas.append(compute_topic_delta(
                topic_id_after=proposed_id,
                kind="replace",
                before=before_topic,
                after=after_topic_shape,
            ))

    elif strategy == "merge":
        if target_topic_id is None:
            introduced_pre.append(ValidationIssue(
                severity="error",
                code="topic.merge_target_required",
                message="cannot merge: target_topic_id is required",
                topic_ids=(proposed_id,) if proposed_id else (),
            ))
        elif target_topic_id not in topics:
            introduced_pre.append(ValidationIssue(
                severity="error",
                code="topic.merge_target_missing",
                message=f"cannot merge: target topic {target_topic_id!r} not in approved graph",
                topic_ids=(target_topic_id,),
            ))
        else:
            before_target = copy.deepcopy(topics[target_topic_id])
            merged = _merged_topic(before_target, after_topic_shape)
            prospective_topics[target_topic_id] = merged
            deltas.append(compute_topic_delta(
                topic_id_after=target_topic_id,
                kind="merge",
                before=before_target,
                after=merged,
            ))

    return deltas, introduced_pre


def _classify_issues(
    current_graph: dict[str, Any],
    prospective_graph: dict[str, Any],
    *,
    introduced_pre: list[ValidationIssue],
    repo_path: Optional[Path | str],
) -> tuple[tuple[ValidationIssue, ...], tuple[ValidationIssue, ...]]:
    """Derive graph_warnings (advisory) and introduced_errors (blocking)."""
    repo_pathobj = Path(repo_path) if repo_path else None
    pre_issues = audit_graph(current_graph, repo_path=repo_pathobj)
    post_issues = audit_graph(prospective_graph, repo_path=repo_pathobj)
    introduced_from_audit, _resolved = diff_issues(pre_issues, post_issues)

    # Keep only pre_issues still present after the diff (= pre-existing rot
    # the apply didn't touch). Anything the apply resolved isn't worth
    # reporting.
    post_keys = {i.identity for i in post_issues}
    graph_warnings = tuple(i for i in pre_issues if i.identity in post_keys)

    introduced_errors = tuple(
        [i for i in introduced_pre if i.severity == "error"]
        + [i for i in introduced_from_audit if i.severity == "error"]
    )
    return graph_warnings, introduced_errors


def diff_against_graph(
    proposed_topic: dict[str, Any],
    current_graph: dict[str, Any],
    *,
    strategy: Strategy,
    target_topic_id: Optional[str] = None,
    repo_path: Optional[Path | str] = None,
) -> GraphDiff:
    """Compute the prospective graph and the diff for one proposed topic.

    The function is permissive: precondition failures (e.g. create with
    a colliding id) surface in `introduced_errors` instead of raising,
    so the caller can use the same code path to ask "what would happen
    if I picked X?" before committing to a strategy.
    """
    if strategy not in VALID_STRATEGIES:
        raise ValueError(f"strategy must be one of {VALID_STRATEGIES}, got {strategy!r}")

    proposed_id = proposed_topic.get("id") or ""
    topics = dict(current_graph.get("topics", {}) or {})
    prospective_graph = copy.deepcopy(current_graph)
    prospective_graph.setdefault("topics", {})

    # `prospective_topics` is the mutable dict we'll mutate to compose
    # the after-state; `topic_deltas` collects the per-topic changes.
    prospective_topics = prospective_graph["topics"]

    valid_strategies = _valid_strategies_for(proposed_id, current_graph, target_topic_id)
    after_topic_shape = _approved_shape(proposed_topic)

    # Apply the strategy: mutate `prospective_topics` in place to compose
    # the after-state, and record per-topic deltas / precondition errors.
    deltas, introduced_pre = _apply_strategy(
        strategy,
        proposed_id=proposed_id,
        target_topic_id=target_topic_id,
        topics=topics,
        prospective_topics=prospective_topics,
        after_topic_shape=after_topic_shape,
    )

    # Audit pre- and post-states to derive graph_warnings (advisory) vs
    # introduced_errors (blocking).
    graph_warnings, introduced_errors = _classify_issues(
        current_graph,
        prospective_graph,
        introduced_pre=introduced_pre,
        repo_path=repo_path,
    )

    return GraphDiff(
        topic_deltas=tuple(deltas),
        graph_warnings=graph_warnings,
        introduced_errors=introduced_errors,
        valid_strategies_by_topic={proposed_id: valid_strategies} if proposed_id else {},
        strategy=strategy,
        target_topic_id=target_topic_id,
        proposed_topic_id=proposed_id,
        prospective_graph=prospective_graph,
    )


def serialize_topic_delta(d: TopicDelta) -> dict[str, Any]:
    """JSON-safe view of a TopicDelta — used by `/diff` and `/apply`."""
    return {
        "topic_id": d.topic_id,
        "kind": d.kind,
        "before": d.before,
        "after": d.after,
        "alias_adds": list(d.alias_adds),
        "alias_removes": list(d.alias_removes),
        "ref_adds": [{"path": p, "role": r, "tier": t} for p, r, t in d.ref_adds],
        "ref_removes": [{"path": p, "role": r, "tier": t} for p, r, t in d.ref_removes],
        "edge_adds": [{"target": t, "type": ty} for t, ty in d.edge_adds],
        "edge_removes": [{"target": t, "type": ty} for t, ty in d.edge_removes],
        "scalar_changes": [
            {"field": f, "before": b, "after": a} for f, b, a in d.scalar_changes
        ],
    }


def serialize_issue(issue: ValidationIssue) -> dict[str, Any]:
    """JSON-safe view of a ValidationIssue."""
    return {
        "severity": issue.severity,
        "code": issue.code,
        "message": issue.message,
        "topic_ids": list(issue.topic_ids),
        "paths": list(issue.paths),
        "aliases": list(issue.aliases),
    }


def serialize_diff(d: GraphDiff) -> dict[str, Any]:
    """JSON-safe view of a GraphDiff — what `/diff` returns to the UI."""
    return {
        "strategy": d.strategy,
        "target_topic_id": d.target_topic_id,
        "proposed_topic_id": d.proposed_topic_id,
        "is_applyable": d.is_applyable,
        "topic_deltas": [serialize_topic_delta(t) for t in d.topic_deltas],
        "graph_warnings": [serialize_issue(w) for w in d.graph_warnings],
        "introduced_errors": [serialize_issue(e) for e in d.introduced_errors],
        "valid_strategies_by_topic": {
            k: list(v) for k, v in d.valid_strategies_by_topic.items()
        },
    }


__all__ = [
    "Strategy",
    "VALID_STRATEGIES",
    "TopicDelta",
    "GraphDiff",
    "diff_against_graph",
    "compute_topic_delta",
    "serialize_diff",
    "serialize_topic_delta",
    "serialize_issue",
]
