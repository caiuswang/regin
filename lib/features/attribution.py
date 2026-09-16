"""Which sessions built a feature, and the act that says so (CAI-231, CAI-235,
CAI-239).

**Nothing here infers an attribution.** No cwd heuristic, no branch-name
convention — nothing in regin captures a branch anywhere — and no top-N-by-cost
sweep. A session is attributed because a human declared it or because a task
the operator linked to the feature launched it. Unattributed is a first-class
state and stays the absence of a row.

Two readings have to agree for the counts downstream to mean anything:

* **A run and the conversation it drove are one session.** An SDK run is traced
  under its own id and the conversation it drives is another row in `sessions`;
  `agent_run_sessions` joins the pair. Counting both inflates the session count
  and splits the cost across two rows. The conversation is canonical — one
  capture path, no arithmetic across the pair.
* **Provenance is decided by identity.** Attributing your own trace is
  `declared`; attributing another, or running from a shell with no trace at
  all, is `recalled`. No window to tune, and the borderline case — declaring
  your own session as it ends — correctly reads `declared`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from sqlmodel import select, text

from lib.activity_log import get_activity_logger
from lib.features import lane as lane_module
from lib.orm import SessionLocal
from lib.orm.models.features import (
    DECLARED, RECALLED, TASK_LINK, SessionFeature,
)

log = get_activity_logger("defects")

__all__ = ["DECLARED", "RECALLED", "TASK_LINK", "AmbiguousTaskLink",
           "AttributedSession", "Attribution", "attribute",
           "attributions_for_session", "canonical_trace", "declared_feature",
           "features_for_session", "sessions_for_feature", "traceless_runs",
           "unattribute"]


class AmbiguousTaskLink(RuntimeError):
    """One task links two issues, so its runs belong to neither.

    The posture `lib/tasks/integrations/registry.py` takes on an ambiguous
    paste, for the same reason: first-wins would silently charge one session's
    whole cost to one of two features and nobody would ever see the choice.
    """


class NoDeclaration(RuntimeError):
    """`@session` was asked to resolve a feature and could not."""


@dataclass(frozen=True)
class AttributedSession:
    """One session of a feature, as the ledger counts it."""

    trace_id: str
    provenance: str
    is_primary: int
    round: Optional[int] = None
    #: The task whose link put this session here. Only a `task-link` row has
    #: one — it is the provenance's evidence, and a tag naming no task is a
    #: provenance nobody can audit.
    task_id: Optional[int] = None


@dataclass(frozen=True)
class Attribution:
    """One feature of a session — the reverse of `AttributedSession`."""

    feature_key: str
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


def _lane_traces(feature_key: str) -> list[tuple]:
    """`(canonical trace, task id)` for every run reachable through the lane.

    Derived on every read rather than copied into `session_features`: the link
    — or the parentage — is the operator's live assertion, and a stored copy
    would be a stale second opinion of it.

    The task id rides along because it is the provenance's evidence — the tag
    `task #44` has to name a task somebody can open.
    """
    _refuse_ambiguous(feature_key)
    with SessionLocal() as session:
        rows = session.exec(text(lane_module.with_lane(
            " SELECT DISTINCT trace_id, task_id FROM lane WHERE key = :k"
        )).bindparams(k=feature_key)).all()
    return [(row[0], row[1]) for row in rows]


def _refuse_ambiguous(feature_key: str) -> None:
    """Refuse a task naming two feature keys, one of them this one.

    The guard lives beside the lane so every reader of it inherits the refusal.
    `index._LANE` had none, which is how a two-link task made the index
    double-count sessions the ledger refuses to count at all.
    """
    with SessionLocal() as session:
        rows = session.exec(text(
            f"WITH {lane_module.LANE}"
            " SELECT a.task_id, GROUP_CONCAT(DISTINCT o.key)"
            " FROM ambiguous a JOIN owns o ON o.task_id = a.task_id"
            " WHERE a.task_id IN (SELECT task_id FROM owns WHERE key = :k)"
            " GROUP BY a.task_id"
        ).bindparams(k=feature_key)).all()
    if rows:
        task_id, keys = rows[0]
        named = (keys or "").replace(",", ", ")
        raise AmbiguousTaskLink(
            f"task {task_id} names {len(named.split(', '))} features "
            f"({named}) — say which one owns its sessions rather than "
            "charging one session to both")


def traceless_runs(feature_key: str) -> int:
    """Runs of this feature that produced no trace at all.

    Reported beside the session list rather than dropped: a feature that burned
    three failed launches must not read as untouched.
    """
    with SessionLocal() as session:
        row = session.exec(text(
            f"WITH {lane_module.LANE}{lane_module.TRACELESS}"
        ).bindparams(k=feature_key)).first()
    return int(row[0] or 0)


def features_for_session(trace_id: str) -> list[SessionFeature]:
    """Every feature this session declared, primary first."""
    canonical = canonical_trace(trace_id)
    with SessionLocal() as session:
        rows = session.exec(
            select(SessionFeature)
            .where(SessionFeature.trace_id == canonical)
            .order_by(SessionFeature.is_primary.desc(),
                      SessionFeature.feature_key)).all()
        return list(rows)


def sessions_for_feature(feature_key: str) -> list[AttributedSession]:
    """The sessions of a feature: declared rows and the task lane, merged.

    Every run that produced a trace counts whatever its status — `done`,
    `failed`, and open rows never settled by their callback. The tokens were
    spent either way, and excluding failures would make an expensive feature
    look cheap, which is the opposite of what cost-per-feature exists to show.
    """
    with SessionLocal() as session:
        declared = list(session.exec(
            select(SessionFeature)
            .where(SessionFeature.feature_key == feature_key)).all())
        primary_elsewhere = {
            r[0] for r in session.exec(text(
                "SELECT trace_id FROM session_features "
                "WHERE is_primary = 1 AND feature_key != :k"
            ).bindparams(k=feature_key)).all()}

    rows: dict[str, AttributedSession] = {
        row.trace_id: AttributedSession(
            trace_id=row.trace_id, provenance=row.provenance,
            is_primary=row.is_primary, round=row.round)
        for row in declared}
    for trace, task_id in _lane_traces(feature_key):
        if trace in rows:
            continue
        rows[trace] = AttributedSession(
            trace_id=trace, provenance=TASK_LINK,
            is_primary=0 if trace in primary_elsewhere else 1,
            task_id=task_id)
    return sorted(rows.values(), key=lambda r: r.trace_id)


def _lane_features_for(canonical: str) -> list[tuple]:
    """`(feature_key, task_id)` every declaration puts this session under.

    The same lane read backwards: a run's trace and the conversation it drove
    are one session, so the match is on the canonical id rather than on
    whichever of the pair the run recorded.

    A task naming two features contributes nothing — the lane already excludes
    it — exactly as it contributes nothing to `sessions_for_feature`. The
    reverse direction cannot quietly charge a session to a feature the forward
    direction refuses to charge it to.
    """
    with SessionLocal() as session:
        rows = session.exec(text(lane_module.with_lane(
            " SELECT DISTINCT key, task_id FROM lane WHERE trace_id = :c"
        )).bindparams(c=canonical)).all()
    return [(row[0], row[1]) for row in rows]


def attributions_for_session(trace_id: str) -> list[Attribution]:
    """Every feature this session is counted under, declared rows and the task
    lane merged.

    The mirror of `sessions_for_feature`, and deliberately *not* the same
    function as `features_for_session`. That one answers "what did this
    session claim", which is what blame demands; this one answers "what does
    the ledger count this session against", which is what a reader is owed.
    Measured on the live database, 22 of 23 attributed traces reach their
    feature only through the task lane, so a display built on the narrower
    reader would call them unattributed while the feature page counted them.
    """
    canonical = canonical_trace(trace_id)
    declared = features_for_session(canonical)
    rows = {row.feature_key: Attribution(
        feature_key=row.feature_key, provenance=row.provenance,
        is_primary=row.is_primary, round=row.round) for row in declared}
    for key, task_id in _lane_features_for(canonical):
        if key in rows:
            continue
        rows[key] = Attribution(feature_key=key, provenance=TASK_LINK,
                                is_primary=1, task_id=task_id)
    return sorted(rows.values(),
                  key=lambda r: (-r.is_primary, r.feature_key))


def unattribute(feature_key: str, trace_id: str) -> bool:
    """Withdraw a declaration. True when a row was removed.

    False — never an error — when the session reaches the feature through the
    task lane instead: there is no row to delete, because the assertion is the
    link on the task, and withdrawing it is an act on that task.
    """
    key, target = _checked(feature_key, trace_id)
    canonical = canonical_trace(target)
    with SessionLocal() as session:
        row = session.exec(
            select(SessionFeature).where(
                SessionFeature.trace_id == canonical,
                SessionFeature.feature_key == key)).first()
        if row is None:
            log.read("feature_unattribute_missed", feature_key=key,
                     trace_id=canonical)
            return False
        session.delete(row)
        session.commit()
    log.write("feature_unattributed", feature_key=key, trace_id=canonical)
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


def _row_for(session, canonical: str, key: str) -> tuple:
    """The `(siblings, row)` pair for this trace, creating the row if new."""
    siblings = list(session.exec(
        select(SessionFeature)
        .where(SessionFeature.trace_id == canonical)).all())
    row = next((s for s in siblings if s.feature_key == key), None)
    if row is None:
        row = SessionFeature(trace_id=canonical, feature_key=key)
        siblings.append(row)
    return siblings, row


def _checked(feature_key: str, trace_id: str) -> tuple:
    key, target = (feature_key or "").strip(), (trace_id or "").strip()
    if not key:
        raise ValueError("attribution needs a feature key")
    if not target:
        raise ValueError("attribution needs a session")
    return key, target


def attribute(feature_key: str, trace_id: str, *, primary: bool = False,
              attributing_trace: Optional[str] = None) -> SessionFeature:
    """Declare that a session was building a feature.

    Runnable at any point, including long after the session ended — CAI-239
    makes the late claim the normal case, and nothing here is time-bounded.
    """
    key, target = _checked(feature_key, trace_id)
    canonical = canonical_trace(target)
    provenance = _provenance_for(target, attributing_trace)

    with SessionLocal() as session:
        siblings, row = _row_for(session, canonical, key)
        row.provenance = provenance
        row.round = _round_at_claim(key) if provenance == DECLARED else None
        _apply_primary(session, siblings, row, primary)
        session.add(row)
        session.commit()
        session.refresh(row)
        log.write("feature_attributed", feature_key=key, trace_id=canonical,
                  provenance=provenance, is_primary=row.is_primary)
        return row


def _round_at_claim(feature_key: str) -> Optional[int]:
    """The round the tracker reports right now, or None when it cannot answer.

    None rather than `1`: a real never-reopened feature reads `1`, so the floor
    must not double as "we could not look".
    """
    from lib.defects import tracker  # noqa: PLC0415  (circular at import time)
    from lib.tasks.integrations import IntegrationError  # noqa: PLC0415
    from lib.tasks import issue_cache  # noqa: PLC0415
    try:
        provider, ref = tracker.resolve(feature_key)
        issue = provider.fetch_issue(ref)
    except (tracker.TrackerUnavailable, IntegrationError):
        return None
    issue_cache.remember(ref.provider, ref.key, issue)
    return issue.rounds


def declared_feature(trace_id: str) -> str:
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
            f"`regin feature attribute <KEY> --session {trace_id}` first")
    if len(rows) > 1:
        keys = ", ".join(sorted(r.feature_key for r in rows))
        raise NoDeclaration(
            f"{trace_id} declared two features ({keys}) — name the one that "
            "caused the bug, which is not necessarily either of them")
    return rows[0].feature_key
