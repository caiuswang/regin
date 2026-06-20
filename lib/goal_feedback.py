"""Goal feedback — close the loop from a verified build back into memory.

The back half of the loop-engineering workflow (Slice 2). After a
`/goal-verified` run finishes, this records two things into the existing
memory store:

1. **Engagement, with a clean signal.** A lesson recalled into the
   roadmap (`goal preflight --with-lessons`) either *made it into the
   approved acceptance checklist* or it didn't. That inclusion is a far
   higher-precision "did this memory help" verdict than the trace-referent
   heuristic in `lib.memory.feedback.score_injection_usefulness` — there is
   no guessing whether a file the memory mentioned later appeared in a
   span; the human approved (or dropped) the lesson explicitly. Included
   lessons are reinforced via the store's own `reinforce`; dropped ones are
   left untouched so reflect's `_decay_chronically_ignored` handles decay —
   exactly the division of labour the existing feedback loop already uses.

2. **New lessons from failures.** Each acceptance item that FAILED in
   verification is a transferable rule the next session should know. It is
   written as a `lesson` memory (the same kind `send_to_user(type=lesson)`
   produces), tagged by area so the next roadmap recalls it. The skill
   phrases failures as rules (not episodes), so this stays LLM-free without
   reproducing the "diary entry" failure mode of heuristic distillation.

This module only *reuses* memory primitives (`remember`, `reinforce`); it
adds no store, table, or index of its own.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# Tag stamped on every failure-derived lesson, so they are filterable and
# the loop's own output is auditable.
FAIL_TAG = "goal-verified-fail"


@dataclass
class OutcomeResult:
    """Summary of what the feedback pass changed in the store."""

    reinforced: list[str] = field(default_factory=list)
    ignored: list[str] = field(default_factory=list)
    new_lessons: list[str] = field(default_factory=list)
    disabled: bool = False


def record_outcome(goal: str, *,
                   included_ids: list[str] | None = None,
                   offered_ids: list[str] | None = None,
                   failures: list[str] | None = None,
                   tags: list[str] | None = None,
                   trace_id: str | None = None,
                   importance: float = 0.6,
                   is_test: bool = False) -> OutcomeResult:
    """Fold one `/goal-verified` outcome back into memory.

    `included_ids` — recalled lessons the agent folded into the approved
    roadmap (the engaged set). `offered_ids` — every lesson preflight
    surfaced; any offered-but-not-included id is recorded as ignored (no
    penalty here — decay is reflect's job). `failures` — acceptance items
    that failed verification, each phrased as a transferable rule; written
    as new lessons tagged by area + FAIL_TAG. Returns what changed.
    """
    included = _dedup(included_ids or [])
    offered = _dedup(offered_ids or [])
    fails = [f.strip() for f in (failures or []) if f.strip()]

    result = OutcomeResult()

    import lib.memory as memory
    if not memory.enabled():
        result.disabled = True
        return result

    result.reinforced = _reinforce_all(memory.get_store(), included)
    result.ignored = [mid for mid in offered if mid not in set(included)]
    result.new_lessons = _write_failures(
        memory, fails, tags=list(tags or []), importance=importance,
        trace_id=trace_id, is_test=is_test)
    return result


def _reinforce_all(store, ids: list[str]) -> list[str]:
    for mid in ids:
        store.reinforce(mid)
    return list(ids)


def _write_failures(memory, fails: list[str], *, tags: list[str],
                    importance: float, trace_id: str | None,
                    is_test: bool) -> list[str]:
    lesson_tags = _dedup(tags + [FAIL_TAG])
    return [
        memory.remember(fail, kind="lesson", tags=lesson_tags,
                        importance=importance, source_trace_id=trace_id,
                        is_test=is_test)
        for fail in fails
    ]


def _dedup(seq: list[str]) -> list[str]:
    return list(dict.fromkeys(seq))


def render_summary(result: OutcomeResult) -> str:
    """Human-readable one-block summary for the CLI."""
    if result.disabled:
        return "memory is disabled — nothing recorded."
    lines = [
        f"reinforced {len(result.reinforced)} lesson(s) folded into the roadmap",
        f"left {len(result.ignored)} offered-but-unused lesson(s) to decay",
        f"wrote {len(result.new_lessons)} new lesson(s) from failures",
    ]
    if result.new_lessons:
        lines.append("  new lesson ids: " + ", ".join(result.new_lessons))
    return "\n".join(lines)


def outcome_to_dict(result: OutcomeResult) -> dict:
    return {
        "reinforced": result.reinforced,
        "ignored": result.ignored,
        "new_lessons": result.new_lessons,
        "disabled": result.disabled,
    }
