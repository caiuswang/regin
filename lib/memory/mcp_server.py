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
    """The repo's approved topic graph (the taxonomy tree the index walks),
    plus the global meta-roots overlay (`skills` / `preferences`) so
    cross-repo skill-usage and preference memories are navigable from here."""
    from lib.settings import settings
    from lib.topics.graph_io import load_authoritative_graph
    from lib.topics.meta_roots import merge_meta_roots
    return merge_meta_roots(
        load_authoritative_graph(str(settings.project_root)))


def _subtree_mem_count(store, graph, node_id: str, scope: str) -> int:
    from lib.topics.tree import subtree_ids
    ids = subtree_ids(graph, node_id)
    return len(store.memories_for_topic_subtree(ids, scope=scope or None))


def _format_card(card: dict, mem_count: Optional[int]) -> str:
    shape = f"{card['child_count']} sub" if card["child_count"] else "leaf"
    mc = f", {mem_count} mem" if mem_count is not None else ""
    return f"- {card['id']} · {card['label']} ({shape}{mc})\n    {card['blurb']}"


def _wiki_section(node_id: str) -> str:
    """Address of the curated per-topic wiki — the agent Reads it if it wants
    the narrative. We hand over the path, not the contents."""
    from lib.settings import settings
    from lib.topics.wiki import wiki_dir
    if not (wiki_dir(settings.project_root) / f"{node_id}.md").exists():
        return "## wiki\n(none — bucket or un-accepted topic)"
    return (f"## wiki\n.regin/topics/wiki/{node_id}.md  "
            f"(Read this for the full topic narrative)")


_REF_CAP = 12  # role-bearing anchors are enough; the wiki has the full file map


def _refs_section(node: dict) -> str:
    """High-signal source-file addresses (path + role). Role-bearing anchors
    first, capped — the full file list lives in the wiki, so we don't dump
    every low-signal path here."""
    refs = node.get("refs") or []
    if not refs:
        return "## source refs\n(none)"
    ranked = sorted(refs, key=lambda r: (r.get("role") in (None, ""),))
    shown = ranked[:_REF_CAP]
    lines = [f"  {r.get('path')} ({r.get('role') or '—'})" for r in shown]
    more = len(refs) - len(shown)
    tail = f"\n  … +{more} more (full file map in the wiki)" if more > 0 else ""
    return f"## source refs ({len(refs)})\n" + "\n".join(lines) + tail


def _memory_headline(m: dict) -> str:
    title = m.get("title") or (m.get("body") or "").strip()[:60] or "(untitled)"
    return f"- [{m['kind']}|imp {m.get('importance', 0):.1f}] {title}  (id: {m['id']})"


def _memories_section(store, ids: list[str], top_k: int) -> str:
    """Memory addresses (kind · title · id), importance-ranked and capped —
    labels for the agent to choose from, not a body dump. The agent reads a
    chosen one with `recall`."""
    total = len(ids)
    if not total:
        return "## memories\n(none linked under this subtree)"
    cap = max(1, min(int(top_k), 50))
    shown = [m for m in (store.get_dict(mid) for mid in ids[:cap]) if m]
    lines = "\n".join(_memory_headline(m) for m in shown)
    more = total - len(shown)
    tail = f"\n… +{more} more (raise top_k)" if more > 0 else ""
    return (f"## memories ({total}, importance-ranked; titles only — "
            f"recall to read one)\n{lines}{tail}")


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
def index_fetch(node_id: str, top_k: int = 10, scope: str = "") -> str:
    """Read a topic node — the leaf step of the navigation walk. Returns
    **addresses, not contents**, so you spend tokens only on what you choose
    to open:

    - the curated **wiki path** (`.regin/topics/wiki/<id>.md`) — Read it for
      the topic narrative;
    - the topic's **source-file refs** (path + role) — Read the relevant ones;
    - its **memories** as importance-ranked titles + ids — `recall` the one
      you want.

    Nothing here dumps a wiki body or memory bodies; you decide what's worth
    reading. This keeps a heavily-used topic from flooding the context.

    Args:
        node_id: The topic node to read (its subtree's memories are listed).
        top_k: Max memory titles to list (default 10, capped at 50).
        scope: Optional repo scope filter like "repo:regin".

    Returns:
        `## wiki`, `## source refs`, `## memories` sections of pointers.
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
    label = node.get("label") or node_id
    sections = [_wiki_section(node_id), _refs_section(node),
                _memories_section(store, ids, top_k)]
    return f"{node_id} · {label}\n\n" + "\n\n".join(sections)


@mcp.tool()
def gate(name: str, session_id: str) -> str:
    """PASS/FAIL a trace-derived span gate for a session (the MCP-native form
    of `regin gate <name> --session <id>`).

    A gate turns an *unenforced* skill step into a checkable invariant: the
    step's tool leaves spans, and the gate asserts they exist for the session.
    `recall-ran` is goal-verified-treenav's anti-skip (did the memory-tree-nav
    recall arm fire?); `task-recall-ran` is goal-verified's.

    `session_id` is REQUIRED and must be the *caller's* session id (read it from
    `$CLAUDE_CODE_SESSION_ID`). This server is shared and long-lived, so its own
    environment holds the session id of whichever session first spawned it — not
    the caller's — which is why the gate cannot infer the session itself.

    Args:
        name: Gate key, e.g. "recall-ran" or "task-recall-ran".
        session_id: The caller's Claude Code session/trace id.

    Returns:
        "<gate description> spans this session: N" plus a PASS/FAIL verdict
        line, mirroring the `regin gate` CLI.
    """
    from lib.trace.span_gates import GATES, span_count

    spec = GATES.get(name)
    if spec is None:
        return f"unknown gate {name!r} — valid gates: {', '.join(sorted(GATES))}"
    if not session_id:
        return (
            "session_id is required — pass your $CLAUDE_CODE_SESSION_ID. This "
            "shared memory server cannot infer the caller's session (its own "
            "environment holds the spawner session's id, not yours)."
        )

    n = span_count(session_id, spec)
    passed = n > 0

    from lib.activity_log import get_activity_logger
    get_activity_logger("gate").read(
        "gate_checked", gate=name, session=session_id, spans=n, passed=passed)

    verdict = ("GATE PASS — arm ran" if passed else
               "GATE FAIL — no spans for this gate; you skipped the step. "
               "Go back and run it.")
    return f"{spec.describe} spans this session: {n}\n{verdict}"


if __name__ == "__main__":
    mcp.run()
