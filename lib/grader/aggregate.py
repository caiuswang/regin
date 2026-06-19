"""Slice 3 of the grade→memory loop — cross-session failure-mode reflection.

Single-session distillation (Slice 1/2) captures *this* mistake; it can't
see that the same weakness recurs. This pass sweeps recent failing grades,
buckets each session's problems into stable mode keys
(`lib.grader.failure_modes`), and when a mode recurs across at least
`grader.aggregate_min_sessions` distinct sessions it consolidates one
high-signal lesson — the rule plus its remediation — into agent memory.

Idempotent by design: each mode owns one memory tagged
`grade-aggregate:<mode>`; a re-run refreshes that row's count/examples
rather than stacking duplicates. Consolidated lessons land `proposed`
(human-gated) — a recurring *pattern* is a stronger signal than a single
session, but still a proposal, not a silent write.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from lib.activity_log import get_activity_logger
from lib.grader import store
from lib.grader.failure_modes import label_for, remediation_for, session_modes

log = get_activity_logger("grader")

_AGG_TAG = "grade-aggregate"
_MAX_EXAMPLES = 3


@dataclass
class AggregateResult:
    """Outcome of one aggregation sweep."""

    trace_count: int = 0          # failing sessions scanned
    recurring: int = 0            # modes at/above the recurrence threshold
    created: int = 0              # new consolidated lessons written
    refreshed: int = 0            # existing lessons updated in place
    modes: list[dict] = field(default_factory=list)   # per-mode summary


def _collect_modes(trace_ids: list[str]) -> dict[str, dict]:
    """`mode_key -> {sessions: set, examples: [str]}` over the pool."""
    acc: dict[str, dict] = {}
    for trace_id in trace_ids:
        grades = store.latest_grades(trace_id, with_detail=True)
        for mode, example in session_modes(grades).items():
            slot = acc.setdefault(mode, {"sessions": set(), "examples": []})
            slot["sessions"].add(trace_id)
            if example and len(slot["examples"]) < _MAX_EXAMPLES:
                slot["examples"].append(example)
    return acc


def _lesson(mode: str, slot: dict) -> tuple[str, str]:
    """(title, body) for the consolidated lesson — the rule, its fix, and a
    few real examples, abstracted away from any single session."""
    n = len(slot["sessions"])
    label = label_for(mode)
    title = f"Recurring weakness: {label} ({n} sessions)"[:120]
    examples = "; ".join(f"«{e}»" for e in slot["examples"]) or "—"
    fix = remediation_for(mode)
    body = (f"The grader has flagged «{label}» [{mode}] across {n} distinct "
            f"sessions — a recurring weakness, not a one-off. "
            + (f"{fix} " if fix else "")
            + f"Recent examples: {examples}")[:1900]
    return title, body


def _importance(n: int, min_sessions: int) -> float:
    """More distinct sessions → more important; capped below the
    auto-approve bar so consolidated lessons still route through review."""
    return min(0.8, 0.55 + 0.05 * (n - min_sessions))


def _existing_for_mode(store_mem, mode: str) -> dict | None:
    """The live (non-retired) consolidated memory for this mode, if any."""
    for mem in store_mem.list_memories(kind="lesson", limit=500):
        tags = mem.get("tags") or []
        if _AGG_TAG in tags and mode in tags and mem.get("status") != "retired":
            return mem
    return None


def _persist_mode(store_mem, mode: str, slot: dict, min_sessions: int,
                  result: AggregateResult) -> None:
    from lib.memory.models import MemoryInput

    title, body = _lesson(mode, slot)
    importance = _importance(len(slot["sessions"]), min_sessions)
    existing = _existing_for_mode(store_mem, mode)
    if existing is not None:
        store_mem.update(existing["id"], title=title, body=body,
                         importance=importance)
        result.refreshed += 1
        mid = existing["id"]
    else:
        mid = store_mem.remember(MemoryInput(
            body=body, title=title, kind="lesson",
            tags=[_AGG_TAG, mode], importance=importance,
            status="proposed", scope="global"))
        result.created += 1
    result.modes.append({"mode": mode, "sessions": len(slot["sessions"]),
                         "memory_id": mid})


def aggregate_failure_modes(*, limit_sessions: int = 200,
                            min_sessions: int | None = None,
                            persist: bool = True) -> AggregateResult:
    """Sweep recent failing grades and consolidate recurring failure modes
    into agent memory. No-op (empty result) when agent memory is disabled.
    `persist=False` reports what would be written without touching the
    store."""
    from lib.settings import settings
    import lib.memory as memory

    if min_sessions is None:
        min_sessions = settings.grader.aggregate_min_sessions
    result = AggregateResult()
    if not memory.enabled():
        log.write("aggregate_skipped_memory_disabled")
        return result

    trace_ids = store.recent_failing_trace_ids(limit=limit_sessions)
    result.trace_count = len(trace_ids)
    modes = _collect_modes(trace_ids)
    store_mem = memory.get_store()
    for mode, slot in modes.items():
        if len(slot["sessions"]) < min_sessions:
            continue
        result.recurring += 1
        if persist:
            _persist_mode(store_mem, mode, slot, min_sessions, result)
        else:
            result.modes.append({"mode": mode,
                                  "sessions": len(slot["sessions"]),
                                  "memory_id": None})
    log.write("failure_modes_aggregated", traces=result.trace_count,
              recurring=result.recurring, created=result.created,
              refreshed=result.refreshed)
    return result


__all__ = ["aggregate_failure_modes", "AggregateResult"]
