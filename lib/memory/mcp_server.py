"""`recall` — the on-demand memory MCP server.

A stdio MCP server exposing one tool, `recall`, for deeper mid-task pulls
beyond the few memories the UserPromptSubmit hook auto-injects. Unlike
`send_to_user`'s deliberately regin-blind server, this one *must* read
the memory DB — so regin imports happen lazily inside the tool call,
keeping server startup instant and shielding tool listing from a DB
hiccup.

The server process lives as long as the session, so the dense + rerank
legs are affordable here (models load once, stay warm); `mode='auto'`
still degrades to FTS-only when torch/transformers are absent.
"""

from __future__ import annotations

import os
import sys

# The server is spawned by the agent harness with an arbitrary cwd; make
# `lib.*` importable the same way `cli/regin.py` does.
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from typing import Optional

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("memory")


def _format_memory(m: dict, *, score: Optional[float] = None) -> str:
    head = (f"[{m['kind']}|{m['scope']}|score {score:.2f}]"
            if score is not None else f"[{m['kind']}|{m['scope']}]")
    title = f" — {m['title']}" if m.get("title") else ""
    src = f" (from session {m['source_trace_id']})" if m.get("source_trace_id") else ""
    return f"{head}{title}\n{m['body']}{src}"


def _format_hit(hit) -> str:
    return _format_memory(hit.memory, score=hit.score)


@mcp.tool()
def recall(query: str, top_k: int = 5, scope: str = "") -> str:
    """Recall experience from regin's cross-session agent memory.

    Use mid-task when past sessions may have hit the same problem:
    before debugging something that feels familiar, before re-deciding
    an architectural question, or when the auto-injected
    <recalled_experience> block hints there is more. Complements (does
    not replace) repo docs — memories are distilled session experience.

    Args:
        query: What you want experience about. Keyword-style works best
            ("playwright stale backend", "schema drift alembic").
        top_k: Max memories to return (default 5).
        scope: Optional repo scope filter like "repo:regin"; empty
            searches every scope.

    Returns:
        Matching memories (best first) with kind, scope, score, and the
        originating session id — or a note that nothing matched.
    """
    import lib.memory as memory
    if not memory.enabled():
        return "agent memory is disabled (settings.agent_memory.enabled)"
    hits = memory.recall(query, top_k=max(1, min(int(top_k), 20)),
                         scope=scope or None, mode="auto")
    if not hits:
        return "no stored experience matched this query"
    return "\n\n".join(_format_hit(h) for h in hits)


def _load_graph():
    """The repo's approved topic graph (the taxonomy tree the index walks)."""
    from lib.settings import settings
    from lib.topics.graph_io import load_authoritative_graph
    return load_authoritative_graph(str(settings.project_root))


def _subtree_mem_count(store, graph, node_id: str, scope: str) -> int:
    from lib.topics.tree import subtree_ids
    ids = subtree_ids(graph, node_id)
    return len(store.memories_for_topic_subtree(ids, scope=scope or None))


def _format_card(card: dict, mem_count: Optional[int]) -> str:
    shape = f"{card['child_count']} sub" if card["child_count"] else "leaf"
    mc = f", {mem_count} mem" if mem_count is not None else ""
    return f"- {card['id']} · {card['label']} ({shape}{mc})\n    {card['blurb']}"


def _format_refs(node: dict) -> Optional[str]:
    refs = node.get("refs") or []
    if not refs:
        return None
    return "refs:\n" + "\n".join(
        f"  {r.get('path')} ({r.get('role')})" for r in refs)


def _format_subtree_memories(store, ids: list[str], top_k: int) -> str:
    if not ids:
        return "(no memories linked under this subtree)"
    cap = max(1, min(int(top_k), 20))
    hits = (store.get_dict(mid) for mid in ids[:cap])
    return "\n\n".join(_format_memory(m) for m in hits if m)


