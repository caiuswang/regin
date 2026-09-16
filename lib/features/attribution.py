"""Which sessions built a feature, and the act that says so (regin:82).

**Nothing here infers an attribution.** No cwd heuristic, no branch-name
convention — nothing in regin captures a branch anywhere — and no top-N-by-cost
sweep. A session is attributed because a human declared it or because a run of
a task under the feature produced it. Unattributed is a first-class state and
stays the absence of a row.

Two readings have to agree for the counts downstream to mean anything:

* **A run and the conversation it drove are one session.** An SDK run is traced
  under its own id and the conversation it drives is another row in `sessions`;
  `agent_run_sessions` joins the pair, and holds two rows for at least one
  trace. Counting both inflates the session count and splits the cost, so every
  read collapses on `ORDER BY id LIMIT 1`. The rule used to live in
  `lane.py`'s `CANONICAL_TRACE` and fired in only one of its three call sites,
  which is why the index once counted 51 sessions where the ledger counted 50.
* **Provenance is decided by identity.** Attributing your own trace is
  `declared`; attributing another, or running from a shell with no trace at
  all, is `recalled`. No window to tune, and the borderline case — declaring
  your own session as it ends — correctly reads `declared`.

The path from a feature to its runs' sessions is now **two joins**:
`run.trace_id → run.task_id → task.feature_id`. It replaced a four-CTE chain
whose job was to reconcile three different ways a task could hold a feature
key, plus an `ambiguous` guard for the tasks that held two. A single foreign
key makes both unnecessary — a task belongs to at most one feature because it
has one column to say so in.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from sqlmodel import select, text

from lib.activity_log import get_activity_logger
from lib.orm import SessionLocal
from lib.orm.models.features import DECLARED, RECALLED, RUN, SessionFeature

log = get_activity_logger("defects")

__all__ = ["DECLARED", "RECALLED", "RUN", "AttributedSession", "Attribution",
           "attribute", "attributions_for_session", "canonical_trace",
           "declared_feature", "features_for_session", "sessions_for_feature",
           "traceless_runs", "unattribute"]

#: One `agent_run_sessions` row per trace, chosen by id. A correlated subquery
#: rather than a `LEFT JOIN`, because the join is what fans a run out into two
#: sessions when the alias table holds two rows for it.
CANONICAL_TRACE = """
COALESCE((SELECT a.cli_session_id FROM agent_run_sessions a
          WHERE a.trace_id = r.trace_id ORDER BY a.id LIMIT 1),
         r.trace_id)"""

#: `(feature_id, canonical trace, task_id)` for every run that produced a
#: trace. Two joins and one collapse rule — the whole lane.
LANE = f"""
lane AS (
    SELECT t.feature_id AS feature_id,
           {CANONICAL_TRACE} AS trace_id,
           r.task_id AS task_id
    FROM agent_task_runs r
    JOIN agent_tasks t ON t.id = r.task_id
    WHERE r.trace_id IS NOT NULL AND t.feature_id IS NOT NULL
)"""


class NoDeclaration(RuntimeError):
    """`@session` was asked to resolve a feature and could not."""


@dataclass(frozen=True)
class AttributedSession:
    """One session of a feature, as the ledger counts it."""

    trace_id: str
    provenance: str
    is_primary: int
    round: Optional[int] = None
    #: The run whose task put this session here. Only a `run` row has one — it
    #: is the provenance's evidence, and a tag naming no run is a provenance
    #: nobody can audit.
    task_id: Optional[int] = None


@dataclass(frozen=True)
class Attribution:
    """One feature of a session — the reverse of `AttributedSession`."""

    feature_id: int
    provenance: str
    is_primary: int
    round: Optional[int] = None
    task_id: Optional[int] = None


def canonical_trace(trace_id: str) -> str:
    """The conversation trace a run's own trace collapses onto.

    Taking the conversation side unconditionally loses a measured $1.34 across
    three pairs in the live database — an ingestion tail, accepted rather than
    reconciled, because reconstructing a run's cost by summing its children
    mixes two capture paths in one number so nobody could say which produced
    a figure.
    """
    if not trace_id:
        return trace_id
    with SessionLocal() as session:
        row = session.exec(text(
            "SELECT cli_session_id FROM agent_run_sessions "
            "WHERE trace_id = :t ORDER BY id LIMIT 1"
        ).bindparams(t=trace_id)).first()
    return row[0] if row else trace_id


def _lane_traces(feature_id: int) -> list[tuple]:
    """`(canonical trace, task id)` for every run of this feature.

    Derived on every read rather than copied into `session_features`: the
    task's placement under the feature is the operator's live assertion, and a
    stored copy would be a stale second opinion of it.
    """
    with SessionLocal() as session:
        rows = session.exec(text(
            f"WITH {LANE} SELECT DISTINCT trace_id, task_id FROM lane"
            " WHERE feature_id = :f"
        ).bindparams(f=feature_id)).all()
    return [(row[0], row[1]) for row in rows]


def traceless_runs(feature_id: int) -> int:
    """Runs of this feature that produced no trace at all.

    Reported beside the session list rather than dropped: a feature that burned
    three failed launches must not read as untouched.
    """
    with SessionLocal() as session:
        row = session.exec(text(
            "SELECT COUNT(*) FROM agent_task_runs r"
            " JOIN agent_tasks t ON t.id = r.task_id"
            " WHERE t.feature_id = :f AND r.trace_id IS NULL"
        ).bindparams(f=feature_id)).first()
    return int(row[0] or 0)


def features_for_session(trace_id: str) -> list[SessionFeature]:
    """Every feature this session declared, primary first."""
    canonical = canonical_trace(trace_id)
    with SessionLocal() as session:
        rows = session.exec(
            select(SessionFeature)
            .where(SessionFeature.trace_id == canonical)
            .order_by(SessionFeature.is_primary.desc(),
                      SessionFeature.feature_id)).all()
        return list(rows)


def sessions_for_feature(feature_id: int) -> list[AttributedSession]:
    """The sessions of a feature: declared rows and the run lane, merged.

    Every run that produced a trace counts whatever its status — `done`,
    `failed`, and open rows never settled by their callback. The tokens were
    spent either way, and excluding failures would make an expensive feature
    look cheap, which is the opposite of what cost-per-feature exists to show.
    """
    with SessionLocal() as session:
        declared = list(session.exec(
            select(SessionFeature)
            .where(SessionFeature.feature_id == feature_id)).all())
        primary_elsewhere = {
            r[0] for r in session.exec(text(
                "SELECT trace_id FROM session_features "
                "WHERE is_primary = 1 AND feature_id != :f"
            ).bindparams(f=feature_id)).all()}

    rows: dict[str, AttributedSession] = {
        row.trace_id: AttributedSession(
            trace_id=row.trace_id, provenance=row.provenance,
            is_primary=row.is_primary, round=row.round)
        for row in declared}
    for trace, task_id in _lane_traces(feature_id):
        if trace in rows:
            continue
        rows[trace] = AttributedSession(
            trace_id=trace, provenance=RUN,
            is_primary=0 if trace in primary_elsewhere else 1,
            task_id=task_id)
    return sorted(rows.values(), key=lambda r: r.trace_id)


def _lane_features_for(canonical: str) -> list[tuple]:
    """`(feature_id, task_id)` every run puts this session under.

    The same lane read backwards: a run's trace and the conversation it drove
    are one session, so the match is on the canonical id rather than on
    whichever of the pair the run recorded.
    """
    with SessionLocal() as session:
        rows = session.exec(text(
            f"WITH {LANE} SELECT DISTINCT feature_id, task_id FROM lane"
            " WHERE trace_id = :c"
        ).bindparams(c=canonical)).all()
    return [(row[0], row[1]) for row in rows]


def attributions_for_session(trace_id: str) -> list[Attribution]:
    """Every feature this session is counted under, declared rows and the run
    lane merged.

    The mirror of `sessions_for_feature`, and deliberately *not* the same
    function as `features_for_session`. That one answers "what did this
    session claim", which is what blame demands; this one answers "what does
    the ledger count this session against", which is what a reader is owed.
    Measured on the live database, 22 of 23 attributed traces reach their
    feature only through the run lane, so a display built on the narrower
    reader would call them unattributed while the feature page counted them.
    """
    canonical = canonical_trace(trace_id)
    declared = features_for_session(canonical)
    rows = {row.feature_id: Attribution(
        feature_id=row.feature_id, provenance=row.provenance,
        is_primary=row.is_primary, round=row.round) for row in declared}
    for feature_id, task_id in _lane_features_for(canonical):
        if feature_id in rows:
            continue
        rows[feature_id] = Attribution(feature_id=feature_id, provenance=RUN,
                                       is_primary=1, task_id=task_id)
    return sorted(rows.values(), key=lambda r: (-r.is_primary, r.feature_id))


def unattribute(feature_id: int, trace_id: str) -> bool:
    """Withdraw a declaration. True when a row was removed.

    False — never an error — when the session reaches the feature through the
    run lane instead: there is no row to delete, because the assertion is the
    task's placement under the feature, and withdrawing it is an act on that
    task.
    """
    target = _checked(feature_id, trace_id)
    canonical = canonical_trace(target)
    with SessionLocal() as session:
        row = session.exec(
            select(SessionFeature).where(
                SessionFeature.trace_id == canonical,
                SessionFeature.feature_id == feature_id)).first()
        if row is None:
            log.read("feature_unattribute_missed", feature_id=feature_id,
                     trace_id=canonical)
            return False
        session.delete(row)
        session.commit()
    log.write("feature_unattributed", feature_id=feature_id,
              trace_id=canonical)
    return True


def _provenance_for(target: str, attributing: Optional[str]) -> str:
    """Declared iff the trace being attributed *is* the attributing trace.

    An identifier comparison. There is no window, no flag and no notion of how
    long ago anything happened — which is the whole point: a rule keyed on age
    would need a threshold, and any threshold is a guess about memory.
    """
    if not attributing:
        return RECALLED
    if canonical_trace(attributing) == canonical_trace(target):
        return DECLARED
    return RECALLED


def _apply_primary(session, siblings: list, row: SessionFeature,
                   explicit: bool) -> None:
    """Give `row` the primary flag when it is first, or when asked.

    First-writer-wins, with `--primary` demoting the incumbent in the same
    transaction. An unrelated later declaration must not silently move money
    between two features' totals as a side effect of naming a second one.
    """
    if not explicit and any(s.is_primary and s is not row for s in siblings):
        return
    for sibling in siblings:
        sibling.is_primary = 1 if sibling is row else 0
        session.add(sibling)


def _row_for(session, canonical: str, feature_id: int) -> tuple:
    """The `(siblings, row)` pair for this trace, creating the row if new."""
    siblings = list(session.exec(
        select(SessionFeature)
        .where(SessionFeature.trace_id == canonical)).all())
    row = next((s for s in siblings if s.feature_id == feature_id), None)
    if row is None:
        row = SessionFeature(trace_id=canonical, feature_id=feature_id)
        siblings.append(row)
    return siblings, row


def _checked(feature_id: int, trace_id: str) -> str:
    target = (trace_id or "").strip()
    if feature_id is None:
        raise ValueError("attribution needs a feature")
    if not target:
        raise ValueError("attribution needs a session")
    return target


def attribute(feature_id: int, trace_id: str, *, primary: bool = False,
              attributing_trace: Optional[str] = None) -> SessionFeature:
    """Declare that a session was building a feature.

    Runnable at any point, including long after the session ended — the late
    claim is the normal case, and nothing here is time-bounded.
    """
    target = _checked(feature_id, trace_id)
    canonical = canonical_trace(target)
    provenance = _provenance_for(target, attributing_trace)

    with SessionLocal() as session:
        siblings, row = _row_for(session, canonical, feature_id)
        row.provenance = provenance
        row.round = _round_at_claim(feature_id) if provenance == DECLARED \
            else None
        _apply_primary(session, siblings, row, primary)
        session.add(row)
        session.commit()
        session.refresh(row)
        log.write("feature_attributed", feature_id=feature_id,
                  trace_id=canonical, provenance=provenance,
                  is_primary=row.is_primary)
        return row


def _round_at_claim(feature_id: int) -> int:
    """The round this feature stands at right now.

    Always answerable, unlike the tracker fetch it replaces: it is a count of
    local events. So there is no longer a NULL meaning "we could not look" on
    a `declared` row, and `1` unambiguously means never walked back.
    """
    from lib.features import events  # noqa: PLC0415  (circular at import time)
    return events.rounds(feature_id)


def declared_feature(trace_id: str) -> int:
    """Resolve `@session` — the feature this session declared.

    Refuses on nothing declared, on two declared, and on a trace nobody knows.
    Falling back to unattributed is the worst outcome available: the operator
    typed something specifically to attribute and would walk away believing it
    landed.

    Two declared refuses rather than following `is_primary`: that flag carries
    a claim about *this session's spend*, and blame is not that claim — the bug
    is in one of the two and which one is precisely what nobody has looked at.
    """
    rows = features_for_session(trace_id)
    if not rows:
        raise NoDeclaration(
            f"{trace_id} declared no feature — "
            f"`regin feature attribute <ID> --session {trace_id}` first")
    if len(rows) > 1:
        named = ", ".join(str(r.feature_id) for r in sorted(
            rows, key=lambda r: r.feature_id))
        raise NoDeclaration(
            f"{trace_id} declared two features ({named}) — name the one that "
            "caused the bug, which is not necessarily either of them")
    return rows[0].feature_id
