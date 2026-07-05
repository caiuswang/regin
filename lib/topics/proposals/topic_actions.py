"""Per-topic review actions: update, accept, replace, merge, ignore.

Each accept/merge/replace composes a GraphDiff for one topic change
and routes it through `apply_diff` — the single write path that owns
GraphSnapshot insertion + atomic disk export. `_apply_topic_change`
is the local wrapper that runs the pre/post `audit_graph` diff so an
operation can never *introduce* new errors into the live graph.
"""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

from lib.topics import (
    DEFAULT_REF_TIER,
    REF_ROLES,
    REF_TIERS,
    TopicGraphError,
    topic_dir,
    utc_now,
)
from lib.topics.apply import ApplyResult, apply_diff
from lib.topics.diff import GraphDiff, compute_topic_delta
from lib.topics.graph_io import load_authoritative_graph
from lib.topics.proposal_drafting import validate_proposal
from lib.topics.snapshots import resolve_or_create_repo
from lib.topics.validation import audit_graph, diff_issues

from ._common import (
    _find_proposed_topic,
    _persist_per_topic_wiki,
    _recompute_proposal_status,
    _topics_log,
)
from .core_io import load_proposal, save_proposal

_VALID_EDGE_TYPES = frozenset({"related", "depends_on", "part_of", "supersedes"})


def _capture_ref_digests_on_accept(repo_path: str | Path, topic_id: str) -> None:
    """Fingerprint the accepted topic's ref files for later drift detection.

    Gated on `topic_evolution.evolution_enabled` (off by default → no
    behaviour change) and best-effort: `capture_ref_digests` never raises, so
    accept can't fail because the digest table is unavailable."""
    from lib.settings import settings
    if not settings.topic_evolution.evolution_enabled:
        return
    from lib.topics.ref_digest import capture_ref_digests
    capture_ref_digests(repo_path, topic_id)


def _restore_topic_memories_on_accept(topic_id: str) -> None:
    """Recovery half of the topic→memory cascade: an accepted/refreshed topic
    un-stales the memories a prior drift demoted. Gated on `evolution_enabled`
    and best-effort (`restore_topic_memories` never raises)."""
    from lib.settings import settings
    if not settings.topic_evolution.evolution_enabled:
        return
    from lib.memory import get_store
    from lib.memory.topic_cascade import restore_topic_memories
    restore_topic_memories(get_store(), topic_id)


def update_proposed_topic(
    repo_path: str | Path,
    proposal_id: str,
    proposed_topic_id: str,
    patch: dict[str, Any],
) -> dict[str, Any]:
    """Edit one proposed topic before review acceptance."""
    proposal = load_proposal(repo_path, proposal_id)
    proposed = _find_proposed_topic(proposal, proposed_topic_id)
    allowed = {
        "id", "label", "aliases", "intent", "status", "refs", "edges",
        "commands", "include_globs", "exclude_globs", "evidence_paths",
        "parent_id", "blurb",
    }
    for key, value in patch.items():
        if key in allowed:
            proposed[key] = value
    errors = validate_proposal(proposal)
    if errors:
        raise TopicGraphError("; ".join(errors))
    proposal["status"] = "pending_review"
    save_proposal(repo_path, proposal_id, proposal)
    _topics_log().write(
        "proposal_topic_edited",
        proposal_id=proposal_id, proposed_topic_id=proposed_topic_id,
        repo_path=str(repo_path), patched_keys=sorted(k for k in patch if k in allowed),
    )
    return proposed


