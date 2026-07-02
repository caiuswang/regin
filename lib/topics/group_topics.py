"""`regin topics group` — cluster a repo's FLAT topic set into a 2-level
bucket tree. The structural INVERSE of `topics split-leaf`: instead of carving
one heavy leaf into siblings, it gathers many unbucketed leaves under a handful
of freshly-minted top-level buckets and reparents them.

The pipeline has three seams, each isolated for testing (mirroring
`split_leaf`):

1. **propose_buckets** (judgment, agentic) — an LLM reads the flat topics and
   groups them by SUBJECT into `lo`-`hi` buckets. Fail-loud, like
   `split_leaf.propose_clusters`: no agent ⇒ `ClusterProposerUnavailable`.
2. **build_group_plan** (pure) — turns clusters into a `GroupPlan`: mints
   valid top-level bucket ids (`kind:"bucket"`, `parent_id: None`) and the
   `{topic_id: bucket_id}` reparent map. Any flat topic the proposer omitted
   keeps its current parent (never enters the assignment).
3. **apply_group** (mutation) — re-runs the safety gate (`check_group`) and
   refuses unless it passes, then writes the new bucket nodes to `topic.json`
   AND sets `parent_id` on each grouped topic, syncing the snapshot. No memory
   relinking — grouping only reshapes the taxonomy skeleton.

`check_group` (the gate) is pure and DB-free — pass a graph dict — and lives in
this module because grouping, unlike splitting, needs no live-store adapter.

Mutation uses the canonical topic.json writer + `import_from_disk` (the proven
path), mirroring `split_leaf._write_nodes`.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from lib.activity_log import get_activity_logger
from lib.topics.split_leaf import ClusterProposerUnavailable, _slug, _unique_id
from lib.topics.tree import (
    UNCLASSIFIED, build_tree, effective_parent, is_bucket,
)
from lib.topics.validation import audit_graph, split_by_severity

log = get_activity_logger("topics")

DEFAULTS = {
    "max_bucket_children": 15,  # cap a bucket's leaf fan-out (index_root cost)
}


@dataclass
class BucketCluster:
    label: str
    intent: str
    topic_ids: list[str] = field(default_factory=list)


@dataclass
class GroupPlan:
    """A proposed grouping, as the clustering step would emit it."""

    new_buckets: dict[str, dict]     # new_bucket_id -> node body (kind:"bucket", parent_id None)
    assignment: dict[str, str]       # flat_topic_id -> new_bucket_id


@dataclass
class GroupGateResult:
    ok: bool
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    stats: dict = field(default_factory=dict)


# ── 1. propose (agentic) ────────────────────────────────────────

_PROMPT = """You are organizing a repo's knowledge base whose topics are all
sitting at the top level, unbucketed. Group them into {lo}-{hi} coherent
top-level BUCKETS so a future reader can drill into the right area. Rules:
- Each topic goes in exactly ONE bucket. Cover every topic id given.
- Give each bucket a short Title-Case label and a one-line intent written as a
  router card ("drill in here when …"), not a description.
- Group by SUBJECT (what the topic is about), not by incidental overlaps.

<topics>
{topics}
</topics>

<output_format>
Respond with ONLY a JSON array:
  [{{"label": "...", "intent": "...", "topic_ids": ["<id>", ...]}}, ...]
</output_format>
"""


def _topics_block(flat_topics: list[dict]) -> str:
    parts = []
    for t in flat_topics:
        label = (t.get("label") or "").strip()
        intent = " ".join((t.get("intent") or "").split())[:300]
        parts.append(f'<topic id="{t["id"]}">\n{label}\n{intent}\n</topic>')
    return "\n".join(parts)


def _json_array(answer: str) -> list:
    """Extract a JSON array from model output, tolerating fences/prose. [] on
    failure."""
    text = re.sub(r"```(?:json)?", "", answer or "")
    start, end = text.find("["), text.rfind("]")
    if start < 0 or end <= start:
        return []
    try:
        items = json.loads(text[start:end + 1])
    except (json.JSONDecodeError, ValueError):
        return []
    return items if isinstance(items, list) else []


def _cluster_from_item(item: object, valid_ids: set[str]) -> "BucketCluster | None":
    if not isinstance(item, dict):
        return None
    ids = [t for t in (item.get("topic_ids") or []) if t in valid_ids]
    label = (item.get("label") or "").strip()
    if not (label and ids):
        return None
    return BucketCluster(
        label=label, intent=(item.get("intent") or "").strip(), topic_ids=ids)


def _parse_clusters(answer: str, valid_ids: set[str]) -> list[BucketCluster]:
    """Parse the LLM's JSON array into clusters, keeping only ids we offered."""
    out = (_cluster_from_item(item, valid_ids) for item in _json_array(answer))
    return [c for c in out if c is not None]


