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

Every figure is local, including the stage: it is the last `feature_events`
row plus a count of escapes, so there is no field this report can fail to
fetch and no `unknown` to render. That word used to be here precisely because
the stage came from a tracker the CLI might not reach.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from lib.defects import escapes as escapes_module
from lib.defects import store
from lib.features import attribution, cost as cost_module
from lib.features import events as events_module
from lib.features import store as feature_store

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
    #: The task whose run put this session here, carried through from
    #: `AttributedSession`. Only a `run` row has one — it is what the
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

    id: int
    title: str
    #: The spec the feature was declared with. Served beside the title rather
    #: than folded into the stage: it is what the acceptance list is checked
    #: against, and an edit to it appends no event.
    body: Optional[str]
    origin: str
    external_ref: Optional[str]
    external_url: Optional[str]
    sync_error: Optional[str]
    #: The registered repo the feature is filed under, plus the name to draw.
    #: The name rides along because a client that had to resolve it would hold
    #: a second copy of the repo list to render one chip.
    project_id: Optional[int]
    project_name: Optional[str]
    stage: str
    rounds: int
    claimed_at: Optional[str]
    cost: cost_module.FeatureCost
    work_time: cost_module.WorkTime
    #: Invariant 7 — the one figure the pipeline is judged on.
    human_prompts: cost_module.HumanPrompts
    tokens: dict
    rows: list
    traceless_runs: int
    open_defects: int
    total_defects: int
    escapes: escapes_module.EscapeCounts
    coverage: cost_module.Coverage

    def to_dict(self) -> dict:
        """Nested, unlike `Stage.to_dict()`: each figure carries its own gap
        counter, and flattened those read as noise — a reader cannot tell which
        number `no_usage_captured` qualifies."""
        return {
            "id": self.id,
            "title": self.title,
            "body": self.body,
            "origin": self.origin,
            "external_ref": self.external_ref,
            "external_url": self.external_url,
            "sync_error": self.sync_error,
            "project_id": self.project_id,
            "project_name": self.project_name,
            "stage": {"stage": self.stage,
                      "blurb": events_module.blurb(self.stage),
                      "rounds": self.rounds,
                      "claimed_at": self.claimed_at,
                      "open_defects": self.open_defects,
                      "escapes": self.escapes.escapes},
            "cost": self.cost.to_dict(),
            "work_time": self.work_time.to_dict(),
            "human_prompts": self.human_prompts.to_dict(),
            "tokens": dict(self.tokens),
            "sessions": {
                "rows": [row.to_dict() for row in self.rows],
                "gaps": {"traceless_runs": self.traceless_runs},
            },
            "defects": {
                "open": self.open_defects, "total": self.total_defects,
                "escapes": self.escapes.escapes,
            },
            "coverage": self.coverage.to_dict(),
        }


def build(feature_id: int) -> Ledger:
    """Assemble the ledger. Every figure is a local read.

    The `stage_resolver` parameter this function used to take is gone with the
    reason for it: the CLI passed a live tracker fetch and the web passed a
    cache reader, because deciding an escape from a stale snapshot would have
    rewritten the numbers in the CLI's own report. A stage derived from rows in
    this database has no stale reading to choose between.
    """
    feature = feature_store.require(feature_id)
    attributed = attribution.sessions_for_feature(feature_id)

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

    defects = store.for_feature(feature_id)
    escapes = escapes_module.counts_for(feature_id)
    return Ledger(
        id=feature.id,
        title=feature.title,
        body=feature.body,
        origin=feature.origin,
        external_ref=feature.external_ref,
        external_url=feature.external_url,
        sync_error=feature.sync_error,
        project_id=feature.project_id,
        project_name=feature_store.project_name(feature.project_id),
        stage=events_module.stage(feature_id, escapes=escapes.escapes),
        rounds=events_module.rounds(feature_id),
        claimed_at=events_module.claimed_at(feature_id),
        cost=cost,
        work_time=work_time,
        human_prompts=cost_module.human_prompts(attributed, facts),
        tokens=tokens,
        rows=rows,
        traceless_runs=attribution.traceless_runs(feature_id),
        open_defects=sum(1 for d in defects if d.status == "open"),
        total_defects=len(defects),
        escapes=escapes,
        coverage=cost_module.coverage(),
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
    lines = [f"#{report.id}  {report.title}"]
    ref = f"  {report.external_ref}" if report.external_ref else ""
    lines.append(f"{report.stage}  rounds={report.rounds}  "
                 f"open_defects={report.open_defects}  "
                 f"escapes={report.escapes.escapes}{ref}")
    lines.append(f"  {events_module.blurb(report.stage)}")
    if report.sync_error:
        lines.append(f"  sync: {report.sync_error}")

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
    human = report.human_prompts
    lines.append(
        f"human prompts  {human.prompts} across "
        f"{_plural(human.sessions, 'human session')}")
    if human.run_sessions:
        lines.append(f"    {_plural(human.run_sessions, 'session')} excluded, "
                     "produced by a task run")
    lines.extend(filter(None, [_gap_line(human.gaps)]))
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

    cov = report.coverage
    lines.append("")
    lines.append(
        f"coverage  {cov.declared_sessions} of "
        f"{_plural(cov.all_sessions, 'session')} "
        f"({_percent(cov.declared_sessions, cov.all_sessions)}) · "
        f"{_money(cov.declared_usd)} of {_money(cov.all_usd)} "
        f"({_percent(cov.declared_usd, cov.all_usd)})")
    return lines