def _apply_topic_change(
    repo_path: str | Path,
    *,
    kind: str,
    after_topic_id: str,
    after_topic: dict[str, Any],
    before_topic: dict[str, Any] | None,
    target_topic_id: str | None = None,
    triggering_run_id: str | None = None,
) -> ApplyResult:
    """Internal shim — compose a GraphDiff for one topic change and apply it.

    The pre/post audit replaces the older "save-then-validate-then-rollback"
    dance: we compute the prospective graph, audit it against the current
    graph, and only let the apply proceed when no NEW errors are introduced.
    Pre-existing rot in unrelated topics no longer blocks the operation —
    `apply_diff` writes those as advisory `graph_warnings` and lets the
    snapshot through.
    """
    graph = load_authoritative_graph(repo_path)
    prospective = deepcopy(graph)
    prospective.setdefault("topics", {})[after_topic_id] = after_topic

    pre = audit_graph(graph, repo_path=Path(repo_path))
    post = audit_graph(prospective, repo_path=Path(repo_path))
    introduced, _ = diff_issues(pre, post)
    new_errors = [i for i in introduced if i.severity == "error"]
    if new_errors:
        raise TopicGraphError("; ".join(i.message for i in new_errors))

    post_keys = {i.identity for i in post}
    graph_warnings = tuple(i for i in pre if i.identity in post_keys)
    delta = compute_topic_delta(
        topic_id_after=after_topic_id,
        kind=kind,
        before=before_topic,
        after=after_topic,
    )
    diff = GraphDiff(
        topic_deltas=(delta,),
        graph_warnings=graph_warnings,
        introduced_errors=(),
        valid_strategies_by_topic={},
        strategy=kind,
        target_topic_id=target_topic_id,
        proposed_topic_id=after_topic_id,
        prospective_graph=prospective,
    )
    repo = resolve_or_create_repo(str(repo_path))
    return apply_diff(
        repo.id, diff,
        reason=kind,
        triggering_run_id=triggering_run_id,
    )


def accept_proposed_topic(
    repo_path: str | Path,
    proposal_id: str,
    proposed_topic_id: str,
    *,
    topic_id: str | None = None,
) -> dict[str, Any]:
    """Promote one proposed topic into the approved graph."""
    proposal = load_proposal(repo_path, proposal_id)
    errors = validate_proposal(proposal)
    if errors:
        raise TopicGraphError("; ".join(errors))

    proposed = _find_proposed_topic(proposal, proposed_topic_id)
    approved_id = topic_id or proposed["id"]
    graph = load_authoritative_graph(repo_path)
    if approved_id in graph.get("topics", {}):
        raise TopicGraphError(f"topic already exists: {approved_id}")

    approved = _approved_topic_from_proposal(proposed, existing_topic_ids=set(graph.get("topics", {})))
    _apply_topic_change(
        repo_path,
        kind="create",
        after_topic_id=approved_id,
        after_topic=approved,
        before_topic=None,
        triggering_run_id=proposal_id,
    )

    proposed["review_status"] = "accepted"
    proposed["accepted_topic"] = approved_id
    proposed["accepted_at"] = utc_now()
    _recompute_proposal_status(proposal)
    save_proposal(repo_path, proposal_id, proposal)
    proposal_dir = topic_dir(repo_path) / "proposals" / proposal_id
    _persist_per_topic_wiki(repo_path, proposal_dir, approved_id, proposed.get("wiki"))
    _capture_ref_digests_on_accept(repo_path, approved_id)
    _restore_topic_memories_on_accept(approved_id)
    _topics_log().write(
        "proposal_topic_accepted",
        proposal_id=proposal_id, proposed_topic_id=proposed_topic_id,
        approved_id=approved_id, repo_path=str(repo_path),
    )
    return approved | {"id": approved_id}


def replace_approved_topic(
    repo_path: str | Path,
    proposal_id: str,
    proposed_topic_id: str,
    *,
    topic_id: str | None = None,
) -> dict[str, Any]:
    """Accept a draft and have it replace the existing approved topic of the same id.

    Used when a regenerated proposal drafts a topic whose id already
    exists in the graph — plain accept rejects with "topic already
    exists". This atomic swap is the user-driven equivalent.
    """
    proposal = load_proposal(repo_path, proposal_id)
    errors = validate_proposal(proposal)
    if errors:
        raise TopicGraphError("; ".join(errors))

    proposed = _find_proposed_topic(proposal, proposed_topic_id)
    approved_id = topic_id or proposed["id"]
    graph = load_authoritative_graph(repo_path)
    topics_map = graph.get("topics", {})
    original = topics_map.get(approved_id)
    if original is None:
        raise TopicGraphError(f"topic does not exist: {approved_id}")

    sibling_ids = set(topics_map) - {approved_id}
    approved = _approved_topic_from_proposal(proposed, existing_topic_ids=sibling_ids)
    _apply_topic_change(
        repo_path,
        kind="replace",
        after_topic_id=approved_id,
        after_topic=approved,
        before_topic=original,
        triggering_run_id=proposal_id,
    )

    proposed["review_status"] = "accepted"
    proposed["accepted_topic"] = approved_id
    proposed["accepted_at"] = utc_now()
    proposed["replaced_existing"] = True
    _recompute_proposal_status(proposal)
    save_proposal(repo_path, proposal_id, proposal)
    proposal_dir = topic_dir(repo_path) / "proposals" / proposal_id
    _persist_per_topic_wiki(repo_path, proposal_dir, approved_id, proposed.get("wiki"))
    _capture_ref_digests_on_accept(repo_path, approved_id)
    _restore_topic_memories_on_accept(approved_id)
    _topics_log().write(
        "proposal_topic_replaced",
        proposal_id=proposal_id, proposed_topic_id=proposed_topic_id,
        approved_id=approved_id, repo_path=str(repo_path),
    )
    return approved | {"id": approved_id}


