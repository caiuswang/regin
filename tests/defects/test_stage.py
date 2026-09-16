"""Acceptance items for CAI-215 — the pipeline stage and its gate.

Stage is derived only from declared facts: the tracker's state, its recorded
state history, and the local defect ledger. Nothing is inferred from prose,
proximity or a clock.
"""

from __future__ import annotations

from lib.defects import stage, store
from lib.tasks.integrations.base import ExternalIssue


def _issue(state_type: str, *, rounds: int = 1,
           key: str = "CAI-215") -> ExternalIssue:
    return ExternalIssue(
        provider="linear", key=key, url="https://x/", title="t",
        state=state_type.title(), state_category=state_type, rounds=rounds)


def _blamed(symptom: str, key: str, state: str):
    """A defect carrying the state it was blamed against."""
    from sqlmodel import text
    from lib.orm import SessionLocal
    row = store.record(symptom, feature_key=key)
    with SessionLocal() as session:
        session.exec(text("UPDATE defects SET blamed_against_state = :s "
                          "WHERE id = :i").bindparams(s=state, i=row.id))
        session.commit()
    return row


def test_an_unstarted_issue_is_the_spec_stage():
    assert stage.stage_for(_issue("backlog")).name == "spec"


def test_a_started_issue_is_the_building_stage():
    assert stage.stage_for(_issue("started")).name == "building"


def test_a_completed_issue_never_walked_back_is_shipped():
    assert stage.stage_for(_issue("completed")).name == "shipped"


def test_a_shipped_feature_with_a_defect_blamed_on_it_is_escaped():
    """The stage `reworking` used to occupy, re-read. It fired on `rounds > 1`
    — a completion walked back — which the pipeline now forbids, so the live
    signal is regin's own ledger: shipped, and leaking."""
    _blamed("it leaks", "CAI-215", "completed")

    result = stage.stage_for(_issue("completed"))

    assert result.name == "escaped"
    assert result.escapes == 1


def test_a_reopened_issue_is_still_read_from_its_current_state():
    """`rounds` rides along and is reported, but no longer names a stage."""
    result = stage.stage_for(_issue("started", rounds=2))

    assert result.name == "building"
    assert result.rounds == 2


def test_the_stage_carries_rounds_and_open_defect_count():
    store.record("a real one", feature_key="CAI-215")
    closed = store.record("already handled", feature_key="CAI-215")
    store.close(closed.id, missing_check="a test")

    result = stage.stage_for(_issue("started"))

    assert result.rounds == 1
    assert result.open_defects == 1
