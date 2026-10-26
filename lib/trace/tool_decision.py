"""Who let a tool call through, as Claude Code's `tool.check` reported it.

The regin-permit mod pipes the engine's verdict (`await next(e)`) and the
loop's `e.agentId` to `regin trace decision`, which lands here as one
`permission.decision` row per call. The tool span's own row is written by
other hooks at other times, so the verdict is stamped onto it at read time
(`stamp_tool_decisions`) as `attributes.permission`, joined on `tool_use_id`.

The row names its call `decided_tool_use_id`, never `tool_use_id`: readers
that retire placeholders or pair rows on `tool_use_id` do it without a
span-name filter, and this row is not the call. Its name stays out of `tool.*`
for the same reason — tool counts and cost queries match on that prefix.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from lib.trace.merge_anchors import _attrs

PERMISSION_DECISION_SPAN = 'permission.decision'
_DECIDED_KEY = 'decided_tool_use_id'


class DecisionRecord(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    tool_use_id: str = Field(min_length=1)
    decision: Literal['allow', 'ask', 'deny']
    reason: str | None = None
    rule: str | None = None
    hook: str | None = None
    setting: str | None = None
    decider: str | None = None
    ceiling: str | None = None
    agent_id: str | None = Field(default=None, alias='agentId')

    def permission(self) -> dict:
        return self.model_dump(exclude={'tool_use_id'}, exclude_none=True)


def decision_span_id(tool_use_id: str) -> str:
    return f'tooldecision-{tool_use_id}'


def record_decision(trace_id: str, record: DecisionRecord) -> bool:
    """POST the verdict keyed by its call, so a re-delivered report lands on
    the same row. True iff ingest accepted it."""
    from lib.hook_plugin import post_span
    return post_span(
        trace_id=trace_id,
        name=PERMISSION_DECISION_SPAN,
        attributes={_DECIDED_KEY: record.tool_use_id, 'permission': record.permission()},
        span_id=decision_span_id(record.tool_use_id),
    )


def stamp_tool_decisions(spans: list[dict]) -> list[dict]:
    """Copy each decision row's `permission` onto the tool spans of its call,
    and drop the decision rows: they carry the verdict, they don't render."""
    verdicts = {
        _attrs(s).get(_DECIDED_KEY): _attrs(s).get('permission')
        for s in spans if s.get('name') == PERMISSION_DECISION_SPAN
    }
    if not verdicts:
        return spans
    out = []
    for s in spans:
        if s.get('name') == PERMISSION_DECISION_SPAN:
            continue
        attrs = _attrs(s)
        verdict = verdicts.get(attrs.get('tool_use_id'))
        if verdict and str(s.get('name') or '').startswith('tool.'):
            s = {**s, 'attributes': {**attrs, 'permission': verdict}}
        out.append(s)
    return out
