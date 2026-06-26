"""Read-only helpers over the authoritative topic graph's `parent_id` tree.

The approved graph (`.regin/topics/topic.json`) carries an optional
`parent_id` on every node — null for the ~dozen top-level taxonomy buckets,
a parent id for each leaf topic. These pure functions turn that flat node
map into a navigable tree the memory MCP `index_*` tools walk coarse-to-fine.

No I/O, no mutation: callers pass the already-loaded graph dict (see
`lib.topics.graph_io.load_authoritative_graph`).
"""

from __future__ import annotations

from typing import Any, Optional

# Reserved bucket that collects leaf topics with no (or a non-bucket) parent,
# so an unplaced topic is visibly pending instead of polluting the top level.
UNCLASSIFIED = "unclassified"


def _topics(graph: dict) -> dict[str, Any]:
    return graph.get("topics") or {}


def is_bucket(node: dict) -> bool:
    """A top-level taxonomy node — the only valid `parent_id` targets and the
    only nodes shown as roots. Marked `kind: "bucket"` in topic.json."""
    return node.get("kind") == "bucket"


def blurb_of(node: dict) -> str:
    """The router-card line a node shows during navigation. Falls back to a
    truncated `intent` when a node has no authored `blurb` (the migrated
    leaf topics rely on this)."""
    return (node.get("blurb") or "").strip() or (node.get("intent") or "")[:120]


def effective_parent(topics: dict[str, Any], buckets: set[str], tid: str) -> str:
    """The node `tid` actually hangs under in the navigation tree.

    A non-bucket topic nests under its `parent_id` whenever that parent
    *exists as a topic* — bucket **or** leaf — so a user-built multi-level
    grouping (a leaf under a leaf, even one not yet placed under a bucket
    itself) survives intact instead of being flattened. It falls back to the
    reserved `UNCLASSIFIED`
    bucket only when the chain has no real root: a `parent_id` that is null,
    dangling (points at no topic), or part of a cycle. So an unplaced topic is
    visibly pending and never silently dropped, while a placed sub-topic stays
    under its declared parent. This is the single rule both `build_tree` and
    the `topic.unclassified` audit consult, so the tree and the audit can never
    disagree about what counts as classified."""
    parent = (topics.get(tid) or {}).get("parent_id")
    if parent is None or parent not in topics:
        return UNCLASSIFIED
    seen = {tid}
    cur: Optional[str] = parent
    while cur is not None and cur in topics:
        if cur in seen:
            return UNCLASSIFIED          # cycle in the chain → quarantine
        if cur in buckets:
            break                        # chain reaches a real bucket
        seen.add(cur)
        cur = (topics.get(cur) or {}).get("parent_id")
    return parent


def build_tree(graph: dict) -> dict[str, Any]:
    """`{"roots", "children"}` derived from `kind:"bucket"` + `parent_id`.

    `roots` = bucket ids, sorted (the curated top level). Each non-bucket node
    hangs under its `parent_id` whenever that parent exists (see
    `effective_parent`), so multi-level groupings nest; a node whose chain has
    no real root (null, dangling, or cyclic `parent_id`) routes to the reserved
    `UNCLASSIFIED` bucket — visibly pending, never silently promoted to the top
    level. `UNCLASSIFIED` is shown as a root only when it actually holds
    something, and is surfaced even if the graph never declared the bucket node
    — so quarantined leaves are never dropped from the walk."""
    topics = _topics(graph)
    buckets = {tid for tid, n in topics.items() if is_bucket(n)}
    children: dict[str, list[str]] = {}
    for tid in topics:
        if tid in buckets:
            continue
        children.setdefault(effective_parent(topics, buckets, tid), []).append(tid)
    for kids in children.values():
        kids.sort()
    roots = [b for b in buckets if b != UNCLASSIFIED]
    if children.get(UNCLASSIFIED):  # surface it even if the node is undeclared
        roots.append(UNCLASSIFIED)
    return {"roots": sorted(roots), "children": children}


def subtree_ids(graph: dict, node_id: str) -> list[str]:
    """`node_id` plus every descendant (depth-first), cycle-safe. Returns
    `[node_id]` for a leaf, and `[]` for an unknown id."""
    topics = _topics(graph)
    if node_id not in topics:
        return []
    children = build_tree(graph)["children"]
    out: list[str] = []
    seen: set[str] = set()
    stack = [node_id]
    while stack:
        cur = stack.pop()
        if cur in seen:
            continue
        seen.add(cur)
        out.append(cur)
        stack.extend(children.get(cur, []))
    return out


def node_card(graph: dict, node_id: str, *,
              mem_count: Optional[int] = None) -> Optional[dict]:
    """Compact navigation card for one node: id, label, blurb, child/ref
    counts, plus an optional caller-supplied subtree memory count. None for
    an unknown id."""
    node = _topics(graph).get(node_id)
    if node is None:
        return None
    children = build_tree(graph)["children"].get(node_id, [])
    return {
        "id": node_id,
        "label": node.get("label") or node_id,
        "blurb": blurb_of(node),
        "child_count": len(children),
        "ref_count": len(node.get("refs") or []),
        "mem_count": mem_count,
    }
