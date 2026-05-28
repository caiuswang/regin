"""Subagent scenario (10).

`tool_trace_hook.py` (Agent span emitter) is not registered, so we assert on
the PostToolUse jsonl payload for `tool_name == 'Agent'` — same pattern as
the other generic-tool tests.
"""

from __future__ import annotations

import pytest


@pytest.mark.slow
def test_task_agent_fires_post_tool_use(trace_session):
    trace_session.send(
        "use the Task tool with subagent_type=general-purpose to read "
        "sample.txt and summarise it in one sentence",
        idle_timeout=300,
    )

    agent_events = [
        e for e in trace_session.hook_events(event="PostToolUse")
        if (e.get("payload") or {}).get("tool_name") == "Agent"
    ]
    assert agent_events, (
        "expected at least one PostToolUse hook for tool_name=Agent"
    )


@pytest.mark.slow
def test_subagent_tool_spans_nest_under_subagent_start(trace_session):
    """Drive a real Task/Agent call and verify the projected tree nests
    the subagent's internal tool calls under `subagent.start`, not as
    flat siblings of the parent's own tools. This is the end-to-end
    proof of `_graft_orphans` pass 4 + the `agent_id` tagging in
    `post_tool_trace.py`.

    The subagent is asked to do *multiple* file reads so we can assert
    on more than one nested tool span — a single tool call could nest
    by accident.
    """
    trace_session.send(
        "use the Task tool with subagent_type=general-purpose. ask it to "
        "read sample.txt and sample.py and report the line count of each "
        "in one line.",
        idle_timeout=300,
    )

    spans = trace_session.fetch_spans()
    by_id = {s["span_id"]: s for s in spans}

    starts = [s for s in spans if s["name"] == "subagent.start"]
    assert starts, (
        f"no subagent.start span — Claude did not actually invoke the "
        f"Task tool. Span names: {sorted({s['name'] for s in spans})}"
    )
    start = starts[0]
    agent_id = (start.get("attributes") or {}).get("agent_id")
    assert agent_id, (
        f"subagent.start has no agent_id attribute; subagent_lifecycle.py "
        f"did not capture it from the SubagentStart payload. attrs: "
        f"{start.get('attributes')}"
    )

    # The matching subagent.stop must be reparented under the start.
    stops = [s for s in spans if s["name"] == "subagent.stop"]
    assert stops, "no subagent.stop span — subagent never returned"
    matching_stops = [
        s for s in stops
        if (s.get("attributes") or {}).get("agent_id") == agent_id
    ]
    assert matching_stops, (
        f"no subagent.stop span shares agent_id={agent_id} with the start"
    )
    assert matching_stops[0]["parent_id"] == start["span_id"], (
        f"subagent.stop should nest under its subagent.start "
        f"(parent_id={matching_stops[0]['parent_id']!r}, "
        f"expected {start['span_id']!r})"
    )

    # Every tool span carrying this agent_id must reparent onto the start.
    inner_tool_spans = [
        s for s in spans
        if s["name"].startswith("tool.")
        and (s.get("attributes") or {}).get("agent_id") == agent_id
    ]
    assert len(inner_tool_spans) >= 2, (
        f"expected ≥2 subagent-internal tool spans (the Task asked for "
        f"two file reads), got {len(inner_tool_spans)}. Tool span names "
        f"in trace: {[s['name'] for s in spans if s['name'].startswith('tool.')]}"
    )
    misparented = [
        s for s in inner_tool_spans if s["parent_id"] != start["span_id"]
    ]
    assert not misparented, (
        f"{len(misparented)} subagent-internal tool span(s) were NOT "
        f"reparented onto subagent.start by _graft_orphans pass 4. "
        f"Examples: "
        f"{[(s['name'], s['parent_id']) for s in misparented[:3]]}"
    )

    # And the parent's own PostToolUse for the Agent call must stay
    # *outside* the subagent — it has no agent_id, so pass 4 must skip it.
    agent_tool_spans = [
        s for s in spans
        if s["name"] == "tool.Agent"
        and not (s.get("attributes") or {}).get("agent_id")
    ]
    if agent_tool_spans:
        # Should not be parented under subagent.start.
        assert agent_tool_spans[0]["parent_id"] != start["span_id"], (
            "parent's tool.Agent span was incorrectly nested under the "
            "subagent it spawned — it has no agent_id and belongs under "
            "the prompt, as a sibling marker that the Agent call returned"
        )
        # Should sit under a `prompt` ancestor, not orphan or under the subagent.
        anc = by_id.get(agent_tool_spans[0]["parent_id"])
        assert anc is not None and anc["name"] == "prompt", (
            f"tool.Agent's parent should be a prompt span, got {anc and anc['name']!r}"
        )


