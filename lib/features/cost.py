"""What a feature cost, and what the figure refuses to claim (CAI-234).

**The rule, in one line.** A feature's cost is the total `sessions.cost_usd` of
every session declared against it, read live, stated as an **upper bound** —
because attribution binds a whole session, and a session may have done other
work that day. The data holds no sub-session boundary, so a narrower claim
would be manufactured precision.

Read-through, never snapshotted: `sessions.cost_usd` rises while a session is
alive, so freezing it would need a refresh trigger. Contrast the round on a
`session_features` row, which *is* snapshotted — it names the round the feature
stood at when the claim was made, and a later restart must not retroactively
move it, while cost is regin's own data and locally recomputable.

Dollars are the headline, singular. The four token columns are a breakdown
beneath it and are **never added together**: a live session reads `input=118`,
`output=57783`, `cache_read=6206875`, so any sum is cache reads wearing a work
number's clothes. Context gauges are absent entirely — a max-of-maxes across a
feature's sessions is uninterpretable.

**Every figure carries its own gap counter, adjacent to it**, and a gap is
always a count of sessions rather than a substituted zero. One combined
"incomplete data" block would leave the reader working out which number is
affected.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from sqlmodel import text

from lib.orm import SessionLocal

_TOKEN_COLUMNS = ("input_tokens", "output_tokens", "cache_read_tokens",
                  "cache_creation_tokens")


@dataclass(frozen=True)
class SessionFacts:
    """One `sessions` row, as the roll-up reads it."""

    trace_id: str
    title: Optional[str] = None
    started_at: Optional[str] = None
    cost_usd: Optional[float] = None
    active_work_ms: Optional[int] = None
    tokens: dict = field(default_factory=dict)
    known: bool = True

    @property
    def no_usage_captured(self) -> bool:
        """Spans present, zero `turn_usage` rows — a regin ingestion gap, and
        27% of the live database. Not a legacy band: it runs to today."""
        return self.cost_usd is None and not any(self.tokens.values())

    @property
    def model_unpriced(self) -> bool:
        """Turns ingested, model outside the pricing catalogue. Counted apart
        from the above because only this one is fixable by editing a price
        table."""
        return self.cost_usd is None and any(self.tokens.values())

    @property
    def zero_duration(self) -> bool:
        """`active_work_ms = 0` is never NULL, so it is a gap wearing a
        number's clothes."""
        return (self.active_work_ms or 0) == 0


@dataclass(frozen=True)
class FeatureCost:
    """Dollars for one feature, with the two gaps that qualify them."""

    usd: Optional[float]
    sessions: int
    recalled: int
    shared: int
    gaps: dict

    upper_bound: bool = True

    def to_dict(self) -> dict:
        return {"usd": self.usd, "upper_bound": self.upper_bound,
                "sessions": self.sessions, "recalled": self.recalled,
                "shared": self.shared, "gaps": dict(self.gaps)}


@dataclass(frozen=True)
class WorkTime:
    """Wall-clock the agent was operating — not the human's attended day."""

    active_work_ms: int
    gaps: dict

    def to_dict(self) -> dict:
        return {"active_work_ms": self.active_work_ms, "gaps": dict(self.gaps)}


@dataclass(frozen=True)
class Coverage:
    """Declared sessions and dollars over all of them.

    A permanent fixture of every ledger read, not a launch-time apology: it is
    the denominator that stops a per-feature figure being read as a share of
    the whole, and under the no-backfill rule it stays low for a long time.
    """

    declared_sessions: int
    all_sessions: int
    declared_usd: float
    all_usd: float

    def to_dict(self) -> dict:
        return {"declared_sessions": self.declared_sessions,
                "all_sessions": self.all_sessions,
                "declared_usd": self.declared_usd, "all_usd": self.all_usd}


def facts_for(trace_ids) -> dict:
    """`{trace_id: SessionFacts}`, with a row for every id asked about.

    An id the `sessions` table does not hold still gets an entry, flagged
    `known=False`: an attribution naming a trace regin never captured is a
    different thing from one whose usage was not ingested, and silently
    dropping it would make a declared session vanish from its own ledger.
    """
    ids = [t for t in dict.fromkeys(trace_ids) if t]
    out = {t: SessionFacts(trace_id=t, tokens={c: None for c in _TOKEN_COLUMNS},
                           known=False) for t in ids}
    if not ids:
        return out
    columns = ", ".join(_TOKEN_COLUMNS)
    from sqlalchemy import bindparam
    with SessionLocal() as session:
        rows = session.exec(text(
            f"SELECT trace_id, title, started_at, cost_usd, active_work_ms, "
            f"{columns} FROM sessions WHERE trace_id IN :ids"
        ).bindparams(bindparam("ids", ids, expanding=True))).all()
    for row in rows:
        out[row[0]] = SessionFacts(
            trace_id=row[0], title=row[1], started_at=row[2],
            cost_usd=row[3], active_work_ms=row[4],
            tokens=dict(zip(_TOKEN_COLUMNS, row[5:])))
    return out


def _tokens_for(seen: list) -> dict:
    """The four columns, each summed on its own. Never a total."""
    return {name.replace("_tokens", ""):
            sum(f.tokens.get(name) or 0 for f in seen)
            for name in _TOKEN_COLUMNS}


def _feature_cost(attributed: list, seen: list, shared: int) -> FeatureCost:
    priced = [f.cost_usd for f in seen if f.cost_usd is not None]
    return FeatureCost(
        usd=round(sum(priced), 10) if attributed else None,
        sessions=len(attributed),
        recalled=sum(1 for row in attributed if row.provenance == "recalled"),
        shared=shared,
        gaps={"no_usage_captured": sum(1 for f in seen if f.no_usage_captured),
              "model_unpriced": sum(1 for f in seen if f.model_unpriced)})


def roll_up(attributed, facts: dict) -> tuple:
    """`(FeatureCost, WorkTime, tokens)` for one feature's session list.

    A session serving two features charges the **primary in full and the
    secondary zero**. Splitting invents a ratio nothing in the data supports;
    duplicating breaks the one global invariant worth holding — ∑ per-feature
    cost ≤ total attributed spend.
    """
    charged = [row for row in attributed if row.is_primary]
    seen = [facts[row.trace_id] for row in charged]
    work = WorkTime(
        active_work_ms=sum(f.active_work_ms or 0 for f in seen),
        gaps={"zero_duration": sum(1 for f in seen if f.zero_duration)})
    return (_feature_cost(attributed, seen, len(attributed) - len(charged)),
            work, _tokens_for(seen))


def coverage() -> Coverage:
    """How much of regin's whole history any feature has claimed."""
    with SessionLocal() as session:
        totals = session.exec(text(
            "SELECT COUNT(*), COALESCE(SUM(cost_usd), 0) FROM sessions"
        )).first()
        from lib.features import attribution  # noqa: PLC0415
        declared = session.exec(text(
            f"WITH {attribution.LANE}"
            " SELECT COUNT(*), COALESCE(SUM(s.cost_usd), 0) FROM sessions s"
            " WHERE s.trace_id IN ("
            "   SELECT trace_id FROM session_features"
            "   UNION SELECT trace_id FROM lane)"
        )).first()
    return Coverage(declared_sessions=int(declared[0] or 0),
                    all_sessions=int(totals[0] or 0),
                    declared_usd=float(declared[1] or 0.0),
                    all_usd=float(totals[1] or 0.0))
