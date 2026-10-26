"""Subagent lineage as Claude Code reported it at spawn time.

The regin-lineage mod hooks `agent.spawn` and pipes the started agent's
`(agentId, parentAgentId, tool_use_id, subagentType)` to `regin trace spawn`,
which lands here as one `agent.spawn` row per agent. `claude_subagents` prefers
these records over inferring the parent from `agent-*.meta.json`.

Every attribute key is `spawn_`-prefixed: readers look spans up by `agent_id`
(the span belongs to that agent) and by `launch_tool_use_id` (the marker of the
agent a call launched) without filtering on the span name, and this row is
neither.
"""

from __future__ import annotations

import json

from pydantic import BaseModel, ConfigDict, Field

AGENT_SPAWN_SPAN = 'agent.spawn'


class SpawnRecord(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    agent_id: str = Field(alias='agentId', min_length=1)
    parent_agent_id: str | None = Field(default=None, alias='parentAgentId')
    tool_use_id: str | None = None
    subagent_type: str | None = Field(default=None, alias='subagentType')


def spawn_span_id(agent_id: str) -> str:
    return f'agentspawn-{agent_id}'


def record_spawn(trace_id: str, record: SpawnRecord) -> bool:
    """POST the record as an `agent.spawn` row keyed by the agent id, so a
    re-delivered spawn lands on the same span. True iff ingest accepted it."""
    from lib.hook_plugin import post_span
    return post_span(
        trace_id=trace_id,
        name=AGENT_SPAWN_SPAN,
        attributes={
            'spawn_agent_id': record.agent_id,
            'spawn_parent_agent_id': record.parent_agent_id,
            'spawn_tool_use_id': record.tool_use_id,
            'spawn_subagent_type': record.subagent_type,
        },
        span_id=spawn_span_id(record.agent_id),
    )


def spawn_parents(conn, trace_id: str) -> dict[str, str | None]:
    """`{agent_id: parent_agent_id}` from the session's spawn records; a
    `None` parent means the agent was spawned by the main loop."""
    rows = conn.execute(
        "SELECT attributes FROM session_spans WHERE trace_id = ? AND name = ?",
        (trace_id, AGENT_SPAWN_SPAN),
    ).fetchall()
    out: dict[str, str | None] = {}
    for (raw,) in rows:
        try:
            attrs = json.loads(raw or '{}')
        except ValueError:
            continue
        agent_id = attrs.get('spawn_agent_id')
        if not isinstance(agent_id, str) or not agent_id:
            continue
        parent = attrs.get('spawn_parent_agent_id')
        out[agent_id] = parent if isinstance(parent, str) and parent else None
    return out
