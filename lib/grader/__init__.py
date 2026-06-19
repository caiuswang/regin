"""Post-hoc rubric grader for captured Claude Code sessions.

Grades a completed session (a regin trace) on two independent,
never-fused axes:

* **correctness** — every assertion the agent made is checked against the
  trace span that should back it (groundedness), the task's required
  items are checked off (coverage), and each grounding source is
  classified authoritative-vs-proxy (source quality).
* **process** — tool-use appropriateness, redundancy/thrash, error
  recovery, and cost-proportionality over regin's token/cost breakdown.

Entry points::

    from lib import grader
    result = grader.grade_session(trace_id, tier="auto")
    grader.latest_grades(trace_id)
    grader.list_grades(limit=50)
    grader.pareto_points()

Internals: evidence index (`evidence`), claim ledger (`extraction`),
criterion engines (`grounding`, `coverage`, `source_quality`, `process`),
axis orchestration (`correctness`, `service`), rubric-as-data (`rubric`),
persistence (`store`), analytics (`pareto`), judge adapter (`adapters`).
"""

from __future__ import annotations


def enabled() -> bool:
    from lib.settings import settings
    return bool(settings.grader.enabled)


def grade_session(trace_id: str, **kwargs) -> dict:
    from lib.grader.service import grade_session as _grade
    return _grade(trace_id, **kwargs)


def latest_grades(trace_id: str, **kwargs) -> dict:
    from lib.grader.store import latest_grades as _latest
    return _latest(trace_id, **kwargs)


def list_grades(**kwargs) -> list[dict]:
    from lib.grader.store import list_grades as _list
    return _list(**kwargs)


def pareto_points(**kwargs) -> dict:
    from lib.grader.pareto import pareto_points as _pareto
    return _pareto(**kwargs)


def aggregate_failure_modes(**kwargs):
    """Consolidate recurring cross-session failure modes into agent memory
    (Slice 3 of the grade→memory loop)."""
    from lib.grader.aggregate import aggregate_failure_modes as _agg
    return _agg(**kwargs)


__all__ = ["enabled", "grade_session", "latest_grades", "list_grades",
           "pareto_points", "aggregate_failure_modes"]
