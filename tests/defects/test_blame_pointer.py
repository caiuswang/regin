"""Blame is a pointer at an immutable event, not a cached string (regin:82).

`blamed_against_state` was a nullable text column written once and read as a
verdict. A pointer into an append-only table cannot go stale and cannot be
silently absent: the event it names is still there, still says what it said,
and an escape is `blamed_event.kind == 'shipped'` — one comparison, no
vocabulary.
"""

from __future__ import annotations

from lib.defects import escapes as escapes_module
from lib.defects import store as defect_store
from lib.features import events, store as feature_store


def _shipped(title: str):
    feature = feature_store.create(title)
    events.append(feature.id, events.SHIPPED, declared_by=events.OPERATOR)
    return feature


def test_reattribution_repoints_snapshot(tmp_db):
    """A new blame takes a new snapshot — and the old feature is exonerated."""
    shipped = _shipped("feature A")
    building = feature_store.create("feature B")
    events.append(building.id, events.BUILD_STARTED,
                  declared_by=events.OPERATOR)
    defect = defect_store.record("the thing broke", feature_id=shipped.id)
    assert escapes_module.counts_for(shipped.id).escapes == 1

    moved = defect_store.attribute(defect.id, building.id)

    assert moved.blamed_event_id == events.last(building.id).id
    assert escapes_module.counts_for(shipped.id).escapes == 0
    assert escapes_module.counts_for(building.id).escapes == 0


def test_later_ship_does_not_forge_an_escape(tmp_db):
    """Shipping after blame cannot rewrite what the blame described."""
    feature = feature_store.create("a feature still building")
    events.append(feature.id, events.BUILD_STARTED,
                  declared_by=events.OPERATOR)
    defect = defect_store.record("found mid-build", feature_id=feature.id)
    pointed_at = defect.blamed_event_id

    events.append(feature.id, events.SHIPPED, declared_by=events.OPERATOR)

    assert defect_store.get(defect.id).blamed_event_id == pointed_at
    assert escapes_module.counts_for(feature.id).escapes == 0


def test_escape_is_a_pointer_comparison(tmp_db):
    """One join, no string column — and no `blamed_against_state` to read."""
    feature = _shipped("a shipped feature")
    defect_store.record("it leaked", feature_id=feature.id)

    counts = escapes_module.counts_for(feature.id)

    assert counts.escapes == 1
    assert not hasattr(defect_store.get(defect_id=1), "blamed_against_state")
