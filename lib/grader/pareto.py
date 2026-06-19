"""Cross-axis analytics — the accuracy–cost Pareto framing.

The grader never fuses the two axes into one number; the useful aggregate
is *cost-per-correct-outcome*, computed here at the analytics layer from
stored (correctness verdict, cost) pairs per task class. Off-frontier
sessions are the interesting ones:

* cheaply wrong   — failed/under-verified at below-median cost (the
                    under-verification shortcut);
* expensively right — passed at top-decile cost (paying for an unmanaged
                    context or over-verification).
"""

from __future__ import annotations

from lib.grader.store import list_grades


def _percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    idx = min(int(len(ordered) * fraction), len(ordered) - 1)
    return ordered[idx]


def _point(trace_id: str, axes: dict) -> dict:
    correctness = axes.get("correctness", {})
    process = axes.get("process", {})
    session = correctness.get("session") or process.get("session") or {}
    return {
        "trace_id": trace_id,
        "title": session.get("title"),
        "correctness": correctness.get("verdict"),
        "process": process.get("verdict"),
        "cost_usd": session.get("cost_usd") or 0.0,
        "task_class": (process.get("scoreboard", {})
                       .get("cost", {}).get("task_class")),
    }


def _flag_off_frontier(points: list[dict],
                       median: float | None, p90: float | None) -> None:
    """Flags are computed within one task class — a long interactive
    session is never 'expensively right' merely for outcosting one-shots.
    A session graded on the process axis only (correctness is None) is
    unknown, not wrong."""
    for point in points:
        verdict = point["correctness"]
        passed = verdict == "satisfied"
        cost = point["cost_usd"]
        point["cheaply_wrong"] = bool(
            verdict is not None and not passed
            and median is not None and cost and cost <= median)
        point["expensively_right"] = bool(
            passed and p90 is not None and cost and cost >= p90)


def _summary(points: list[dict]) -> dict:
    satisfied = [p for p in points if p["correctness"] == "satisfied"]
    total_cost = sum(p["cost_usd"] for p in points)
    costs = [p["cost_usd"] for p in points if p["cost_usd"]]
    return {
        "sessions": len(points),
        "satisfied": len(satisfied),
        "total_cost_usd": round(total_cost, 4),
        "cost_per_correct_outcome": (
            round(total_cost / len(satisfied), 4) if satisfied else None),
        "median_cost_usd": _percentile(costs, 0.5),
        "p90_cost_usd": _percentile(costs, 0.9),
    }


def pareto_points(*, limit: int = 200, include_tests: bool = False) -> dict:
    """Per-session (correctness, cost) points with off-frontier flags
    computed per task class, plus per-class and overall summaries."""
    grades = list_grades(limit=limit * 2, include_tests=include_tests)
    by_trace: dict[str, dict] = {}
    for grade in grades:
        by_trace.setdefault(grade["trace_id"], {})[grade["axis"]] = grade
    points = [_point(tid, axes) for tid, axes in by_trace.items()]

    by_class: dict[str, list[dict]] = {}
    for point in points:
        by_class.setdefault(point["task_class"] or "unknown", []).append(point)
    for class_points in by_class.values():
        costs = [p["cost_usd"] for p in class_points if p["cost_usd"]]
        _flag_off_frontier(class_points, _percentile(costs, 0.5),
                           _percentile(costs, 0.9))

    summary = _summary(points)
    summary["by_task_class"] = {
        cls: _summary(class_points)
        for cls, class_points in sorted(by_class.items())}
    return {"points": points, "summary": summary}


__all__ = ["pareto_points"]
