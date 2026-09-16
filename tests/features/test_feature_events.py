"""`feature_events` is append-only, and every derived figure reads it (regin:82).

`rounds`, `claimed_at` and `stage` used to be columns that a writer had to
remember to move. Here they are queries, so the counter and the fact have no
way to disagree — and the history a walk-back is computed from survives the
walk-back.
"""

from __future__ import annotations

import pytest

from lib.features import events, index, ledger as ledger_module, store
from lib.orm import SessionLocal
from lib.orm.models.features import FeatureEvent
from lib.tasks.integrations.base import (
    STATE_BACKLOG, STATE_CANCELED, STATE_COMPLETED, STATE_STARTED,
    STATE_TRIAGE, STATE_UNKNOWN, STATE_UNSTARTED,
)


def _shipped(title: str = "a shipped feature"):
    feature = store.create(title)
    events.append(feature.id, events.SHIPPED, declared_by=events.OPERATOR)
    return feature


def test_restart_appends_and_keeps_history(tmp_db):
    """A run over a shipped feature is a round, and the ship stays on record."""
    feature = _shipped()

    events.on_run_start(feature.id, run_id=7)

    assert events.rounds(feature.id) == 2
    assert events.claimed_at(feature.id) is None
    kinds = [row.kind for row in events.history(feature.id)]
    assert kinds == [events.SPECCED, events.SHIPPED, events.RESTARTED]


def test_second_run_in_one_round_is_not_a_round(tmp_db):
    """Two attempts inside one round append one event, not two."""
    feature = store.create("a feature being built")
    events.on_run_start(feature.id, run_id=1)

    assert events.on_run_start(feature.id, run_id=2) is None
    assert events.rounds(feature.id) == 1
    assert events.stage(feature.id) == events.STAGE_BUILDING


@pytest.mark.parametrize("stage,expected", [
    (events.STAGE_SPEC, events.BUILD_STARTED),
    (events.STAGE_BUILDING, None),
    (events.STAGE_SHIPPED, events.RESTARTED),
    (events.STAGE_ESCAPED, events.RESTARTED),
    (events.STAGE_ABANDONED, events.BUILD_STARTED),
])
def test_run_start_table_is_exhaustive(tmp_db, defect_row, stage, expected):
    """Every stage has a row in the table; none falls to a default branch."""
    feature = store.create(f"a feature at {stage}")
    if stage == events.STAGE_BUILDING:
        events.append(feature.id, events.BUILD_STARTED,
                      declared_by=events.OPERATOR)
    elif stage in (events.STAGE_SHIPPED, events.STAGE_ESCAPED):
        events.append(feature.id, events.SHIPPED, declared_by=events.OPERATOR)
    elif stage == events.STAGE_ABANDONED:
        events.append(feature.id, events.ABANDONED,
                      declared_by=events.OPERATOR)
    if stage == events.STAGE_ESCAPED:
        defect_row(feature_id=feature.id, symptom="it leaked")
    assert events.stage(feature.id) == stage

    written = events.on_run_start(feature.id, run_id=1)

    assert (written.kind if written else None) == expected


def test_resuming_an_abandoned_feature_costs_no_round(tmp_db):
    """Picking work back up is not a walk-back — nothing was called done."""
    feature = store.create("an abandoned feature")
    events.append(feature.id, events.ABANDONED, declared_by=events.OPERATOR)

    written = events.on_run_start(feature.id, run_id=1)

    assert written.kind == events.BUILD_STARTED
    assert events.rounds(feature.id) == 1


def test_abandoning_a_shipped_feature_is_not_a_walk_back(tmp_db):
    """Taking a shipped capability down costs no round, and clears the claim."""
    feature = _shipped()

    events.append(feature.id, events.ABANDONED, declared_by=events.OPERATOR)

    assert events.stage(feature.id) == events.STAGE_ABANDONED
    assert events.rounds(feature.id) == 1
    assert events.claimed_at(feature.id) is None


def test_sync_is_idempotent_on_unchanged_state(tmp_db):
    """A Refresh that learns nothing new writes nothing."""
    feature = store.create("a remote feature", origin="linear",
                           external_ref="CAI-1")
    events.on_sync(feature.id, STATE_STARTED, provider="linear")
    before = len(events.history(feature.id))

    assert events.on_sync(feature.id, STATE_STARTED, provider="linear") is None
    assert len(events.history(feature.id)) == before


def test_unknown_remote_state_writes_no_event(tmp_db):
    """A word regin does not carry is recorded as an error, never guessed."""
    feature = store.create("a remote feature", origin="linear",
                           external_ref="CAI-2")

    assert events.on_sync(feature.id, STATE_UNKNOWN, provider="linear") is None

    assert len(events.history(feature.id)) == 1
    assert store.get(feature.id).sync_error


@pytest.mark.parametrize("category,kind", [
    (STATE_COMPLETED, events.SHIPPED),
    (STATE_STARTED, events.BUILD_STARTED),
    (STATE_BACKLOG, events.SPECCED),
    (STATE_UNSTARTED, events.SPECCED),
    (STATE_TRIAGE, events.SPECCED),
    (STATE_CANCELED, events.ABANDONED),
])
def test_remote_state_maps_without_a_default(tmp_db, category, kind):
    """Every category regin carries maps to a named kind."""
    assert events.kind_for_state(category) == kind


def test_event_order_is_by_id_not_time(tmp_db):
    """Two events sharing a timestamp still have one order — the id's."""
    feature = store.create("a feature shipped and restarted at once")
    at = "2026-09-15 08:00:00"
    with SessionLocal() as session:
        for kind in (events.SHIPPED, events.RESTARTED):
            session.add(FeatureEvent(feature_id=feature.id, kind=kind,
                                     declared_by=events.OPERATOR, at=at))
        session.commit()

    assert events.last(feature.id).kind == events.RESTARTED
    assert events.stage(feature.id) == events.STAGE_BUILDING


def test_feature_title_is_authoritative(tmp_db, native_task):
    """Two titles, two questions — the ledger never falls back to the task's."""
    feature = store.create("what the feature delivers")
    task_id = native_task(title="what this step does")
    store.adopt_task(feature.id, task_id)

    assert store.get(feature.id).title == "what the feature delivers"
    assert ledger_module.build(feature.id).title == "what the feature delivers"
    assert [row.title for row in index.page().items] == [
        "what the feature delivers"]


def test_feature_without_task_cannot_run_but_can_ship(tmp_db):
    """Zero tasks is a runnable-nothing, not an unshippable feature."""
    feature = store.create("a feature with no task")

    with pytest.raises(store.NotRunnable):
        store.start_run(feature.id)

    events.append(feature.id, events.SHIPPED, declared_by=events.OPERATOR)
    assert events.stage(feature.id) == events.STAGE_SHIPPED