def merge_proposed_topic(
    repo_path: str | Path,
    proposal_id: str,
    proposed_topic_id: str,
    target_topic_id: str,
) -> dict[str, Any]:
    """Merge one proposed topic's reviewable context into an approved topic."""
    proposal = load_proposal(repo_path, proposal_id)
    errors = validate_proposal(proposal)
    if errors:
        raise TopicGraphError("; ".join(errors))
    proposed = _find_proposed_topic(proposal, proposed_topic_id)

    graph = load_authoritative_graph(repo_path)
    original_target = graph.get("topics", {}).get(target_topic_id)
    if not original_target:
        raise TopicGraphError(f"topic not found: {target_topic_id}")

    target = _merge_into_target(original_target, proposed)
    _apply_topic_change(
        repo_path,
        kind="merge",
        after_topic_id=target_topic_id,
        after_topic=target,
        before_topic=original_target,
        target_topic_id=target_topic_id,
        triggering_run_id=proposal_id,
    )

    proposed["review_status"] = "merged"
    proposed["merged_topic"] = target_topic_id
    proposed["merged_at"] = utc_now()
    _recompute_proposal_status(proposal)
    save_proposal(repo_path, proposal_id, proposal)
    _restore_topic_memories_on_accept(target_topic_id)
    _topics_log().write(
        "proposal_topic_merged",
        proposal_id=proposal_id, proposed_topic_id=proposed_topic_id,
        target_topic_id=target_topic_id, repo_path=str(repo_path),
    )
    return target | {"id": target_topic_id}


def _merge_into_target(original_target: dict[str, Any], proposed: dict[str, Any]) -> dict[str, Any]:
    """Fold proposed topic's refs / aliases / globs / commands into the
    approved target; target's wiki narrative is intentionally kept."""
    target = deepcopy(original_target)
    refs_by_path = {
        ref.get("path"): ref for ref in target.get("refs", []) if isinstance(ref, dict)
    }
    for ref in _approved_refs_from_proposal(proposed.get("refs", [])):
        refs_by_path.setdefault(ref["path"], ref)
    target["refs"] = sorted(refs_by_path.values(), key=lambda ref: ref["path"])
    target["aliases"] = sorted(set(target.get("aliases", [])) | set(proposed.get("aliases", [])))
    target["include_globs"] = sorted(
        set(target.get("include_globs", [])) | set(proposed.get("include_globs", []))
    )
    target["exclude_globs"] = sorted(
        set(target.get("exclude_globs", [])) | set(proposed.get("exclude_globs", []))
    )
    target["commands"] = sorted(
        set(target.get("commands", [])) | set(proposed.get("commands", []))
    )
    return target


