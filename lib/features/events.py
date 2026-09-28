"""A feature's lifecycle, as an append-only log (regin:82).

Every figure the pipeline reports about where a feature stands — its stage, how
many rounds it has taken, when it was last claimed done, whether a defect
escaped it — is a **query over `feature_events`**. None of them is a stored
column, which is the point: a counter and the fact it counts cannot drift when
there is only the fact.

Three rules make that work, and each one exists because its absence was a bug:

* **The order is `id`, never `at`.** Two events written in one transaction
  share a timestamp, so a ship-then-restart read by clock would come back in
  whichever order the timestamp's precision allowed.
* **Writing is decided by the last event's kind alone.** No clock, no run
  sequence number, no comparison of two moments — so reporting the same run's
  start twice appends nothing the second time.
* **Nothing is ever deleted or updated.** Undoing a ship is a `restarted` row.
  That is what keeps the history auditable and `rounds` recomputable.
"""

from __future__ import annotations

from typing import Optional

from sqlmodel import select

from lib.activity_log import get_activity_logger
from lib.orm import SessionLocal
from lib.orm.models.features import (
    ABANDONED, BUILD_STARTED, KINDS, OPERATOR, RESTARTED, SHIPPED, SPECCED,
    Feature, FeatureEvent,
)
from lib.tasks.integrations.base import (
    STATE_BACKLOG, STATE_CANCELED, STATE_COMPLETED, STATE_STARTED,
    STATE_TRIAGE, STATE_UNSTARTED,
)

log = get_activity_logger("defects")

__all__ = ["ABANDONED", "BUILD_STARTED", "KINDS", "OPERATOR", "RESTARTED",
           "SHIPPED", "SPECCED", "STAGE_ABANDONED", "STAGE_BUILDING",
           "STAGE_ESCAPED", "STAGE_SHIPPED", "STAGE_SPEC", "STAGES",
           "append", "blurb", "claimed_at", "history", "kind_for_state",
           "last", "on_run_start", "on_sync", "rounds", "run_declarer",
           "stage", "stage_from", "stage_rank_sql", "stage_sql",
           "sync_declarer"]

STAGE_SPEC = "spec"
STAGE_BUILDING = "building"
STAGE_SHIPPED = "shipped"
STAGE_ESCAPED = "escaped"
#: The fifth stage, and the one the old mapping could not express: `canceled`
#: fell through `name_for`'s else branch and rendered as `spec`, so a feature
#: somebody had taken down read as one nobody had started.
STAGE_ABANDONED = "abandoned"
STAGES: tuple[str, ...] = (STAGE_SPEC, STAGE_BUILDING, STAGE_SHIPPED,
                           STAGE_ESCAPED, STAGE_ABANDONED)

_BLURB = {
    STAGE_SPEC: "specified, not started",
    STAGE_BUILDING: "being built",
    STAGE_SHIPPED: "completed and never walked back",
    STAGE_ESCAPED: "shipped, and bugs have been blamed on it since",
    STAGE_ABANDONED: "taken down or dropped — not a walked-back completion",
}

#: The last event's kind → the stage, before the escape question. `shipped`
#: is absent because it is the one kind that needs the second column.
_STAGE_OF = {
    SPECCED: STAGE_SPEC,
    BUILD_STARTED: STAGE_BUILDING,
    RESTARTED: STAGE_BUILDING,
    ABANDONED: STAGE_ABANDONED,
}

#: What a run beginning means, per stage. Total over `STAGES`, so no stage
#: reaches a default branch — `building` is an explicit "write nothing", which
#: is what makes a second attempt inside one round cost no round.
_ON_RUN_START = {
    STAGE_SPEC: BUILD_STARTED,
    STAGE_BUILDING: None,
    STAGE_SHIPPED: RESTARTED,
    STAGE_ESCAPED: RESTARTED,
    STAGE_ABANDONED: BUILD_STARTED,
}

