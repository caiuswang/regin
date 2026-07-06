"""`regin topics split-leaf` — carve a too-large topic leaf into sibling
sub-topics and redistribute its memories.

The pipeline has three seams, each isolated for testing:

1. **propose_clusters** (judgment, agentic) — an LLM reads the leaf's memories
   and groups them into coherent sub-themes. Fail-loud, mirroring
   `topic_classify.classify_memories`: no agent ⇒ `ClusterProposerUnavailable`.
2. **build_split_plan** (pure) — turns clusters into a `SplitPlan`: mints valid
   sibling topic ids under the leaf's bucket, builds node bodies (inheriting the
   leaf's globs/refs), and the per-memory assignment map.
3. **apply_split** (mutation) — re-runs the safety gate (`check_split`) and
   refuses unless it passes, then writes the new nodes to `topic.json`, syncs
   the snapshot, and relinks memories (link new destination, unlink the leaf for
   every moved memory). Conservation/provenance are the gate's job; this only
   executes a plan the gate already cleared.

Mutation uses the canonical topic.json writer + `import_from_disk` (the proven
path). Routing through `apply.apply_diff` is the production-hardening follow-up.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from lib.activity_log import get_activity_logger
from lib.topics.split_gate import (
    SplitGateResult, SplitPlan, check_split, gather_leaf_links,
)
from lib.topics.tree import is_bucket

log = get_activity_logger("topics")


class ClusterProposerUnavailable(RuntimeError):
    """No LLM reachable to cluster the leaf — fail-loud, never a silent split."""


@dataclass
class SplitCluster:
    label: str
    intent: str
    memory_ids: list[str] = field(default_factory=list)


# ── 1. propose (agentic) ────────────────────────────────────────

# The clustering prompt now lives as the editable `topic-split-leaf` surface
# (lib/prompts/surfaces/topics.py::_DEFAULT_BODY_SPLIT); `propose_clusters`
# wires the leaf's label/intent/memories into its `{{ … }}` slots.


def _memories_block(memories: list[dict]) -> str:
    parts = []
    for m in memories:
        title = (m.get("title") or "").strip()
        body = " ".join((m.get("body") or "").split())[:600]
        parts.append(f'<memory id="{m["id"]}">\n{title}\n{body}\n</memory>')
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


def _cluster_from_item(item: object, valid_ids: set[str]) -> "SplitCluster | None":
    if not isinstance(item, dict):
        return None
    ids = [m for m in (item.get("memory_ids") or []) if m in valid_ids]
    label = (item.get("label") or "").strip()
    if not (label and ids):
        return None
    return SplitCluster(
        label=label, intent=(item.get("intent") or "").strip(), memory_ids=ids)


def _parse_clusters(answer: str, valid_ids: set[str]) -> list[SplitCluster]:
    """Parse the LLM's JSON array into clusters, keeping only ids we gave it."""
    out = (_cluster_from_item(item, valid_ids) for item in _json_array(answer))
    return [c for c in out if c is not None]


def propose_clusters(leaf_node: dict, memories: list[dict], llm, *,
                     lo: int = 2, hi: int = 5) -> list[SplitCluster]:
    """Agentically cluster `memories` into sub-themes. Fail-loud: raises
    `ClusterProposerUnavailable` when the LLM returns nothing."""
    from lib.prompts import render_surface
    from lib.prompts.surfaces.topics import SPLIT_LEAF_SURFACE_ID
    prompt = render_surface(SPLIT_LEAF_SURFACE_ID, {
        "label": leaf_node.get("label") or "topic",
        "intent": " ".join((leaf_node.get("intent") or "").split())[:400],
        "memories": _memories_block(memories), "lo": lo, "hi": hi})
    answer = llm.complete(prompt, max_tokens=4096, surface_id=SPLIT_LEAF_SURFACE_ID)
    if not answer:
        raise ClusterProposerUnavailable(
            "no LLM completion — is an external agent configured?")
    clusters = _parse_clusters(answer, {m["id"] for m in memories})
    log.write("split_clusters_proposed",
              leaf=leaf_node.get("label"), clusters=len(clusters))
    return clusters


# ── 2. build plan (pure) ────────────────────────────────────────

