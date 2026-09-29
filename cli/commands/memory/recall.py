"""`regin memory` read side: recall, read, index-* and recall-for-task."""

from __future__ import annotations

import json
from typing import Optional

import typer

from ._common import (
    _SESSION_OPT,
    _fingerprint,
    _merged_graph,
    _print_memory_line,
    _tool_scope,
    _walk,
    memory_app,
    tree_nav_module,
)


@memory_app.command("recall")
def cmd_recall(
    query: str = typer.Argument(..., help="What to recall experience about"),
    top_k: int = typer.Option(5, "--top-k"),
    scope: Optional[str] = typer.Option(
        None, "--scope",
        help="e.g. repo:regin; omitted applies the scope policy, 'all' opts out"),
    fts_only: bool = typer.Option(False, "--fts-only",
                                  help="Skip the dense leg + rerank"),
    json_out: bool = typer.Option(False, "--json"),
    compact: bool = typer.Option(
        False, "--compact",
        help="One inspection line per hit (tier/status/importance/use) instead "
             "of the agent-facing lead + part index"),
    session: Optional[str] = _SESSION_OPT,
) -> None:
    """Semantic recall across the whole store.

    Prints what `mcp__memory__recall` returns — the lead of each hit plus an
    index of what was withheld, which is what makes the `regin memory read <id>
    --part …` follow-up discoverable without MCP. `--compact` gives the older
    one-line-per-hit listing for eyeballing the store.
    """
    import lib.memory as memory
    tree_nav = tree_nav_module()
    scope = _tool_scope(scope)
    mode = "fts" if fts_only else "auto"
    # The kill switch has to be honoured HERE: `memory.recall` itself does not
    # consult `enabled()`, so reaching it while the subsystem is off would
    # return real bodies, bump their recall_count, and credit the anti-skip
    # gate. `--json` keeps its old contract of an empty array rather than a
    # prose line a consumer's `json.loads` would choke on.
    if not memory.enabled():
        print(json.dumps([], indent=2) if json_out else tree_nav.DISABLED)
        raise typer.Exit(0 if json_out else 1)
    # One clamp for every output mode, matching `render_recall`'s — otherwise
    # `--json --top-k 50` returns 50 hits where the default render returns 20.
    top_k = max(1, min(int(top_k), 20))
    if not (json_out or compact):
        rendered = tree_nav.render_recall(query, top_k=top_k, scope=scope or "",
                                          mode=mode)
        _walk(session, "recall", rendered, query=query[:120])
        raise typer.Exit(0 if tree_nav.is_result(rendered) else 1)
    hits = memory.recall(query, top_k=top_k, scope=scope, mode=mode)
    # `--session` fingerprints these legs too. Honouring it only on the default
    # output would make `recall --json --session $SID` a silent no-op, and the
    # agent that scripted it would then be told it skipped the recall arm.
    if hits:
        _fingerprint(session, "recall", query=query[:120])
    _print_inspection_hits(hits, json_out)


def _print_inspection_hits(hits, json_out: bool) -> None:
    """The `--json` / `--compact` legs: machine rows, or one line per hit."""
    if json_out:
        print(json.dumps([{**h.memory, "score": h.score,
                           "score_kind": h.score_kind} for h in hits], indent=2))
        return
    if not hits:
        print("no stored experience matched")
        raise typer.Exit(1)
    for h in hits:
        print(f"[{h.score:.3f} {h.score_kind}]", end="")
        _print_memory_line(h.memory)


@memory_app.command("read")
def cmd_read(
    memory_id: str = typer.Argument(..., help="Memory id (a unique prefix works)"),
    part: Optional[str] = typer.Option(
        None, "--part", help="Section name from the hit's part index, e.g. 'how'"),
    session: Optional[str] = _SESSION_OPT,
) -> None:
    """Read one memory in full — the follow-up to a recall hit whose lead
    ended in a `⋯ +N chars` index (CLI form of `mcp__memory__memory_read`)."""
    _walk(session, "read",
          tree_nav_module().render_memory_read(memory_id, part=part or ""),
          memory_id=memory_id)


@memory_app.command("index-root")
def cmd_index_root(
    scope: Optional[str] = typer.Option(
        None, "--scope",
        help="e.g. repo:regin; omitted applies the scope policy, 'all' opts out"),
    session: Optional[str] = _SESSION_OPT,
) -> None:
    """List the top-level topic buckets — step 1 of the tree walk (CLI form of
    `mcp__memory__index_root`, for harnesses without MCP)."""
    scope = _tool_scope(scope)
    _walk(session, "index-root",
          tree_nav_module().render_index_root(scope or ""), scope=scope or "")


@memory_app.command("index-expand")
def cmd_index_expand(
    node_id: str = typer.Argument(..., help="Topic node id from index-root"),
    scope: Optional[str] = typer.Option(
        None, "--scope",
        help="e.g. repo:regin; omitted applies the scope policy, 'all' opts out"),
    session: Optional[str] = _SESSION_OPT,
) -> None:
    """Drill into one topic node and list its children — step 2 of the walk."""
    scope = _tool_scope(scope)
    _walk(session, "index-expand",
          tree_nav_module().render_index_expand(node_id, scope or ""),
          node_id=node_id)