def ignore_proposed_topic(repo_path: str | Path, proposal_id: str,
                          proposed_topic_id: str, *,
                          rebaseline_drift: bool = False) -> dict[str, Any]:
    """Mark a proposed topic ignored without mutating approved graph data.

    `rebaseline_drift` is the *explicit human "this change is unrelated"* signal
    and defaults **off**. When set AND the proposal is a standalone content-drift
    refresh (`provider == content-drift`, the fallback for a drifted topic with
    no origin run), advance the topic's drift baseline so `detect_drifted_topics`
    stops re-emitting it — the standalone-proposal analogue of dismissing an
    origin-run drift note.

    It must stay opt-in: the automated callers (`expire_stale_auto_proposals`,
    `agent_spawn._dismiss_trivial`) also ignore content-drift proposals, and
    silently re-baselining there would permanently retire a *genuine* drift the
    user never judged — leaving the wiki stale forever. Only the human `/ignore`
    entry point passes `rebaseline_drift=True`."""
    proposal = load_proposal(repo_path, proposal_id)
    proposed = _find_proposed_topic(proposal, proposed_topic_id)
    proposed["review_status"] = "ignored"
    proposed["ignored_at"] = utc_now()
    _recompute_proposal_status(proposal)
    save_proposal(repo_path, proposal_id, proposal)
    rebaselined = False
    if rebaseline_drift:
        from lib.topics.content_drift import REFRESH_PROVIDER
        if proposal.get("provider") == REFRESH_PROVIDER:
            from lib.topics.ref_digest import capture_ref_digests
            rebaselined = capture_ref_digests(repo_path, proposed_topic_id) > 0
    _topics_log().write(
        "proposal_topic_ignored",
        proposal_id=proposal_id, proposed_topic_id=proposed_topic_id,
        drift_rebaselined=rebaselined, repo_path=str(repo_path),
    )
    return proposed


# ──────────────────── proposal → approved-graph converters ────────────────


def _approved_topic_from_proposal(
    topic: dict[str, Any],
    *,
    existing_topic_ids: set[str] | None = None,
) -> dict[str, Any]:
    return {
        "label": topic["label"],
        "aliases": topic.get("aliases", []),
        "intent": topic["intent"],
        "status": topic.get("status", "active"),
        "refs": _approved_refs_from_proposal(topic.get("refs", [])),
        "edges": _approved_edges_from_proposal(
            topic.get("edges", []), existing_topic_ids=existing_topic_ids,
        ),
        "commands": topic.get("commands", []),
        "include_globs": topic.get("include_globs", []),
        "exclude_globs": topic.get("exclude_globs", []),
        # Navigation-taxonomy placement: a null parent_id lands the topic in
        # the `unclassified` bucket (a reviewed-field backlog), never silently
        # at the top level. blurb is the router card; falls back to intent.
        "parent_id": topic.get("parent_id"),
        "blurb": topic.get("blurb", ""),
    }


def _normalize_edge(
    edge: Any,
    existing_topic_ids: set[str] | None,
) -> tuple[str, str] | None:
    """Validate one edge; return (edge_type, target) tuple or None."""
    if not isinstance(edge, dict):
        return None
    target = edge.get("target") or edge.get("to")
    if not isinstance(target, str) or not target:
        return None
    edge_type = edge.get("type") or edge.get("rel") or "related"
    if edge_type not in _VALID_EDGE_TYPES:
        return None
    if existing_topic_ids is not None and target not in existing_topic_ids:
        return None
    return edge_type, target


def _approved_edges_from_proposal(
    edges: list[Any], *, existing_topic_ids: set[str] | None = None
) -> list[dict[str, str]]:
    approved_edges: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for edge in edges:
        normalized = _normalize_edge(edge, existing_topic_ids)
        if normalized is None or normalized in seen:
            continue
        edge_type, target = normalized
        approved_edges.append({"type": edge_type, "target": target})
        seen.add(normalized)
    return approved_edges


def _approved_refs_from_proposal(refs: list[Any]) -> list[dict[str, str]]:
    approved_refs: list[dict[str, str]] = []
    seen: set[str] = set()
    for ref in refs:
        if not isinstance(ref, dict) or not isinstance(ref.get("path"), str):
            continue
        path = ref["path"]
        if path in seen:
            continue
        # Keep a valid role/tier from the proposal; drop unknown/missing ones
        # rather than inventing one (both axes are LLM/human-owned). `tier` is
        # persisted only when it's an explicit non-default (`reference`), so a
        # normal ref stays the canonical `{path, role?}` — this is what carries
        # a drift-exclusion tag through BOTH apply paths (modern /apply and
        # legacy accept converge here via `_approved_topic_from_proposal`).
        approved: dict[str, str] = {"path": path}
        role = ref.get("role")
        if role in REF_ROLES:
            approved["role"] = role
        tier = ref.get("tier")
        if tier in REF_TIERS and tier != DEFAULT_REF_TIER:
            approved["tier"] = tier
        approved_refs.append(approved)
        seen.add(path)
    return approved_refs
