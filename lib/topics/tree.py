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


def _topics(graph: dict) -> dict[str, Any]:
    return graph.get("topics") or {}


def blurb_of(node: dict) -> str:
    """The router-card line a node shows during navigation. Falls back to a
    truncated `intent` when a node has no authored `blurb` (the migrated
    leaf topics rely on this)."""
    return (node.get("blurb") or "").strip() or (node.get("intent") or "")[:120]


def build_tree(graph: dict) -> dict[str, Any]:
    """`{"roots", "children"}` derived from `parent_id`. `roots` = ids with
    no (or a dangling) parent, sorted; `children[pid]` = sorted child ids. A
    node whose `parent_id` points nowhere is treated as a root, so even a
    malformed graph yields a walkable forest."""
    topics = _topics(graph)
    children: dict[str, list[str]] = {}
    roots: list[str] = []
    for tid, node in topics.items():
        parent = node.get("parent_id")
        if parent and parent in topics:
            children.setdefault(parent, []).append(tid)
        else:
            roots.append(tid)
    for kids in children.values():
        kids.sort()
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
