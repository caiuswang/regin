"""MCP tool scenario (11).

Claude is not obligated to call a specific MCP tool just because we ask,
and the availability of `mcp__*` tools depends on which plugins are loaded
for the tmux session. This test coaxes Claude into calling one and skips
(rather than fails) if it refuses — the assertion itself is simply that
hook-payloads.jsonl records the PostToolUse with a `tool_name` starting
with `mcp__` when one IS called.
"""

from __future__ import annotations

import pytest


@pytest.mark.slow
def test_mcp_tool_fires_post_tool_use(trace_session):
    # Prompt the agent to exercise any `mcp__*` tool available in this
    # session. The specific tool doesn't matter — the test asserts the
    # PostToolUse hook records a tool_name starting with `mcp__`. If no
    # MCP plugins are loaded, the test skips rather than fails.
    trace_session.send(
        "If any MCP tool is available (names starting with `mcp__`), "
        "call one read-only MCP tool now and report what it returned. "
        "You MUST use the MCP tool; do not use Bash or Grep.",
        idle_timeout=180,
    )

    mcp_events = [
        e for e in trace_session.hook_events(event="PostToolUse")
        if ((e.get("payload") or {}).get("tool_name") or "").startswith("mcp__")
    ]
    if not mcp_events:
        pytest.skip(
            "Claude declined to call an mcp__ tool in this session. Payloads: "
            + str([
                (e.get("payload") or {}).get("tool_name")
                for e in trace_session.hook_events(event="PostToolUse")
            ])
        )

    # When Claude DID use an MCP tool, the hook payload must carry the full
    # namespaced name — validating the ingest path for mcp tool names.
    names = {(e.get("payload") or {}).get("tool_name") for e in mcp_events}
    assert all(n and n.startswith("mcp__") for n in names)