@memory_app.command("index-fetch")
def cmd_index_fetch(
    node_id: str = typer.Argument(..., help="Topic node to read"),
    top_k: int = typer.Option(10, "--top-k", help="Max memory titles (max 50)"),
    scope: Optional[str] = typer.Option(
        None, "--scope",
        help="e.g. repo:regin; omitted applies the scope policy, 'all' opts out"),
    reinforce: bool = typer.Option(
        True, "--reinforce/--no-reinforce",
        help="Count as wiki usage; pass --no-reinforce for audit sweeps"),
    session: Optional[str] = _SESSION_OPT,
) -> None:
    """Read a topic node's wiki path, source refs and memory titles — step 3,
    the leaf. Returns addresses, not contents."""
    scope = _tool_scope(scope)
    _walk(session, "index-fetch",
          tree_nav_module().render_index_fetch(
              node_id, top_k=top_k, scope=scope or "", reinforce=reinforce),
          node_id=node_id, reinforce=reinforce)


def _resolve_subsystem_node(graph: dict, subsystem: Optional[str], task: str,
                            repo_path) -> Optional[str]:
    """The subsystem topic node for a task-scoped recall: a valid `--subsystem`
    override if given, else the node the task text keyword-routes to (or None
    when it routes nowhere)."""
    if subsystem and subsystem in (graph.get("topics") or {}):
        return subsystem
    from lib.topics.route import match_topic
    routed = match_topic(repo_path, task)
    return routed.get("id") if routed else None


def _task_subtree_memories(store, graph: dict, node_id: Optional[str],
                           top_k: int, scope: Optional[str]) -> list[dict]:
    """Structure-first retrieval: the subsystem subtree's active memories
    (importance/recall ranked — NOT query similarity), hydrated and capped to
    `top_k`. [] for an unrouted task. This is the leg that makes the topic tree
    the retriever, so a stage-language task description can't filter the right
    memories out of the candidate pool."""
    if not node_id:
        return []
    from lib.topics.tree import subtree_ids
    ids = store.memories_for_topic_subtree(subtree_ids(graph, node_id),
                                           scope=scope)
    return [d for d in (store.get_dict(mid) for mid in ids[:top_k]) if d]


def _record_task_offered(store, session: str, mems: list[dict],
                         task: str) -> None:
    """Log the surfaced memories as *offered* (an injection event, no
    recall_count bump) — the same exposure-without-usefulness record
    `goal_preflight.record_offered` writes. This is what folds structure-first
    task recall into the engagement-grading loop: without it, these hits are
    never scored, so we could never compare its engaged-rate against the
    flat-recall baseline. Best-effort — recall must never break on it."""
    try:
        ids = [m["id"] for m in mems if m.get("id")]
        if ids:
            store.record_injections(session, ids, query=task)
    except Exception:
        pass


def _hit_summaries(mems: list[dict]) -> list[dict]:
    """Compact, machine-readable view of the surfaced memories — the shared
    source for both the `memory.recall.task` span attributes and the `--json`
    output, so the two never drift."""
    return [{"id": m.get("id"), "kind": m.get("kind"),
             "title": m.get("title"), "scope": m.get("scope")} for m in mems]


def _render_block(mems: list[dict]) -> str:
    """The default `<recalled_experience>` text block a spawner pastes atop a
    sub-task prompt. `(no filed experience)` when the subtree was empty."""
    from lib.memory.skill_experience import format_memory_line
    body = [format_memory_line(m) for m in mems] or ["(no filed experience)"]
    return "\n".join([
        "<recalled_experience>",
        "Experience recalled from regin's past sessions. It may be stale —",
        "verify against the current code before relying on it.",
        *body,
        "</recalled_experience>",
    ])


@memory_app.command("recall-for-task")
def cmd_recall_for_task(
    task: str = typer.Argument(
        ..., help="Sub-task description to recall experience for"),
    session: str = typer.Option(
        ..., "--session", "-s",
        help="Session/trace id to attach the memory.recall.task span to"),
    subsystem: Optional[str] = typer.Option(
        None, "--subsystem",
        help="Topic node id to scope retrieval to; if omitted, routed from "
             "the task text"),
    top_k: int = typer.Option(3, "--top-k"),
    scope: Optional[str] = typer.Option(
        None, "--scope",
        help="e.g. repo:regin; omitted applies the scope policy, 'all' opts out"),
    json_out: bool = typer.Option(
        False, "--json",
        help="Emit the hits as JSON (subsystem, hit_count, and a hits array of "
             "id/kind/title/scope) for an orchestrator to consume "
             "programmatically, instead of the text block"),
) -> None:
    """Structure-first, task-scoped recall for a spawner to bake into a
    sub-task prompt.

    Resolves a subsystem topic node, pulls that subtree's memories via
    `_task_subtree_memories` (structure-first, not query similarity), prints a
    <recalled_experience> block to stdout (or JSON with `--json`), and emits a
    `memory.recall.task` span so `regin gate task-recall-ran` can prove it
    fired. The binding constraint is topic-link coverage (`regin memory
    link-topics`): a subsystem node with no linked memories returns nothing.
    """
    import lib.memory as memory
    from lib.hook_plugin import post_span
    from lib.settings import settings

    store = memory.get_store()
    graph = _merged_graph()
    scope = _tool_scope(scope)
    node_id = _resolve_subsystem_node(graph, subsystem, task,
                                      settings.project_root)
    mems = _task_subtree_memories(store, graph, node_id, top_k, scope)
    hits = _hit_summaries(mems)

    # Side effects fire in BOTH output modes — never gated on format.
    post_span(trace_id=session, name="memory.recall.task", attributes={
        "task": task[:120], "subsystem": node_id,
        "hit_count": len(mems), "hits": hits,
    })
    _record_task_offered(store, session, mems, task)

    if json_out:
        print(json.dumps(
            {"subsystem": node_id, "hit_count": len(mems), "hits": hits},
            indent=2))
    else:
        # Block (and nothing else) to stdout so it pipes into a prompt.
        print(_render_block(mems))