#: A remote tracker's vocabulary, folded into ours. `unknown` is deliberately
#: absent: guessing a kind for a word we do not carry would silently decide an
#: escape, so the caller records `sync_error` and appends nothing.
_KIND_OF_STATE = {
    STATE_COMPLETED: SHIPPED,
    STATE_STARTED: BUILD_STARTED,
    STATE_BACKLOG: SPECCED,
    STATE_UNSTARTED: SPECCED,
    STATE_TRIAGE: SPECCED,
    STATE_CANCELED: ABANDONED,
}


#: A tracker leaving a completed state for one of these is a walk-back — the
#: same fact as a run starting over a shipped feature, so it is a `restarted`
#: and costs the round. Folded as the plain kind, a Done → In Progress on the
#: tracker moved the stage and never reached `rounds`.
_WALK_BACK_KINDS = frozenset({BUILD_STARTED, SPECCED})
_CLAIMED = frozenset({STAGE_SHIPPED, STAGE_ESCAPED})


def run_declarer(run_id: int) -> str:
    """`declared_by` for an event a run's start wrote."""
    return f"run:{int(run_id)}"


def sync_declarer(provider: str) -> str:
    """`declared_by` for an event a remote fetch wrote."""
    return f"sync:{provider}"


def blurb(stage_name: str) -> str:
    return _BLURB.get(stage_name, "")


def kind_for_state(state_category: str) -> Optional[str]:
    """The event kind a remote state implies, or None for a word we do not
    carry."""
    return _KIND_OF_STATE.get(state_category or "")


def append(feature_id: int, kind: str, *, declared_by: str,
           trace_id: Optional[str] = None) -> FeatureEvent:
    """Record one step of a feature's life.

    `declared_by` has no default: every state change is attributable to
    somebody, and a default would make `operator` the thing an unattributed
    write silently became.
    """
    if kind not in KINDS:
        raise ValueError(f"{kind!r} is not a feature event kind")
    if not declared_by:
        raise ValueError("a feature event records who declared it")
    with SessionLocal() as session:
        row = FeatureEvent(feature_id=feature_id, kind=kind,
                           declared_by=declared_by, trace_id=trace_id)
        session.add(row)
        session.commit()
        session.refresh(row)
    log.write("feature_event_appended", feature_id=feature_id, kind=kind,
              declared_by=declared_by)
    return row


def history(feature_id: int) -> list[FeatureEvent]:
    """Every event, oldest first, in `id` order."""
    with SessionLocal() as session:
        return list(session.exec(
            select(FeatureEvent)
            .where(FeatureEvent.feature_id == feature_id)
            .order_by(FeatureEvent.id)).all())


def last(feature_id: int) -> Optional[FeatureEvent]:
    """The current event — `ORDER BY id DESC`, never by `at`."""
    with SessionLocal() as session:
        return session.exec(
            select(FeatureEvent)
            .where(FeatureEvent.feature_id == feature_id)
            .order_by(FeatureEvent.id.desc())).first()


def rounds(feature_id: int) -> int:
    """One build, plus every completion later walked back.

    `1` on a feature with no events at all, which the creation transaction
    makes unreachable — but a floor that reads the same as a real
    never-walked-back feature is the right answer either way, and there is no
    third reading to confuse it with.
    """
    return 1 + sum(1 for row in history(feature_id) if row.kind == RESTARTED)


def claimed_at(feature_id: int) -> Optional[str]:
    """When this feature was last claimed done, if it still stands.

    None once anything follows that claim — a restart or an abandonment — so
    the field answers *is it claimed now*, and never *was it ever*. The history
    is still there for the second question.
    """
    row = last(feature_id)
    return row.at if row is not None and row.kind == SHIPPED else None


def stage_from(kind: Optional[str], escapes: int) -> str:
    """The decision table itself: the last event's kind, plus one question.

    Split out from `stage` so a page rendering many features can feed it the
    kind and escape count it already has, rather than re-querying per row —
    and so the table is exercised directly by its own test instead of through
    whichever reader happened to call it.
    """
    if kind is None:
        return STAGE_SPEC
    if kind != SHIPPED:
        return _STAGE_OF[kind]
    return STAGE_ESCAPED if escapes else STAGE_SHIPPED


