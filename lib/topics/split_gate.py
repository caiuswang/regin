"""Standalone safety gate for a heavy-leaf split (prototype).

A future `regin topics split-leaf` carves a too-large topic leaf into several
sibling sub-topics and redistributes its memories. The *clustering* that
proposes the split is judgment; whether a proposed split is **safe to apply** is
mechanical — and this module is that mechanical part, run BEFORE any mutation.

Three hard gates (any failure ⇒ `ok=False`, do not apply):

* **structural** — every new sub-topic is a sibling leaf under the heavy leaf's
  bucket (`parent_id` = a `kind:"bucket"` node), and the prospective graph still
  audits clean with none of the new nodes routed to `unclassified`. (The tree is
  strictly 2-level; a sub-topic parented to the leaf itself silently vanishes
  into `unclassified` — the exact failure this gate exists to catch.)
* **conservation** — every memory currently on the leaf lands on exactly one
  destination (a new sub-topic or the leaf kept as overview); nothing is lost,
  duplicated, or invented.
* **provenance** — `manual` / `reflect` links are not moved off the leaf without
  explicit opt-in (`allow_protected_move`).

Soft gates emit warnings (advisory, not blocking): eligibility threshold,
min-per-topic, dominant-cluster share, max-leaf actually shrank, bucket fan-out.

The core (`check_split`) is pure — pass a graph dict and a `{memory_id: source}`
map — so it is DB-free testable. `gather_leaf_links` adapts a live store into
that map.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from lib.topics.tree import UNCLASSIFIED, build_tree, is_bucket
from lib.topics.validation import audit_graph, split_by_severity

# Link sources that represent a human/synthesis decision — never auto-moved.
PROTECTED_SOURCES = frozenset({"manual", "reflect"})

DEFAULTS = {
    "min_leaf": 15,             # below this, splitting probably isn't worth it
    "min_per_topic": 3,         # a sub-topic thinner than this is noise
    "max_share": 0.9,           # one cluster holding > this fraction ⇒ weak split
    "max_bucket_children": 15,  # cap a bucket's leaf fan-out (index_root cost)
}


@dataclass
class SplitPlan:
    """A proposed split, as the clustering step would emit it."""

    leaf_id: str                     # the heavy leaf being split
    bucket_id: str                   # bucket the new siblings hang under
    new_topics: dict[str, dict]      # new_topic_id -> node body (parent_id == bucket_id)
    assignment: dict[str, str]       # memory_id -> destination (a new topic id OR leaf_id)


@dataclass
class SplitGateResult:
    ok: bool
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    stats: dict = field(default_factory=dict)


def check_split(plan: SplitPlan, graph: dict, leaf_links: dict[str, str], *,
                repo_path=None, thresholds: dict | None = None,
                allow_protected_move: bool = False) -> SplitGateResult:
    """Validate `plan` against the current `graph` and the leaf's current links
    (`{memory_id: source}`). Pure: no mutation, no I/O beyond `audit_graph`."""
    t = {**DEFAULTS, **(thresholds or {})}
    errors: list[str] = []
    warnings: list[str] = []

    errors += _check_structural(plan, graph, repo_path)
    errors += _check_conservation(plan, leaf_links)
    prov_errors, prov_warnings = _check_provenance(
        plan, leaf_links, allow_protected_move)
    errors += prov_errors
    warnings += prov_warnings
    warnings += _check_soft(plan, graph, leaf_links, t)

    dest_counts = _dest_counts(plan)
    moved_protected = _protected_moves(plan, leaf_links)
    stats = {
        "leaf_mems": len(leaf_links),
        "new_topics": len(plan.new_topics),
        "dest_counts": dest_counts,
        "moved_protected": len(moved_protected),
    }
    return SplitGateResult(
        ok=not errors, errors=errors, warnings=warnings, stats=stats)


# ── gate 1: structural ──────────────────────────────────────────

def _check_structural(plan: SplitPlan, graph: dict, repo_path) -> list[str]:
    errors: list[str] = []
    topics = graph.get("topics") or {}

    bucket = topics.get(plan.bucket_id)
    if bucket is None or not is_bucket(bucket):
        errors.append(f"target {plan.bucket_id!r} is not a kind:'bucket' node")

    leaf = topics.get(plan.leaf_id)
    if leaf is None:
        errors.append(f"leaf {plan.leaf_id!r} not in graph")
    elif leaf.get("parent_id") != plan.bucket_id:
        errors.append(
            f"leaf {plan.leaf_id!r} sits under {leaf.get('parent_id')!r}, "
            f"not the target bucket {plan.bucket_id!r}")

    for tid, body in plan.new_topics.items():
        if tid in topics:
            errors.append(f"new topic {tid!r} already exists")
        if body.get("parent_id") != plan.bucket_id:
            errors.append(
                f"new topic {tid!r} must be a bucket sibling: parent_id must be "
                f"{plan.bucket_id!r}, got {body.get('parent_id')!r}")

    errors += _audit_prospective(plan, graph, repo_path)
    return errors


def _audit_prospective(plan: SplitPlan, graph: dict, repo_path) -> list[str]:
    """Add the new nodes, re-audit, and fail on any error touching them or any
    new node that build_tree routes to `unclassified` (the 2-level trap)."""
    errors: list[str] = []
    prospective = {**graph,
                   "topics": {**(graph.get("topics") or {}), **plan.new_topics}}
    new_ids = set(plan.new_topics)

    issues = audit_graph(prospective, repo_path=repo_path)
    errs, _ = split_by_severity(issues)
    for e in errs:
        tids = set(e.topic_ids or ())
        if not tids or tids & new_ids:
            errors.append(f"audit error {e.code}: {e.message}")

    stray = new_ids & set(build_tree(prospective)["children"].get(UNCLASSIFIED, []))
    if stray:
        errors.append(
            f"new topics not nested under a bucket (routed to unclassified): "
            f"{sorted(stray)}")
    return errors


# ── gate 2: conservation ────────────────────────────────────────

def _check_conservation(plan: SplitPlan, leaf_links: dict[str, str]) -> list[str]:
    errors: list[str] = []
    original = set(leaf_links)
    assigned = set(plan.assignment)

    missing = original - assigned
    if missing:
        errors.append(
            f"{len(missing)} leaf memories have no destination "
            f"(e.g. {sorted(missing)[:3]})")
    extra = assigned - original
    if extra:
        errors.append(
            f"{len(extra)} assigned memories are not on the leaf "
            f"(e.g. {sorted(extra)[:3]})")

    valid_dests = set(plan.new_topics) | {plan.leaf_id}
    bad = sorted({d for d in plan.assignment.values() if d not in valid_dests})
    if bad:
        errors.append(f"assignment targets that are not split destinations: {bad[:3]}")

    return errors


# ── gate 3: provenance ──────────────────────────────────────────

def _protected_moves(plan: SplitPlan, leaf_links: dict[str, str]) -> list[str]:
    return [m for m, src in leaf_links.items()
            if src in PROTECTED_SOURCES
            and plan.assignment.get(m) not in (None, plan.leaf_id)]


def _check_provenance(plan: SplitPlan, leaf_links: dict[str, str],
                      allow_protected_move: bool) -> tuple[list[str], list[str]]:
    moved = _protected_moves(plan, leaf_links)
    if not moved:
        return [], []
    if allow_protected_move:
        return [], [f"moving {len(moved)} manual/reflect link(s) off the leaf "
                    f"(allow_protected_move=True)"]
    return [f"{len(moved)} manual/reflect link(s) would be moved off the leaf "
            f"without confirmation (e.g. {sorted(moved)[:3]})"], []


# ── soft gates ──────────────────────────────────────────────────

def _dest_counts(plan: SplitPlan) -> dict[str, int]:
    counts: dict[str, int] = {}
    for d in plan.assignment.values():
        counts[d] = counts.get(d, 0) + 1
    return counts


def _check_soft(plan: SplitPlan, graph: dict, leaf_links: dict[str, str],
                t: dict) -> list[str]:
    counts = _dest_counts(plan)
    new_counts = {d: c for d, c in counts.items() if d in plan.new_topics}
    return (_warn_eligibility(leaf_links, t)
            + _warn_cluster_quality(len(leaf_links), counts, new_counts, t)
            + _warn_fanout(plan, graph, t))


def _warn_eligibility(leaf_links: dict[str, str], t: dict) -> list[str]:
    n = len(leaf_links)
    if n < t["min_leaf"]:
        return [f"leaf has {n} mems (< min_leaf {t['min_leaf']}): "
                f"may not be worth splitting"]
    return []


def _warn_cluster_quality(n: int, counts: dict[str, int],
                          new_counts: dict[str, int], t: dict) -> list[str]:
    warnings: list[str] = []
    for tid, c in sorted(new_counts.items()):
        if c < t["min_per_topic"]:
            warnings.append(f"sub-topic {tid} holds {c} mems "
                            f"(< min_per_topic {t['min_per_topic']})")
    if n and counts and max(counts.values()) > t["max_share"] * n:
        warnings.append(f"one destination holds {max(counts.values())}/{n} "
                        f"(> {t['max_share']:.0%}): split barely discriminates")
    if new_counts and max(new_counts.values()) >= n:
        warnings.append("largest sub-topic is no smaller than the original "
                        "leaf: split does not reduce max-leaf size")
    return warnings


def _warn_fanout(plan: SplitPlan, graph: dict, t: dict) -> list[str]:
    cur = len(build_tree(graph)["children"].get(plan.bucket_id, []))
    after = cur + len(plan.new_topics)
    if after > t["max_bucket_children"]:
        return [f"bucket {plan.bucket_id} would have {after} children "
                f"(> cap {t['max_bucket_children']})"]
    return []


# ── live-store adapter ──────────────────────────────────────────

def gather_leaf_links(store, leaf_id: str) -> dict[str, str]:
    """`{memory_id: source}` for ACTIVE memories currently linked to `leaf_id`.

    The store exposes node→memory ids but not the link source, so this reads
    `memory_authoritative_topics` directly — the one fact the provenance gate
    needs. `store` is accepted for symmetry / future use.
    """
    from sqlmodel import select

    from lib.memory.engine import MemorySessionLocal
    from lib.memory.models import Memory, MemoryAuthoritativeTopic

    with MemorySessionLocal() as session:
        rows = session.exec(
            select(MemoryAuthoritativeTopic.memory_id,
                   MemoryAuthoritativeTopic.source)
            .join(Memory, Memory.id == MemoryAuthoritativeTopic.memory_id)
            .where(MemoryAuthoritativeTopic.topic_node_id == leaf_id,
                   Memory.status == "active")).all()
    return {mid: src for mid, src in rows}
