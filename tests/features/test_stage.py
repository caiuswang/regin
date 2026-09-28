"""The stage decision table, read back kind by kind (regin:82).

Two columns — the last event's kind, and whether anything escaped — and the
pair is exhaustive. `abandoned` is the kind the old mapping had no column for:
it fell through `name_for`'s else branch and rendered as `spec`, so a feature
somebody had taken down read as one nobody had started.
"""

from __future__ import annotations

import pytest

from lib.features import events, store


def _at(kind: str, *, escaped=False, defect_row=None):
    feature = store.create(f"a feature whose last event is {kind}")
    if kind != events.SPECCED:
        events.append(feature.id, kind, declared_by=events.OPERATOR)
    if escaped:
        defect_row(feature_id=feature.id, symptom="it leaked")
    return feature


@pytest.mark.parametrize("kind,escaped,expected", [
    (events.SPECCED, False, events.STAGE_SPEC),
    (events.BUILD_STARTED, False, events.STAGE_BUILDING),
    (events.RESTARTED, False, events.STAGE_BUILDING),
    (events.SHIPPED, False, events.STAGE_SHIPPED),
    (events.SHIPPED, True, events.STAGE_ESCAPED),
    (events.ABANDONED, False, events.STAGE_ABANDONED),
])
def test_stage_table_is_exhaustive(tmp_db, defect_row, kind, escaped,
                                   expected):
    """Every kind names a stage; none falls to a default branch."""
    feature = _at(kind, escaped=escaped, defect_row=defect_row)

    assert events.stage(feature.id) == expected
    assert set(events.KINDS) == {events.SPECCED, events.BUILD_STARTED,
                                events.SHIPPED, events.RESTARTED,
                                events.ABANDONED}


def test_abandoned_is_not_spec(tmp_db):
    """The stage a dropped feature reads — the one the old else branch ate."""
    feature = store.create("a dropped feature")
    events.append(feature.id, events.ABANDONED, declared_by=events.OPERATOR)

    assert events.stage(feature.id) == events.STAGE_ABANDONED
    assert events.stage(feature.id) != events.STAGE_SPEC


def test_a_clean_reship_after_a_fixed_escape_reads_shipped(tmp_db,
                                                           defect_row):
    """`escaped` asks about the CURRENT ship claim. A defect blamed on an
    earlier ship was paid for by the round that followed it; it stays an
    escape in the all-time count, but it does not make the new claim wrong."""
    from lib.defects import escapes

    feature = store.create("shipped, escaped, fixed, re-shipped")
    events.append(feature.id, events.SHIPPED, declared_by=events.OPERATOR)
    defect_row(feature_id=feature.id, symptom="it leaked", status="closed")
    assert events.stage(feature.id) == events.STAGE_ESCAPED

    events.on_run_start(feature.id, run_id=1)
    events.append(feature.id, events.SHIPPED, declared_by=events.OPERATOR)

    assert events.stage(feature.id) == events.STAGE_SHIPPED
    assert escapes.counts_for(feature.id).escapes == 1
    assert events.rounds(feature.id) == 2


def test_index_and_ledger_agree_on_a_clean_reship(tmp_db, defect_row):
    """The index computes stage in SQL; it must reach the ledger's answer."""
    from lib.features import index, ledger

    feature = store.create("re-shipped in the index")
    events.append(feature.id, events.SHIPPED, declared_by=events.OPERATOR)
    defect_row(feature_id=feature.id, symptom="it leaked", status="closed")
    events.on_run_start(feature.id, run_id=1)
    events.append(feature.id, events.SHIPPED, declared_by=events.OPERATOR)

    row = {r.id: r for r in index.page().items}[feature.id]
    assert row.stage == events.STAGE_SHIPPED
    assert row.escapes == 1
    assert ledger.build(feature.id).stage == events.STAGE_SHIPPED
