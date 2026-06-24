"""Trace-derived gates for *unenforced* skill steps.

regin doesn't own the agent loop, so a skill step it can't enforce (e.g.
`goal-verified-treenav`'s memory-tree-nav recall arm) is honour-system — unless
its tool leaves a fingerprint in the trace. Every MCP/tool call is persisted as
a `session_spans` row, so "did the step run?" collapses to "did its spans appear
for this session?" — a cheap, scriptable check the `regin gate` CLI exposes.

This module is the single source of truth for those span fingerprints, so the
patterns live in code (typed, testable) instead of copy-pasted SQL inside a
`SKILL.md` heredoc. Add a gate by appending a `SpanGate` to `GATES`.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SpanGate:
    """One named "did this tool run?" check, expressed as span-name matchers.

    `like` holds SQL `LIKE` patterns and `exact` holds literal span names; a
    span matches the gate if it matches *any* entry in either. `describe` is the
    human label shown in CLI help and the failure message.
    """

    key: str
    like: tuple[str, ...] = ()
    exact: tuple[str, ...] = ()
    describe: str = ""


# The memory MCP server emits one span per index_root / index_expand /
# index_fetch (tool.mcp__memory__index_*) and one per recall
# (tool.mcp__memory__recall) — see lib/memory/mcp_server.py.
RECALL_ARM = SpanGate(
    key="recall-ran",
    like=("tool.mcp__memory__index_%",),
    exact=("tool.mcp__memory__recall",),
    describe="memory-tree-nav / recall arm (goal-verified-treenav step 1b)",
)

# `regin memory recall-for-task` emits one `memory.recall.task` span per
# spawner-baked, task-scoped recall — see cli/commands/memory.py. v0 gates at
# the session level ("did any task-scoped recall fire this session"); per-stage
# correlation is a later refinement.
TASK_RECALL = SpanGate(
    key="task-recall-ran",
    exact=("memory.recall.task",),
    describe="task-scoped recall fired this session (spawner-baked recall)",
)

GATES: dict[str, SpanGate] = {g.key: g for g in (RECALL_ARM, TASK_RECALL)}


def span_count(trace_id: str, gate: SpanGate) -> int:
    """Count `session_spans` for `trace_id` whose name matches `gate`.

    Routes through the ORM (`SessionLocal`) rather than the raw-sqlite trace
    readers in `queries.py`: this is a trivial aggregate, not a paginated read.
    Returns 0 for a gate with no matchers (never matches everything by accident).
    """
    from sqlalchemy import func, or_
    from sqlmodel import select

    from lib.orm import SessionLocal
    from lib.orm.models.trace import SessionSpan

    conds = [SessionSpan.name.like(p) for p in gate.like]
    conds += [SessionSpan.name == n for n in gate.exact]
    if not conds:
        return 0

    stmt = (
        select(func.count(SessionSpan.id))
        .where(SessionSpan.trace_id == trace_id)
        .where(or_(*conds))
    )
    with SessionLocal() as session:
        return int(session.exec(stmt).one())
