"""The feature ledger — one report, every figure stating its own gaps.

Ordering is the decision here: **the stage line leads, cost second.** The stage
frames every number under it — `$31.80` means something different on a `spec`
than on a `shipped` — and a reader who meets money first has to scroll back to
interpret it.

The session list is printed, not merely counted, because it is the *evidence*
for an upper-bound figure: "$412 across 9 sessions" gives a reader no way to
check which sessions. Identity reuses `regin stats sessions`' convention
verbatim (`date · trace_id[:8] · title`) so an id carries by eye between the
two surfaces.

Unlike `regin feature stage`, this exits 0 when the tracker cannot answer and
prints `unknown` where the stage would be. Its substance is local and correct;
withholding it because one field could not be fetched would trade a right
answer for no answer. And `unknown` is a render-only string — never a fifth
stage name, because it is a statement about us, not about the feature.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from lib.defects import escapes as escapes_module
from lib.defects import stage as stage_module
from lib.defects import store, tracker
from lib.features import attribution, cost as cost_module
from lib.tasks.integrations import IntegrationError

#: What a figure reads when regin could not look. Never a stage name.
UNKNOWN = "unknown"
TRACE_PREFIX = 8


@dataclass(frozen=True)
class SessionRow:
    """One line of the evidence list."""

    trace_id: str
    date: str
    title: str
    cost_usd: Optional[float]
    provenance: str
    round: Optional[int]
    is_primary: int
    #: The task whose link put this session here, carried through from
    #: `AttributedSession`. Only a `task-link` row has one — it is what the
    #: provenance tag links to, and a tag naming no task is a provenance
    #: nobody can audit.
    task_id: Optional[int] = None

    @property
    def short_trace(self) -> str:
        return self.trace_id[:TRACE_PREFIX]

    def to_dict(self) -> dict:
        return {"trace_id": self.trace_id, "short_trace": self.short_trace,
                "date": self.date, "title": self.title,
                "cost_usd": self.cost_usd, "provenance": self.provenance,
                "round": self.round, "is_primary": bool(self.is_primary),
                "task_id": self.task_id}


@dataclass(frozen=True)
class Ledger:
    """Everything `regin feature ledger` knows about one feature."""

    key: str
    stage: Optional[stage_module.Stage]
    cost: cost_module.FeatureCost
    work_time: cost_module.WorkTime
    tokens: dict
    rows: list
    traceless_runs: int
    open_defects: int
    total_defects: int
    escapes: escapes_module.EscapeCounts
    coverage: cost_module.Coverage
    ambiguous: Optional[str] = None

    def to_dict(self) -> dict:
        """Nested, unlike `Stage.to_dict()`: each figure carries its own gap
        counter, and flattened those read as noise — a reader cannot tell which
        number `no_usage_captured` qualifies."""
        return {
            "key": self.key,
            "stage": self.stage.to_dict() if self.stage else None,
            "cost": self.cost.to_dict(),
            "work_time": self.work_time.to_dict(),
            "tokens": dict(self.tokens),
            "sessions": {
                "rows": [row.to_dict() for row in self.rows],
                "gaps": {"traceless_runs": self.traceless_runs},
            },
            "defects": {
                "open": self.open_defects, "total": self.total_defects,
                "escapes": self.escapes.escapes,
                "gaps": {
                    "state_unknown": self.escapes.state_unknown,
                    "unknown_vocabulary": self.escapes.unknown_vocabulary,
                },
            },
            "coverage": self.coverage.to_dict(),
        }


def _stage_or_none(key: str) -> Optional[stage_module.Stage]:
    try:
        return stage_module.stage_for_key(key)
    except (tracker.TrackerUnavailable, IntegrationError):
        return None


def build(feature_key: str, *, stage_resolver=_stage_or_none) -> Ledger:
    """Assemble the ledger. Never raises when the tracker cannot answer.

    `stage_resolver` is how a *web* read stays local: the default goes to the
    tracker, because a CLI report deciding an escape from a stale snapshot
    would rewrite its own numbers, and the web passes
    `lib.features.stage_source.from_cache` instead. A parameter rather than a
    convention, so "the ledger endpoint never calls `fetch_issue`" is
    something a test can name.
    """
    key = (feature_key or "").strip()
    ambiguous = None
    try:
        attributed = attribution.sessions_for_feature(key)
    except attribution.AmbiguousTaskLink as exc:
        attributed, ambiguous = [], str(exc)

    facts = cost_module.facts_for(row.trace_id for row in attributed)
    cost, work_time, tokens = cost_module.roll_up(attributed, facts)
    rows = sorted(
        (SessionRow(trace_id=row.trace_id,
                    date=(facts[row.trace_id].started_at or "")[:10],
                    title=facts[row.trace_id].title or "",
                    cost_usd=(facts[row.trace_id].cost_usd
                              if row.is_primary else 0.0),
                    provenance=row.provenance, round=row.round,
                    is_primary=row.is_primary, task_id=row.task_id)
         for row in attributed),
        key=lambda r: (r.date, r.trace_id))

    defects = store.for_feature(key)
    return Ledger(
        key=key,
        stage=stage_resolver(key),
        cost=cost,
        work_time=work_time,
        tokens=tokens,
        rows=rows,
        traceless_runs=attribution.traceless_runs(key),
        open_defects=sum(1 for d in defects if d.status == "open"),
        total_defects=len(defects),
        escapes=escapes_module.counts_for(key),
        coverage=cost_module.coverage(),
        ambiguous=ambiguous,
    )


# ── Rendering ─────────────────────────────────────────────────

def _money(value: Optional[float]) -> str:
    return "none declared" if value is None else f"${value:,.2f}"


def _duration(ms: int) -> str:
    if ms <= 0:
        return "—"
    minutes, seconds = divmod(round(ms / 1000), 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h {minutes:02d}m" if hours else f"{minutes}m {seconds:02d}s"


def _plural(count: int, noun: str) -> str:
    return f"{count} {noun}" if count == 1 else f"{count} {noun}s"


def _percent(part: float, whole: float) -> str:
    return "—" if not whole else f"{100 * part / whole:.1f}%"


def _gap_line(gaps: dict) -> str:
    named = " · ".join(f"{name.replace('_', '-')} {count}"
                       for name, count in gaps.items() if count)
    return f"    gaps: {named}" if named else ""


def render(report: Ledger) -> list[str]:
    """The human report, stage line first."""
    lines = []
    if report.stage:
        lines.append(f"{report.key}  {report.stage.name}  "
                     f"rounds={report.stage.rounds}  "
                     f"open_defects={report.stage.open_defects}  "
                     f"escapes={report.stage.escapes}")
        lines.append(f"  {report.stage.blurb}")
    else:
        lines.append(f"{report.key}  stage {UNKNOWN}  escapes {UNKNOWN} — "
                     "no tracker answered — it is unreachable, or holds "
                     "no such issue")
    if report.ambiguous:
        lines.append(f"  refused: {report.ambiguous}")

    lines.append("")
    suffix = (f" ({report.cost.recalled} recalled)"
              if report.cost.recalled else "")
    if report.cost.usd is None:
        lines.append(f"cost (upper bound)  {_money(None)}")
    else:
        lines.append(f"cost (upper bound)  {_money(report.cost.usd)} across "
                     f"{_plural(report.cost.sessions, 'session')}{suffix}")
    if report.cost.shared:
        lines.append(f"    {_plural(report.cost.shared, 'shared session')}, "
                     "cost charged elsewhere")
    lines.extend(filter(None, [_gap_line(report.cost.gaps)]))

    lines.append(f"work time  {_duration(report.work_time.active_work_ms)}")
    lines.extend(filter(None, [_gap_line(report.work_time.gaps)]))
    lines.append("tokens  " + " · ".join(
        f"{name.replace('_', '-')} {value:,}"
        for name, value in report.tokens.items()))

    lines.append("")
    lines.append("sessions")
    for row in report.rows:
        lines.append(
            f"  {row.date:<10}  {row.short_trace}  {row.title[:44]:<44}  "
            f"{_money(row.cost_usd):>10}  {row.provenance:<9}  "
            f"round {row.round if row.round is not None else '—'}")
    if not report.rows:
        lines.append("  none declared")
    lines.extend(filter(None,
                        [_gap_line({"traceless_runs": report.traceless_runs})]))

    lines.append("")
    lines.append(f"defects  {report.open_defects} open / "
                 f"{report.total_defects} total      "
                 f"escapes {report.escapes.escapes}")
    lines.extend(filter(None, [_gap_line({
        "state_unknown": report.escapes.state_unknown,
        "unknown_vocabulary": report.escapes.unknown_vocabulary})]))

    cov = report.coverage
    lines.append("")
    lines.append(
        f"coverage  {cov.declared_sessions} of "
        f"{_plural(cov.all_sessions, 'session')} "
        f"({_percent(cov.declared_sessions, cov.all_sessions)}) · "
        f"{_money(cov.declared_usd)} of {_money(cov.all_usd)} "
        f"({_percent(cov.declared_usd, cov.all_usd)})")
    return lines
