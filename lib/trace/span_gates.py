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
    # What must be installed for the gated step to be *runnable* at all. A
    # count of 0 only means "you skipped it" once this is known present —
    # otherwise it may just mean the tool was never there, and reporting the
    # two identically is what made the retired `ui-verified` gate unpassable.
    capability: str = ""
    # True when running regin proves the capability (the span comes from a
    # regin CLI command, so anyone who can invoke the gate could have invoked
    # the step). False when the capability is an MCP server that may or may
    # not be loaded in the caller's session.
    capability_self_evident: bool = False
    # True when the gated tools are served by the *memory* MCP server, which
    # also serves `mcp__memory__gate`. Reaching that tool then proves this
    # gate's capability — one FastMCP instance, so if the gate was callable
    # the step's tools were too. Only that path may honour this flag; the CLI
    # cannot see which MCP servers a session loaded.
    served_by_memory_mcp: bool = False


# The memory MCP server emits one span per index_root / index_expand /
# index_fetch (tool.mcp__memory__index_*) and one per recall
# (tool.mcp__memory__recall) — see lib/memory/mcp_server.py.
RECALL_ARM = SpanGate(
    key="recall-ran",
    like=("tool.mcp__memory__index_%",),
    exact=("tool.mcp__memory__recall",),
    describe="memory-tree-nav / recall arm (goal-verified-treenav step 1b)",
    capability="the memory MCP server (the index_*/recall tools)",
    # Not self-evident from the CLI: `regin gate` runs fine in a session that
    # never loaded the memory MCP, and there 0 spans proves nothing. It IS
    # self-evident from `mcp__memory__gate`, because one FastMCP instance
    # serves the gate and the index_*/recall tools alike — reaching the gate
    # means the arm's tools were reachable too. That path passes
    # capability_proven=True explicitly.
    capability_self_evident=False,
    served_by_memory_mcp=True,
)

# `regin memory recall-for-task` emits one `memory.recall.task` span per call
# (see cli/commands/memory.py:cmd_recall_for_task). This gate proves the
# task-scoped recall arm fired — the spawner-baked recall the goal-verified loop
# relies on. v0 is session-level ("did any task-recall happen this session"),
# not per-stage correlation.
TASK_RECALL = SpanGate(
    key="task-recall-ran",
    exact=("memory.recall.task",),
    describe="task-scoped recall (goal-verified recall arm)",
    capability="the regin CLI (`regin memory recall-for-task`)",
    # Self-evident everywhere: the span comes from a regin CLI command, so any
    # caller able to reach this gate could have run the step. 0 spans is a
    # genuine skip, never an absent instrument.
    capability_self_evident=True,
)

# A `ui-verified` gate lived here: it counted Playwright *MCP* browser spans to
# prove a UI goal was rendered rather than asserted from the diff. Removed —
# its premise ("the browser MCP is available in every regin session") turned out
# to be false, and a gate that cannot distinguish "you skipped the render" from
# "the instrument was absent" fails identically in both cases. That is worse
# than no gate: the only way past it is for the agent to talk itself out of a
# red gate, which is a habit that does not stay confined to one gate.
#
# The invariant it stood for now has a real enforcer that needs no MCP: the
# no-horizontal-overflow cases in `frontend/tests/responsive.spec.js` (detectors
# in `tests/helpers/overflow.js`) assert it at mobile/tablet/desktop widths on
# every run, and `scripts/dom-measure.mjs --overflow [--baseline]` reports the
# same measurement interactively. Prefer extending those over reviving a
# tool-presence proxy.

GATES: dict[str, SpanGate] = {
    g.key: g for g in (RECALL_ARM, TASK_RECALL)
}


#: Exit code / status for a gate whose spans are absent but whose capability
#: could not be shown to have been present. Distinct from FAIL on purpose: it
#: means "no evidence either way", and it must never read as PASS.
INCONCLUSIVE = "INCONCLUSIVE"
PASS = "PASS"
FAIL = "FAIL"

STATUS_EXIT = {PASS: 0, FAIL: 1, INCONCLUSIVE: 2}


def verdict(gate: SpanGate, count: int, capability_proven: bool) -> tuple[str, str]:
    """Resolve (status, human message) for a gate result.

    Shared by the `regin gate` CLI and the `mcp__memory__gate` MCP tool so the
    two can't drift into disagreeing about what 0 spans means.

    `capability_proven` answers "do we know the gated step was even runnable
    here?". When it is False and no spans exist, the honest answer is
    INCONCLUSIVE, not FAIL: telling an agent "you skipped the step, go back
    and run it" when the tool was never installed is an instruction it cannot
    follow, and the only way past an unfollowable gate is to argue around a
    red one. That is the failure that retired the `ui-verified` gate.
    """
    if count > 0:
        return PASS, "GATE PASS — arm ran"
    if capability_proven:
        return FAIL, (
            "GATE FAIL — no spans for this gate, and its tools WERE available "
            f"({gate.capability}); the step was skipped. Go back and run it."
        )
    return INCONCLUSIVE, (
        "GATE INCONCLUSIVE — no spans, but this path cannot show that "
        f"{gate.capability} was present, so 0 proves nothing. Re-check from a "
        "context that establishes the capability (for recall-ran, the "
        "`mcp__memory__gate` tool: reaching it proves the arm's tools were "
        "loaded). Do NOT record this as a pass."
    )


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