def propose_buckets(flat_topics: list[dict], llm, *,
                    lo: int = 3, hi: int = 8) -> list[BucketCluster]:
    """Agentically cluster `flat_topics` into top-level buckets. Fail-loud:
    raises `ClusterProposerUnavailable` when the LLM returns nothing."""
    prompt = _PROMPT.format(
        topics=_topics_block(flat_topics), lo=lo, hi=hi)
    answer = llm.complete(prompt, max_tokens=4096)
    if not answer:
        raise ClusterProposerUnavailable(
            "no LLM completion — is an external agent configured?")
    clusters = _parse_clusters(answer, {t["id"] for t in flat_topics})
    log.write("group_buckets_proposed", buckets=len(clusters))
    return clusters


# ── 2. build plan (pure) ────────────────────────────────────────

def _norm_label(label: str) -> str:
    """Canonical form for comparing bucket LABELS (not ids): case- and
    whitespace-folded. Two buckets whose labels collapse to the same value are
    twins that would render as confusing duplicates in the nav/wiki tree.
    Deliberately coarser than `_slug` on punctuation would be — we only fold
    genuinely identical human labels ("Agent Runtime Core" == "Agent Runtime
    Core"), not merely slug-adjacent ones ("Rules!" stays distinct from
    "rules")."""
    return " ".join((label or "").split()).casefold()


def _bucket_body(cluster: BucketCluster) -> dict:
    """A new top-level bucket node. `kind:"bucket"` + `parent_id: None` is the
    hard requirement `build_tree` needs to nest anything under it."""
    intent = cluster.intent or f"Topics about {cluster.label}."
    return {
        "kind": "bucket",
        "label": cluster.label,
        "intent": intent,
        "blurb": intent,
        "status": "active",
        "parent_id": None,
        "aliases": [],
        "refs": [],
        "edges": [],
        "commands": [],
        "include_globs": [],
        "exclude_globs": [],
    }


def build_group_plan(clusters: list[BucketCluster], flat_topic_ids, graph: dict,
                     *, keep_unplaced: bool = True) -> GroupPlan:
    """Assemble a `GroupPlan` from clusters. Mints unique bucket ids (deduped
    against existing topic ids AND newly-minted ones); builds the
    `{topic_id: bucket_id}` reparent map from the offered flat topics only. Any
    flat topic the proposer omitted is left out of the assignment, so it keeps
    its current parent (conservation).

    A cluster whose LABEL matches an existing bucket (or one already minted in
    this same pass) is **folded into that bucket** rather than minting a
    same-label twin: a second grouping pass — or an LLM re-proposing a bucket
    that already exists — would otherwise create a duplicate root like
    `agent-runtime-core` + `agent-runtime-core-2` that render as confusing
    twins in the nav/wiki tree. Only real `kind:"bucket"` nodes are reuse
    targets (a same-slug leaf can't host children)."""
    topics = graph.get("topics") or {}
    taken = set(topics)
    offered = set(flat_topic_ids)
    new_buckets: dict[str, dict] = {}
    assignment: dict[str, str] = {}
    # normalized label → bucket id, seeded with existing buckets and grown as we
    # mint, so both cross-pass and within-pass label collisions fold into one.
    bucket_by_label = {
        _norm_label(n.get("label") or tid): tid
        for tid, n in topics.items()
        if isinstance(n, dict) and is_bucket(n)
    }

    for cluster in clusters:
        bid = _mint_or_reuse_bucket(cluster, bucket_by_label, taken, new_buckets)
        for tid in cluster.topic_ids:
            # first bucket to claim an offered topic wins (dict-enforced 1:1)
            if tid in offered and tid not in assignment:
                assignment[tid] = bid

    return GroupPlan(new_buckets=new_buckets, assignment=assignment)


