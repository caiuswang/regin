"""`recall` — the on-demand memory MCP server.

A stdio MCP server exposing the memory tools for deeper mid-task pulls beyond
the few memories the UserPromptSubmit hook auto-injects. Unlike
`send_to_user`'s deliberately regin-blind server, this one *must* read
the memory DB — so regin imports happen lazily inside the tool call,
keeping server startup instant and shielding tool listing from a DB
hiccup.

Every tool here is a thin delegate: the text they return is rendered by
`lib/memory/tree_nav.py`, which `regin memory recall|read|index-*` also calls.
The docstrings, not the bodies, are this file's payload — they are the tool
schemas the harness shows the model. Harnesses without MCP run the same walk
through the CLI.

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

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("memory")


def _nav():
    """The shared renderer, imported per call.

    Deferred because this file is *run as a script* (see the plugin's
    `.mcp.json`), so its module is `__main__` and no parent package has been
    imported yet: a module-scope `from lib.memory import tree_nav` would run
    `lib/memory/__init__.py` — the store and reflect layers — before the server
    can list a single tool. Inside the call it costs a dict lookup.
    """
    from lib.memory import tree_nav
    return tree_nav


@mcp.tool()
def recall(query: str, top_k: int = 5, scope: str = "",
           reinforce: bool = True, brief: bool = True) -> str:
    """Recall experience from regin's cross-session agent memory.

    Use mid-task when past sessions may have hit the same problem:
    before debugging something that feels familiar, before re-deciding
    an architectural question, or when the auto-injected
    <recalled_experience> block hints there is more. Complements (does
    not replace) repo docs — memories are distilled session experience.

    This is the *semantic* leg of memory retrieval: it ranks across the
    whole store regardless of where a lesson is filed, so it is what finds
    cross-cutting lessons the topic-tree walk structurally cannot reach.
    Walk the tree (`.regin/memory/tree/`) to survey a subsystem; `recall`
    to sweep a concept.

    Args:
        query: What you want experience about. Keyword-style works best
            ("playwright stale backend", "schema drift alembic").
        top_k: Max memories to return (default 5).
        scope: Optional repo scope filter like "repo:regin"; empty
            searches every scope.
        reinforce: Whether this pull counts as usage. A genuine pull
            (default) bumps the hit's recall_count/last_recalled — the
            usefulness signal that feeds quality ranking and the forget
            rule. Pass False for AUDIT / curation / eval sweeps (e.g.
            surveying what's stored, scoring recall quality) so the
            measurement never inflates the very signal it measures.
        brief: Return each hit's lead plus an index of what was withheld
            (default), instead of whole bodies. Pull the rest of one hit
            with `memory_read`. Pass False for eval/curation sweeps that
            must score the full text.

    Returns:
        Matching memories (best first) with kind, scope, score and memory id —
        or a note that nothing matched. The originating session id is carried
        only when `brief=False`; in brief mode read it back with `memory_read`.
    """
    return _nav().render_recall(query, top_k=top_k, scope=scope,
                                reinforce=reinforce, brief=brief)


@mcp.tool()
def memory_read(memory_id: str, part: str = "") -> str:
    """Read one memory in full — the follow-up to a `recall` hit whose lead
    ended in a `⋯ +N chars` index.

    Args:
        memory_id: The id from a `recall` hit or an exported tree filename
            (a unique prefix is enough).
        part: Optional section name from the hit's part index, matched
            case-insensitively by name then prefix (`"how"` finds
            `**How to apply:**`). Most memories are flat prose with no
            named parts; omit this for them.

    Returns:
        The full body, or just the named part when `part` is given.
    """
    return _nav().render_memory_read(memory_id, part=part)


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
    return _nav().render_index_root(scope)


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
    return _nav().render_index_expand(node_id, scope)


@mcp.tool()
def index_fetch(node_id: str, top_k: int = 10, scope: str = "",
                reinforce: bool = True) -> str:
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
        reinforce: Whether this fetch counts as wiki usage. A genuine
            navigation pull (default) bumps the wiki's exposure counter;
            pass False for AUDIT / curation / eval sweeps so surveying the
            tree never inflates the signal (mirrors `recall`'s reinforce).

    Returns:
        `## wiki`, `## source refs`, `## memories` sections of pointers.
    """
    return _nav().render_index_fetch(node_id, top_k=top_k, scope=scope,
                                     reinforce=reinforce)


@mcp.tool()
def gate(name: str, session_id: str) -> str:
    """PASS/FAIL a trace-derived span gate for a session (the MCP-native form
    of `regin gate <name> --session <id>`).

    A gate turns an *unenforced* skill step into a checkable invariant: the
    step's tool leaves spans, and the gate asserts they exist for the session.
    `recall-ran` is goal-verified-treenav's anti-skip (did the memory-tree-nav
    recall arm fire?); `task-recall-ran` is goal-verified's.

    Gates here assert a *tool left a fingerprint*, so only add one when the
    tool is guaranteed present. A `ui-verified` gate was removed for failing
    that test: it counted Playwright MCP spans, and in a session without that
    MCP it read identically to a genuine skip — unpassable, and the only way
    past it was to argue around a red gate. Verify UI with the Playwright
    suite or `scripts/dom-measure.mjs --overflow` instead.

    `session_id` is REQUIRED and must be the *caller's* session id (read it with
    `regin session-id`, or from `$CLAUDE_CODE_SESSION_ID` under Claude Code).
    This server is shared and long-lived, so its own environment holds the
    session id of whichever session first spawned it — not the caller's — which
    is why the gate cannot infer the session itself.

    Args:
        name: Gate key, e.g. "recall-ran" or "task-recall-ran".
        session_id: The caller's session/trace id.

    Returns:
        "<gate description> spans this session: N" plus a PASS/FAIL verdict
        line, mirroring the `regin gate` CLI.
    """
    from lib.trace.span_gates import (GATES, PASS, span_count,
                                      unresolved_session_id, verdict)

    spec = GATES.get(name)
    if spec is None:
        return f"unknown gate {name!r} — valid gates: {', '.join(sorted(GATES))}"
    unusable = unresolved_session_id(session_id)
    if unusable:
        return (
            f"session_id is unusable: {unusable}. Pass the id `regin "
            "session-id` prints. This shared memory server cannot infer the "
            "caller's session (its own environment holds the spawner session's "
            "id, not yours). This is NOT a gate failure — nothing was counted."
        )

    n = span_count(session_id, spec)
    # Reaching this function proves the memory MCP server is loaded in the
    # caller's session, and one FastMCP instance serves `gate` alongside
    # `recall` / `index_*` — so the recall arm's tools were demonstrably
    # available here. That makes 0 spans a genuine skip rather than an absent
    # instrument, and this path can always say so. Gates whose capability is
    # something else entirely (a different MCP server) would not inherit this
    # proof and must not be assumed available just because they were called
    # from here.
    capability_proven = spec.capability_self_evident or spec.served_by_memory_mcp
    status, message = verdict(spec, n, capability_proven)

    from lib.activity_log import get_activity_logger
    get_activity_logger("gate").read(
        "gate_checked", gate=name, session=session_id, spans=n,
        passed=status == PASS, status=status)

    return f"{spec.describe} spans this session: {n}\n{message}"


if __name__ == "__main__":
    mcp.run()
