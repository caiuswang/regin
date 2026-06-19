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

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("memory")


def _format_hit(hit) -> str:
    m = hit.memory
    title = f" — {m['title']}" if m.get("title") else ""
    src = f" (from session {m['source_trace_id']})" if m.get("source_trace_id") else ""
    return (f"[{m['kind']}|{m['scope']}|score {hit.score:.2f}]"
            f"{title}\n{m['body']}{src}")


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


if __name__ == "__main__":
    mcp.run()
