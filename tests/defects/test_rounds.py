"""Acceptance items for CAI-206 — defect records and the round counter.

Each test is one acceptance item from the spec. Red is the checklist, green is
the ship gate, green in CI afterwards is the regression watch.

CAI-241 retired the feature-level `store.rounds()` these once asserted. It read
`1 + every reopen across a feature's defects`, which witnesses nothing about
done-ness: it could read 4 on a feature that never shipped and 1 on one shipped
and walked back twice. Rounds-to-done is `lib.defects.tracker` alone;
`Defect.round` is fix attempts on one defect and is never aggregated.
"""

from __future__ import annotations

import pytest

from lib.defects import DefectError, store

FEATURE = "CAI-206"


def test_defect_recorded_against_feature_is_listed_by_feature_key():
    store.record("the fold gutter never unfolds", feature_key=FEATURE)
    store.record("the sticky bar clips its title", feature_key="CAI-999")

    symptoms = [d.symptom for d in store.for_feature(FEATURE)]

    assert symptoms == ["the fold gutter never unfolds"]


def test_unattributed_defect_is_recorded_and_belongs_to_no_feature():
    orphan = store.record("something is off, no idea where")
    store.close(orphan.id, missing_check="a test")
    store.reopen(orphan.id)

    assert [d.id for d in store.unattributed()] == [orphan.id]
    assert store.for_feature(FEATURE) == []


def test_reopening_a_closed_defect_increments_its_round():
    """The round only moves out of a *closed* state: incrementing it on a row
    never called done would record a walk-back that never happened."""
    defect = store.record("still not fixed", feature_key=FEATURE)
    assert defect.round == 1

    store.close(defect.id, missing_check="a test")
    assert store.reopen(defect.id).round == 2
    store.close(defect.id, missing_check="a better test")
    assert store.reopen(defect.id).round == 3


def test_a_defects_round_is_its_own_and_is_never_summed_to_the_feature():
    first = store.record("gutter folds the newest span", feature_key=FEATURE)
    second = store.record("resume bar offers a dead session",
                          feature_key=FEATURE)
    for _ in range(2):
        store.close(first.id, missing_check="a test")
        store.reopen(first.id)
    store.close(second.id, missing_check="a test")
    store.reopen(second.id)

    assert {d.id: d.round for d in store.for_feature(FEATURE)} == {
        first.id: 3, second.id: 2}
    assert not hasattr(store, "rounds")


def test_closing_without_a_check_answer_is_refused():
    defect = store.record("a tap selects the row", feature_key=FEATURE)

    with pytest.raises(DefectError):
        store.close(defect.id, missing_check="   ")

    assert store.for_feature(FEATURE)[0].status == "open"
    closed = store.close(defect.id, missing_check="a playwright tap-target spec")
    assert closed.status == "closed"
    assert closed.missing_check == "a playwright tap-target spec"


def test_recording_the_same_symptom_twice_for_a_feature_is_idempotent():
    first = store.record("images vanish on paste", feature_key=FEATURE)
    again = store.record("images vanish on paste", feature_key=FEATURE)

    assert again.id == first.id
    assert again.round == 1
    assert len(store.for_feature(FEATURE)) == 1


def test_attributing_an_unattributed_defect_later_moves_it_onto_the_feature():
    orphan = store.record("the composer loses its draft")
    assert store.for_feature(FEATURE) == []

    store.attribute(orphan.id, FEATURE)

    assert [d.id for d in store.for_feature(FEATURE)] == [orphan.id]
    assert store.unattributed() == []