def _mint_or_reuse_bucket(cluster: BucketCluster, bucket_by_label: dict,
                          taken: set, new_buckets: dict) -> str:
    """Resolve the bucket id for `cluster`: reuse an existing/just-minted bucket
    with the same normalized label, else mint a fresh unique id. Mutates
    `taken`, `new_buckets`, and `bucket_by_label` in place."""
    key = _norm_label(cluster.label)
    existing = bucket_by_label.get(key)
    if existing is not None:
        return existing                         # fold into the same-label bucket
    bid = _unique_id(_slug(cluster.label), taken)
    taken.add(bid)
    new_buckets[bid] = _bucket_body(cluster)
    bucket_by_label[key] = bid
    return bid


# ── gate: check_group (pure, DB-free) ───────────────────────────

def check_group(plan: GroupPlan, graph: dict, *,
                repo_path=None, thresholds: dict | None = None) -> GroupGateResult:
    """Validate `plan` against the current `graph`. Pure: no mutation, no I/O
    beyond `audit_graph`. Any error ⇒ `ok=False`, do not apply."""
    t = {**DEFAULTS, **(thresholds or {})}
    errors: list[str] = []
    warnings: list[str] = []

    errors += _check_structural(plan, graph, repo_path)
    errors += _check_conservation(plan, graph)
    warnings += _check_soft(plan, t)

    counts = _bucket_counts(plan)
    stats = {
        "new_buckets": len(plan.new_buckets),
        "grouped": len(plan.assignment),
        "bucket_counts": counts,
    }
    return GroupGateResult(
        ok=not errors, errors=errors, warnings=warnings, stats=stats)


# ── gate 1: structural ──────────────────────────────────────────

def _check_structural(plan: GroupPlan, graph: dict, repo_path) -> list[str]:
    topics = graph.get("topics") or {}
    existing_buckets = {tid for tid, n in topics.items()
                        if isinstance(n, dict) and is_bucket(n)}
    return (_check_new_buckets(plan, topics)
            + _check_assignment_targets(plan, existing_buckets)
            + _check_only_flat(plan, topics, existing_buckets)
            + _check_empty_buckets(plan)
            + _check_duplicate_labels(plan, topics)
            + _audit_prospective(plan, graph, repo_path))


def _check_new_buckets(plan: GroupPlan, topics: dict) -> list[str]:
    """Every minted bucket must be a fresh `kind:"bucket"` root."""
    errors: list[str] = []
    for bid, body in plan.new_buckets.items():
        if bid in topics:
            errors.append(f"new bucket {bid!r} already exists")
        if not is_bucket(body):
            errors.append(f"new bucket {bid!r} must be kind:'bucket'")
        if body.get("parent_id") is not None:
            errors.append(
                f"new bucket {bid!r} must have parent_id=None, "
                f"got {body.get('parent_id')!r}")
    return errors


def _check_assignment_targets(plan: GroupPlan,
                              existing_buckets: set[str]) -> list[str]:
    """No existing bucket may be reparented; every target must be a bucket."""
    errors: list[str] = []
    valid_targets = set(plan.new_buckets) | existing_buckets
    for tid, target in plan.assignment.items():
        if tid in existing_buckets:
            errors.append(
                f"topic {tid!r} is an existing bucket and must never be "
                f"reparented (2-level rule)")
        if target not in valid_targets:
            errors.append(
                f"assignment target {target!r} for topic {tid!r} is not a "
                f"kind:'bucket' node")
    return errors