@mcp.tool()
def index_root(scope: str = "") -> str:
    """List the top-level topic buckets — the taxonomy roots — to start a
    coarse-to-fine walk of regin's knowledge instead of a blind semantic
    recall.

    Use at the start of a task to pick the 1-3 buckets it touches, then
    `index_expand(node_id)` to drill into a bucket's children, then
    `index_fetch(node_id)` to read the memories + file refs under it. Fall
    back to `recall` when the tree dead-ends (a bucket with no memories, or
    a long-tail topic not yet classified).

    Args:
        scope: Optional repo scope filter like "repo:regin"; empty counts
            memories across every scope.

    Returns:
        Each root as `id · label (N sub, M mem)` with its router blurb.
    """
    import lib.memory as memory
    from lib.topics.tree import build_tree, node_card
    if not memory.enabled():
        return "agent memory is disabled (settings.agent_memory.enabled)"
    graph = _load_graph()
    store = memory.get_store()
    lines = [_format_card(node_card(graph, rid),
                          _subtree_mem_count(store, graph, rid, scope))
             for rid in build_tree(graph)["roots"]]
    if not lines:
        return "topic graph has no nodes (run `regin topics scan`)"
    return "top-level topics (then index_expand / index_fetch):\n" + \
        "\n".join(lines)


@mcp.tool()
def index_expand(node_id: str, scope: str = "") -> str:
    """Drill into one topic node: show its card plus its children, so you
    can decide whether to descend further or `index_fetch` here.

    Args:
        node_id: A topic node id from `index_root` / a prior `index_expand`.
        scope: Optional repo scope filter like "repo:regin".

    Returns:
        The node's blurb + subtree memory count, then each child as a card.
        A leaf node says so and points you at `index_fetch`.
    """
    import lib.memory as memory
    from lib.topics.tree import build_tree, node_card
    if not memory.enabled():
        return "agent memory is disabled (settings.agent_memory.enabled)"
    graph = _load_graph()
    card = node_card(graph, node_id)
    if card is None:
        return f"no topic node {node_id!r} — call index_root to list roots"
    store = memory.get_store()
    self_mc = _subtree_mem_count(store, graph, node_id, scope)
    head = (f"{node_id} · {card['label']} "
            f"({self_mc} mem in subtree)\n{card['blurb']}")
    kids = build_tree(graph)["children"].get(node_id, [])
    if not kids:
        return head + "\n\n(leaf — use index_fetch to read its memories)"
    lines = [_format_card(node_card(graph, k),
                          _subtree_mem_count(store, graph, k, scope))
             for k in kids]
    return head + "\n\nchildren:\n" + "\n".join(lines)


@mcp.tool()
def index_fetch(node_id: str, top_k: int = 8, scope: str = "") -> str:
    """Read the memories under a topic node's whole subtree, plus the node's
    file refs — the leaf step of the navigation walk.

    Args:
        node_id: The topic node to read (its subtree's memories are pulled).
        top_k: Max memories to return (default 8, capped at 20).
        scope: Optional repo scope filter like "repo:regin".

    Returns:
        The node's file refs, then matching memories (kind, scope, body,
        originating session) — or a note that the subtree has none.
    """
    import lib.memory as memory
    from lib.topics.tree import subtree_ids
    if not memory.enabled():
        return "agent memory is disabled (settings.agent_memory.enabled)"
    graph = _load_graph()
    node = (graph.get("topics") or {}).get(node_id)
    if node is None:
        return f"no topic node {node_id!r} — call index_root to list roots"
    store = memory.get_store()
    ids = store.memories_for_topic_subtree(subtree_ids(graph, node_id),
                                           scope=scope or None)
    parts = [p for p in (_format_refs(node),
                         _format_subtree_memories(store, ids, top_k)) if p]
    return f"{node_id}:\n" + "\n\n".join(parts)


if __name__ == "__main__":
    mcp.run()