def _slug(label: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", label.lower()).strip("-")
    return s or "topic"


def _unique_id(base: str, taken: set[str]) -> str:
    if base not in taken:
        return base
    i = 2
    while f"{base}-{i}" in taken:
        i += 1
    return f"{base}-{i}"


def _node_body(cluster: SplitCluster, bucket_id: str, leaf_node: dict) -> dict:
    """A new sibling-leaf node, inheriting the leaf's globs/refs so it stays
    code-anchored; label/intent come from the cluster."""
    return {
        "label": cluster.label,
        "intent": cluster.intent or f"Memories about {cluster.label}.",
        "status": "active",
        "parent_id": bucket_id,
        "aliases": [],
        "refs": list(leaf_node.get("refs") or [])[:6],
        "edges": [],
        "commands": [],
        "include_globs": list(leaf_node.get("include_globs") or []),
        "exclude_globs": [],
    }


def build_split_plan(leaf_id: str, bucket_id: str, clusters: list[SplitCluster],
                     leaf_links: dict[str, str], graph: dict, *,
                     keep_on_leaf: "set[str] | None" = None) -> SplitPlan:
    """Assemble a `SplitPlan` from clusters. Mints unique sibling ids; any leaf
    memory not placed by a cluster is kept on the leaf (so conservation holds)."""
    leaf_node = (graph.get("topics") or {}).get(leaf_id, {})
    taken = set(graph.get("topics") or {})
    new_topics: dict[str, dict] = {}
    assignment: dict[str, str] = {}

    for cluster in clusters:
        tid = _unique_id(_slug(cluster.label), taken)
        taken.add(tid)
        new_topics[tid] = _node_body(cluster, bucket_id, leaf_node)
        for mid in cluster.memory_ids:
            assignment[mid] = tid

    keep = keep_on_leaf or set()
    for mid in leaf_links:
        if mid not in assignment or mid in keep:
            assignment[mid] = leaf_id   # unplaced / explicitly-kept stay put

    return SplitPlan(leaf_id=leaf_id, bucket_id=bucket_id,
                     new_topics=new_topics, assignment=assignment)


# ── 3. apply (mutation, gated) ──────────────────────────────────

def bucket_for_leaf(graph: dict, leaf_id: str) -> "str | None":
    """The leaf's bucket = its `parent_id`, but only if that is a real bucket."""
    leaf = (graph.get("topics") or {}).get(leaf_id)
    if leaf is None:
        return None
    parent = leaf.get("parent_id")
    parent_node = (graph.get("topics") or {}).get(parent or "")
    return parent if parent_node and is_bucket(parent_node) else None


def apply_split(store, repo_path, plan: SplitPlan, graph: dict, *,
                allow_protected_move: bool = False) -> dict:
    """Gate, then execute `plan`. Raises `ValueError` (with the gate errors) if
    the plan does not pass — you cannot apply an ungated split."""
    leaf_links = gather_leaf_links(store, plan.leaf_id)
    gate = check_split(plan, graph, leaf_links, repo_path=repo_path,
                       allow_protected_move=allow_protected_move)
    if not gate.ok:
        raise ValueError("split gate failed: " + "; ".join(gate.errors))

    _write_nodes(repo_path, graph, plan)
    try:
        moved = _relink(store, plan, leaf_links)
    except Exception:
        # A relink failure leaves the DB clean (`_relink` rolls back its own
        # partial moves), but the new nodes are already on disk + snapshot.
        # Remove them so a failed apply doesn't strand orphan empty sub-topics
        # — a re-run mints fresh ids and would never reuse them. Best-effort:
        # covers exceptions, not a hard process kill (the apply_diff routing
        # follow-up is what makes that case durable).
        _remove_nodes(repo_path, graph, plan)
        raise
    log.write("split_applied", leaf=plan.leaf_id, bucket=plan.bucket_id,
              new_topics=len(plan.new_topics), moved=moved)
    return {"new_topics": list(plan.new_topics), "moved": moved,
            "kept_on_leaf": sum(1 for d in plan.assignment.values()
                                if d == plan.leaf_id)}


def _write_nodes(repo_path, graph: dict, plan: SplitPlan) -> None:
    """Add the new nodes to the base graph (canonical write) and sync the snapshot."""
    from lib.topics.core import load_graph, write_graph_to_disk
    from lib.topics.graph_io import import_from_disk

    disk = load_graph(repo_path)
    disk["topics"].update(plan.new_topics)
    write_graph_to_disk(repo_path, disk)
    graph["topics"].update(plan.new_topics)   # keep caller's view consistent
    import_from_disk(repo_path, reason="split-leaf")


def _remove_nodes(repo_path, graph: dict, plan: SplitPlan) -> None:
    """Undo `_write_nodes`: drop the plan's new nodes from the base graph + the
    caller's graph and re-sync, so a failed apply leaves no orphan sub-topics."""
    from lib.topics.core import load_graph, write_graph_to_disk
    from lib.topics.graph_io import import_from_disk

    disk = load_graph(repo_path)
    for tid in plan.new_topics:
        disk["topics"].pop(tid, None)
        graph["topics"].pop(tid, None)
    write_graph_to_disk(repo_path, disk)
    import_from_disk(repo_path, reason="split-leaf-rollback")


def _relink(store, plan: SplitPlan, leaf_links: dict[str, str]) -> int:
    """Link each moved memory to its new sub-topic and unlink it from the leaf.
    Memories whose destination is the leaf are left untouched. On any failure,
    the moves committed so far are reversed (restoring each memory's original
    leaf link + source from `leaf_links`) before the error propagates, so a
    partial relink never half-moves a memory."""
    done: list[tuple[str, str]] = []
    try:
        for mid, dest in plan.assignment.items():
            if dest == plan.leaf_id:
                continue
            store.link_authoritative_topic(mid, dest, source="split")
            store.unlink_authoritative_topic(mid, plan.leaf_id)
            done.append((mid, dest))
    except Exception:
        for mid, dest in reversed(done):
            store.unlink_authoritative_topic(mid, dest)
            store.link_authoritative_topic(
                mid, plan.leaf_id, source=leaf_links.get(mid, "split"))
        raise
    return len(done)


def gate_only(store, repo_path, plan: SplitPlan, graph: dict, *,
              allow_protected_move: bool = False) -> SplitGateResult:
    """Run the gate without mutating — backs the CLI's dry-run."""
    leaf_links = gather_leaf_links(store, plan.leaf_id)
    return check_split(plan, graph, leaf_links, repo_path=repo_path,
                       allow_protected_move=allow_protected_move)