def _check_only_flat(plan: GroupPlan, topics: dict,
                     existing_buckets: set[str]) -> list[str]:
    """Grouping may reparent only FLAT topics — ones whose current effective
    parent is `UNCLASSIFIED`. A topic already nested under a real bucket must not
    be silently re-homed. The CLI pre-filters to flat topics, but the gate
    enforces the precondition so a second caller can't bypass the invariant."""
    errors: list[str] = []
    for tid in plan.assignment:
        node = topics.get(tid)
        if node is None or is_bucket(node):
            continue  # missing / bucket cases are caught by the other gates
        if effective_parent(topics, existing_buckets, tid) != UNCLASSIFIED:
            errors.append(
                f"topic {tid!r} is already placed under a bucket and must not "
                f"be regrouped (grouping only reparents flat topics)")
    return errors


def _check_empty_buckets(plan: GroupPlan) -> list[str]:
    """A minted bucket with zero grouped topics is a childless top-level root —
    never write one (it only inflates `index_root`)."""
    counts = _bucket_counts(plan)
    return [f"new bucket {bid!r} would be created with no member topics"
            for bid in plan.new_buckets if counts.get(bid, 0) == 0]


def _check_duplicate_labels(plan: GroupPlan, topics: dict) -> list[str]:
    """No minted bucket may reuse the LABEL of an existing bucket or another
    minted bucket. Same-label roots render as confusing twins in the nav/wiki
    tree (the `agent-runtime-core` / `agent-runtime-core-2` bug). `build_group_plan`
    folds re-proposals into the existing bucket, so this gate normally never
    fires — it guarantees the invariant for any other plan source."""
    errors: list[str] = []
    existing = {}
    for tid, n in topics.items():
        if isinstance(n, dict) and is_bucket(n):
            existing.setdefault(_norm_label(n.get("label") or tid), tid)
    seen: dict[str, str] = {}
    for bid, body in plan.new_buckets.items():
        key = _norm_label(body.get("label") or bid)
        if key in existing:
            errors.append(
                f"new bucket {bid!r} duplicates the label of existing bucket "
                f"{existing[key]!r} ({body.get('label')!r}); assign topics to it "
                f"instead of minting a twin")
        if key in seen:
            errors.append(
                f"new buckets {seen[key]!r} and {bid!r} share the label "
                f"{body.get('label')!r}")
        seen[key] = bid
    return errors


def _prospective(plan: GroupPlan, graph: dict) -> dict:
    """The graph as it would look after applying `plan`: new buckets added and
    each grouped topic's `parent_id` pointed at its bucket."""
    topics = dict(graph.get("topics") or {})
    topics.update(plan.new_buckets)
    for tid, bid in plan.assignment.items():
        if tid in topics:
            node = dict(topics[tid])
            node["parent_id"] = bid
            topics[tid] = node
    return {**graph, "topics": topics}


def _audit_prospective(plan: GroupPlan, graph: dict, repo_path) -> list[str]:
    """Add the new buckets + reparents, re-audit, and fail on any error touching
    a new/grouped node or any grouped topic that build_tree routes to
    `unclassified` (the 2-level trap this gate exists to catch)."""
    errors: list[str] = []
    prospective = _prospective(plan, graph)
    touched = set(plan.new_buckets) | set(plan.assignment)

    issues = audit_graph(prospective, repo_path=repo_path)
    errs, _ = split_by_severity(issues)
    for e in errs:
        tids = set(e.topic_ids or ())
        if not tids or tids & touched:
            errors.append(f"audit error {e.code}: {e.message}")

    stray = set(plan.assignment) & set(
        build_tree(prospective)["children"].get(UNCLASSIFIED, []))
    if stray:
        errors.append(
            f"grouped topics not nested under a bucket (routed to "
            f"unclassified): {sorted(stray)}")
    return errors


# ── gate 2: conservation ────────────────────────────────────────

def _check_conservation(plan: GroupPlan, graph: dict) -> list[str]:
    errors: list[str] = []
    topics = graph.get("topics") or {}

    missing = sorted(tid for tid in plan.assignment if tid not in topics)
    if missing:
        errors.append(
            f"{len(missing)} assigned topics are not in the graph "
            f"(e.g. {missing[:3]})")
    return errors


# ── soft gates ──────────────────────────────────────────────────

