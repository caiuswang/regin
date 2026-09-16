"""The population is the `features` table — nothing else decides it (regin:82).

The old population was a five-arm union of strings, and the two failures it
produced are the two tests here: a feature could be shipped without appearing
in the list at all, and membership depended on which arm happened to hold a key
rather than on a row existing.
"""

from __future__ import annotations

from lib.defects import store as defect_store
from lib.features import attribution, events, index, store


def test_spec_only_feature_ships_and_is_listed(tmp_db):
    """A feature with no task, no run and no defect still ships and lists."""
    feature = store.create("a feature nobody has run")
    events.append(feature.id, events.SHIPPED, declared_by=events.OPERATOR)

    page = index.page()

    assert [row.id for row in page.items] == [feature.id]
    assert events.stage(feature.id) == events.STAGE_SHIPPED


def test_population_is_the_table(tmp_db, native_task, session_row):
    """N rows in, N rows out — whatever the old union arms happen to hold.

    Each of the three writes below put a key into the population under the old
    reading: a defect's blame, a session declaration, and a task. None of them
    is a feature row, so none of them is a feature.
    """
    made = [store.create(f"feature {n}").id for n in range(3)]
    defect_store.record("an unattributed bug", session_id="t-1")
    attribution.attribute(made[0], session_row("t-2"))
    native_task(title="a task under no feature")

    assert sorted(row.id for row in store.population()) == sorted(made)
    assert sorted(row.id for row in index.page().items) == sorted(made)
    assert index.page().total == 3
    assert index.ROW_SOURCE == "features"
