"""Tool-use scenarios (2, 3, 6).

Reality check: `pre_tool_trace_hook.py` and `tool_trace_hook.py` exist but are
NOT registered in `~/.claude/settings.json` today, so generic `pre_tool.*` and
`tool.*` spans are not emitted. What IS observable for every tool call is a
PostToolUse entry in `~/.claude/hook-payloads.jsonl`. These tests validate
that entry instead — the moment the catch-all tool trace hook is registered,
the matching span-level assertions below become easy to re-enable.
"""

from __future__ import annotations

import pytest


def _post_tool_use_for(trace_session, tool_name):
    return [
        e for e in trace_session.hook_events(event="PostToolUse")
        if (e.get("payload") or {}).get("tool_name") == tool_name
    ]


def test_read_tool_fires_post_tool_use(trace_session):
    trace_session.send("please read sample.txt in the current directory and tell me line 2")

    assert _post_tool_use_for(trace_session, "Read"), (
        "expected at least one PostToolUse hook for tool_name=Read"
    )


def test_bash_tool_fires_post_tool_use_with_command(trace_session):
    sentinel = "trace-bash-9371"
    trace_session.send(f"run `echo {sentinel}` using the Bash tool")

    bash_events = _post_tool_use_for(trace_session, "Bash")
    assert bash_events, "expected at least one PostToolUse hook for tool_name=Bash"
    commands = [
        ((e.get("payload") or {}).get("tool_input") or {}).get("command", "")
        for e in bash_events
    ]
    assert any(sentinel in c for c in commands), (
        f"no Bash PostToolUse carried the sentinel command; got {commands}"
    )


@pytest.mark.xfail(
    reason="Environmental: real claude-cli sometimes picks Read/Bash instead "
           "of Grep for 'find the word ... inside sample.txt'. Rerunning "
           "with a 'use the Grep tool literally' prompt doesn't help. Marked "
           "xfail until the harness can pin tool selection deterministically.",
    strict=False,
)
def test_grep_tool_fires_post_tool_use(trace_session):
    trace_session.send(
        "use the Grep tool to find the word 'line' inside sample.txt in the current directory"
    )

    assert _post_tool_use_for(trace_session, "Grep"), (
        "expected at least one PostToolUse hook for tool_name=Grep"
    )