def _bucket_counts(plan: GroupPlan) -> dict[str, int]:
    counts: dict[str, int] = {bid: 0 for bid in plan.new_buckets}
    for bid in plan.assignment.values():
        counts[bid] = counts.get(bid, 0) + 1
    return counts


def _check_soft(plan: GroupPlan, t: dict) -> list[str]:
    counts = _bucket_counts(plan)
    warnings: list[str] = []
    for bid in plan.new_buckets:
        c = counts.get(bid, 0)
        if c > t["max_bucket_children"]:
            warnings.append(f"bucket {bid} would have {c} children "
                            f"(> cap {t['max_bucket_children']})")
        if c == 1:
            warnings.append(f"bucket {bid} holds {c} member(s) (< 2)")
    return warnings


# ── 3. apply (mutation, gated) ──────────────────────────────────

def gate_only(repo_path, plan: GroupPlan, graph: dict) -> GroupGateResult:
    """Run the gate without mutating — backs the CLI's dry-run."""
    return check_group(plan, graph, repo_path=repo_path)


def apply_group(repo_path, plan: GroupPlan, graph: dict) -> dict:
    """Gate, then execute `plan`. Raises `ValueError` (with the gate errors) if
    the plan does not pass — you cannot apply an ungated grouping."""
    gate = check_group(plan, graph, repo_path=repo_path)
    if not gate.ok:
        raise ValueError("group gate failed: " + "; ".join(gate.errors))

    _write_group(repo_path, graph, plan)
    log.write("group_applied", new_buckets=len(plan.new_buckets),
              grouped=len(plan.assignment))
    return {"new_buckets": list(plan.new_buckets), "grouped": len(plan.assignment)}


def _write_group(repo_path, graph: dict, plan: GroupPlan) -> None:
    """Add the new bucket nodes to topic.json and set each grouped topic's
    `parent_id` (canonical write), then sync the snapshot. On any failure while
    syncing, roll back both the new buckets and the parent_id changes so a
    failed apply leaves no orphan buckets or half-reparented topics."""
    from lib.topics.graph_io import import_from_disk

    topic_json = Path(repo_path) / ".regin" / "topics" / "topic.json"
    disk = json.loads(topic_json.read_text())
    original_parents = {
        tid: (disk["topics"].get(tid) or {}).get("parent_id")
        for tid in plan.assignment if tid in disk["topics"]}

    disk["topics"].update(plan.new_buckets)
    for tid, bid in plan.assignment.items():
        if tid in disk["topics"]:
            disk["topics"][tid]["parent_id"] = bid
    topic_json.write_text(json.dumps(disk, indent=2, sort_keys=True) + "\n")

    # keep the caller's graph view consistent (copy bodies so a later rollback
    # can't leave a shared-mutable node half-updated in the caller's dict)
    graph["topics"].update({bid: dict(body) for bid, body in plan.new_buckets.items()})
    for tid, bid in plan.assignment.items():
        if tid in graph["topics"]:
            graph["topics"][tid]["parent_id"] = bid

    try:
        import_from_disk(repo_path, reason="group-topics")
    except Exception:
        _rollback_group(repo_path, graph, plan, original_parents)
        raise


def _rollback_group(repo_path, graph: dict, plan: GroupPlan,
                    original_parents: dict[str, str | None]) -> None:
    """Undo `_write_group`: drop the new buckets and restore each grouped
    topic's original `parent_id`, on disk + in the caller's graph, then re-sync."""
    from lib.topics.graph_io import import_from_disk

    topic_json = Path(repo_path) / ".regin" / "topics" / "topic.json"
    disk = json.loads(topic_json.read_text())
    for bid in plan.new_buckets:
        disk["topics"].pop(bid, None)
        graph["topics"].pop(bid, None)
    for tid, parent in original_parents.items():
        if tid in disk["topics"]:
            disk["topics"][tid]["parent_id"] = parent
        if tid in graph["topics"]:
            graph["topics"][tid]["parent_id"] = parent
    topic_json.write_text(json.dumps(disk, indent=2, sort_keys=True) + "\n")
    import_from_disk(repo_path, reason="group-topics-rollback")