@pytest.mark.slow
def test_subagent_assistant_response_spans_emitted_and_nested(trace_session):
    """End-to-end proof of the SubagentStop → assistant_response pipeline
    added in commit ba68e48.

    On SubagentStop the handler reads the subagent's own transcript via
    `agent_transcript_path` and emits one `assistant_response` span per
    text turn, tagged with `agent_id` and span_id `resp-sa-<turn_uuid[:13]>`.
    `_graft_orphans` Pass 5 then nests them under the matching
    `subagent.start` so the rendered tree shows the subagent's reasoning
    inline with its tool calls.

    The subagent is asked to produce more than one turn so we exercise
    multi-span emission rather than the single-bubble degenerate case.
    """
    trace_session.send(
        "use the Task tool with subagent_type=general-purpose. ask it to "
        "(1) read sample.txt and report its first line, then (2) read "
        "sample.py and report its line count. Both as a two-line bullet "
        "report.",
        idle_timeout=300,
    )

    spans = trace_session.fetch_spans()
    by_id = {s["span_id"]: s for s in spans}

    starts = [s for s in spans if s["name"] == "subagent.start"]
    assert starts, (
        f"no subagent.start span — Claude did not actually invoke the "
        f"Task tool. Span names: {sorted({s['name'] for s in spans})}"
    )
    start = starts[0]
    agent_id = (start.get("attributes") or {}).get("agent_id")
    assert agent_id, "subagent.start has no agent_id attribute"

    sa_responses = [
        s for s in spans
        if s["name"] == "assistant_response"
        and s["span_id"].startswith("resp-sa-")
        and (s.get("attributes") or {}).get("agent_id") == agent_id
    ]
    assert sa_responses, (
        f"no resp-sa-* spans for agent_id={agent_id}. The SubagentStop "
        f"handler did not emit assistant_response spans for the subagent's "
        f"transcript. assistant_response span_ids in trace: "
        f"{[s['span_id'] for s in spans if s['name'] == 'assistant_response']}"
    )

    # Span_id format: 'resp-sa-' + 13 chars of turn uuid = 21 chars total.
    for s in sa_responses:
        assert len(s["span_id"]) == 21, (
            f"span_id {s['span_id']!r} is not 'resp-sa-' + 13 chars"
        )

    # Required attributes present and shaped correctly.
    for s in sa_responses:
        attrs = s.get("attributes") or {}
        assert isinstance(attrs.get("text"), str) and attrs["text"], (
            f"resp-sa span {s['span_id']} missing/empty text attribute"
        )
        assert attrs.get("turn_uuid"), "missing turn_uuid"
        assert attrs.get("model"), "missing model"
        assert attrs.get("response_chars") == len(attrs["text"]), (
            f"response_chars={attrs.get('response_chars')} != "
            f"len(text)={len(attrs['text'])}"
        )

    # `_graft_orphans` Pass 5 must reparent resp-sa-* under the matching
    # subagent.start. Without this, the dashboard would show subagent
    # turns floating at the top of the conversation.
    misparented = [s for s in sa_responses if s["parent_id"] != start["span_id"]]
    assert not misparented, (
        f"{len(misparented)} resp-sa-* span(s) were NOT reparented onto "
        f"subagent.start by _graft_orphans pass 5. Examples: "
        f"{[(s['span_id'], s['parent_id']) for s in misparented[:3]]}"
    )

    # `tool_calls` correlation: only present when the model bundles text and
    # tool_use into the same logical turn (same `message.id`). Claude does
    # this; kimi-for-coding (and other models) sometimes split them across
    # separate API calls, leaving the text turn with no `tool_calls`. The
    # underlying tool_use ↔ tool_result correlation is exercised
    # deterministically by `tests/test_transcript_usage.py` — here we only
    # validate shape if the model happened to bundle.
    with_tools = [
        s for s in sa_responses
        if (s.get("attributes") or {}).get("tool_calls")
    ]
    for s in with_tools:
        for call in s["attributes"]["tool_calls"]:
            assert "name" in call and "is_error" in call, (
                f"tool_calls entry missing required keys: {call}"
            )