def stage_sql(kind_col: str, escapes_col: str) -> str:
    """`stage_from` as a SQL expression over two columns.

    A page that filters or sorts by stage has to compute it in the statement,
    and the stage is not a column. Built from `_STAGE_OF` here rather than
    hand-written in the query module, because a decision table with two
    readings is the drift this whole module exists to prevent — the arms
    cover every kind but `shipped`, so the fall-through IS the escape
    question.
    """
    arms = " ".join(f"WHEN {kind_col} = '{kind}' THEN '{name}'"
                    for kind, name in _STAGE_OF.items())
    return (f"CASE WHEN {kind_col} IS NULL THEN '{STAGE_SPEC}' {arms} "
            f"WHEN COALESCE({escapes_col}, 0) > 0 THEN '{STAGE_ESCAPED}' "
            f"ELSE '{STAGE_SHIPPED}' END")


def stage_rank_sql(stage_col: str) -> str:
    """`STAGES` order as a SQL expression — the lifecycle, not the alphabet.

    Ordered by the string a stage column reads abandoned, building, escaped,
    shipped, spec: five names in an order nobody can name, offered as if it
    were a sort somebody asked for.
    """
    arms = " ".join(f"WHEN {stage_col} = '{name}' THEN {rank}"
                    for rank, name in enumerate(STAGES))
    return f"CASE {arms} ELSE {len(STAGES)} END"


def stage(feature_id: int) -> str:
    """Where the feature stands: the last event's kind, plus one question —
    was a defect blamed against *that* ship claim.

    Only the current claim counts. A defect blamed on an earlier ship was paid
    for by the round that followed it; it stays in the all-time escape figure,
    but reading it here would pin a cleanly re-shipped feature at `escaped`
    for good.
    """
    row = last(feature_id)
    if row is None:
        return STAGE_SPEC
    escapes = 0
    if row.kind == SHIPPED:
        from lib.defects import escapes as escapes_module  # noqa: PLC0415
        escapes = escapes_module.against(row.id)
    return stage_from(row.kind, escapes)


def on_run_start(feature_id: int, run_id: int, *,
                 trace_id: Optional[str] = None) -> Optional[FeatureEvent]:
    """Record what a run beginning means for this feature. None when nothing.

    Idempotent by construction: the decision reads only the last event's kind,
    so the same run reported twice writes once — no de-duplication table, no
    window, no run-sequence check.
    """
    kind = _ON_RUN_START[stage(feature_id)]
    if kind is None:
        return None
    return append(feature_id, kind, declared_by=run_declarer(run_id),
                  trace_id=trace_id)


def on_sync(feature_id: int, state_category: str, *,
            provider: str) -> Optional[FeatureEvent]:
    """Fold a remote state into the log. None when it says nothing new.

    Two ways to say nothing: the remote answered with a word regin does not
    carry — recorded as `sync_error`, never guessed into a kind — or it
    answered with the state the log already ends on, in which case a Refresh
    that learned nothing leaves no row. The test is an identifier comparison,
    not a time window.
    """
    kind = kind_for_state(state_category)
    if kind is None:
        _record_sync_error(feature_id, state_category)
        return None
    current = last(feature_id)
    if current is not None and current.kind == kind:
        return None
    if kind in _WALK_BACK_KINDS and stage(feature_id) in _CLAIMED:
        kind = RESTARTED
    return append(feature_id, kind, declared_by=sync_declarer(provider))


def _record_sync_error(feature_id: int, state_category: str) -> None:
    with SessionLocal() as session:
        row = session.get(Feature, feature_id)
        if row is None:
            return
        row.sync_error = (
            f"the tracker answered with the state {state_category!r}, which "
            "regin does not carry — the stage below is unchanged")
        session.add(row)
        session.commit()
    log.write("feature_sync_vocabulary_unknown", feature_id=feature_id,
              state_category=state_category)
