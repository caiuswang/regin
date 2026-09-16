"""Enumerating every feature regin knows.

The index used to be a four-arm union of strings, and most of this file was
about which arm let a key in. There is no union: the population is the
`features` table, so what is left to pin is that the page stays one statement,
that its ordering is honest about gaps, and that a search narrows the view
without changing what the counters describe.
"""

from __future__ import annotations

from lib.features import index as index_module


def _ids(page):
    return [row.id for row in page.items]


def test_a_feature_row_is_a_row_in_the_index(feature_row):
    feature = feature_row("a feature")

    assert _ids(index_module.page()) == [feature.id]


def test_a_defect_alone_puts_nothing_in_the_population(defect_row):
    """A quarter of the old population existed *only* because a defect named
    a key. A blame is not a declaration that a feature exists."""
    defect_row(None)

    assert index_module.page().items == []
    assert index_module.page().total == 0


def test_index_is_one_query_regardless_of_row_count(
        session_row, task_lane, feature_row, counted_queries):
    """A row must add no query. `ledger.build()` measured 726-1679 ms per
    feature, so a page that fired one read per row would be 15-35 s on the real
    population — the cost of a list has to be flat in its length."""
    first_feature = feature_row("first")
    session_row("t-idx-one")
    task_lane(829, first_feature.id, [("t-idx-one", "done")])
    with counted_queries() as first:
        index_module.page()

    for n in range(5):
        session_row(f"t-idx-n{n}")
        task_lane(830 + n, feature_row(f"f{n}").id, [(f"t-idx-n{n}", "done")])
    with counted_queries() as many:
        page = index_module.page()

    assert len(page.items) >= 6
    assert many.value == first.value


def test_coverage_is_computed_once_per_page_not_once_per_row(
        session_row, task_lane, feature_row, monkeypatch):
    from lib.features import cost as cost_module
    calls = []
    real = cost_module.coverage
    monkeypatch.setattr(cost_module, "coverage",
                        lambda: (calls.append(1), real())[1])
    for n in range(3):
        session_row(f"t-cov-{n}")
        task_lane(840 + n, feature_row(f"c{n}").id, [(f"t-cov-{n}", "done")])

    index_module.page()

    assert len(calls) == 1


def test_index_never_calls_ledger_build(session_row, task_lane, feature_row,
                                        monkeypatch):
    """`ledger.build()` measured 726-1679 ms per feature; a 21-row loop is
    15-35 s, which is why the row is a join and not a ledger."""
    from lib.features import ledger as ledger_module
    monkeypatch.setattr(ledger_module, "build", _forbidden)
    session_row("t-idx-4")
    task_lane(850, feature_row().id, [("t-idx-4", "done")])

    index_module.page()


def test_index_never_resolves_a_tracker_reference(session_row, task_lane,
                                                  feature_row, monkeypatch):
    from lib.tasks.integrations import registry
    monkeypatch.setattr(registry, "resolve_ref", _forbidden)
    session_row("t-idx-5")
    task_lane(851, feature_row().id, [("t-idx-5", "done")])

    index_module.page()


def test_unattributed_defects_is_a_counter_never_a_row(defect_row):
    defect_row(None)

    page = index_module.page()

    assert page.unattributed_defects == 1
    assert page.items == []


def test_ordering_is_most_recent_activity_descending(session_row, task_lane,
                                                     feature_row):
    old, new = feature_row("old"), feature_row("new")
    session_row("t-old", started="2026-01-01 10:00:00")
    session_row("t-new", started="2026-06-01 10:00:00")
    task_lane(860, old.id, [("t-old", "done")])
    task_lane(861, new.id, [("t-new", "done")])

    ids = _ids(index_module.page())

    assert ids.index(new.id) < ids.index(old.id)


def test_a_same_day_defect_outranks_an_earlier_session(session_row, task_lane,
                                                       defect_row,
                                                       feature_row):
    """`sessions.started_at` is ISO with a `T` and `defects.opened_at`
    separates with a space, so MAX over the raw strings compared `T` (0x54)
    against ` ` (0x20) and the session always won inside one day."""
    feature = feature_row()
    session_row("t-early", started="2026-06-01T08:00:00.000000")
    task_lane(890, feature.id, [("t-early", "done")])
    defect_row(feature.id, opened_at="2026-06-01 23:00:00")

    row = {r.id: r for r in index_module.page().items}[feature.id]

    assert row.last_activity.startswith("2026-06-01 23:00:00")


def test_a_later_session_still_outranks_an_earlier_defect(session_row,
                                                          task_lane,
                                                          defect_row,
                                                          feature_row):
    feature = feature_row()
    session_row("t-late", started="2026-06-01T23:00:00.000000")
    task_lane(891, feature.id, [("t-late", "done")])
    defect_row(feature.id, opened_at="2026-06-01 08:00:00")

    row = {r.id: r for r in index_module.page().items}[feature.id]

    assert row.last_activity.startswith("2026-06-01 23:00:00")


def test_null_cost_never_sorts_as_cheapest(session_row, task_lane,
                                           feature_row):
    """A feature with no session has no cost at all. Ordering by cost would
    file that absence beside `$0.00`, which is the one rendering this surface
    refuses — so the order must not be cost."""
    costly, idle = feature_row("costly"), feature_row("idle")
    session_row("t-costly", cost=100.0, started="2026-01-01 10:00:00")
    task_lane(870, costly.id, [("t-costly", "done")])

    rows = {row.id: row for row in index_module.page().items}

    assert rows[idle.id].cost_usd is None
    assert rows[idle.id].sessions == 0


def test_every_row_carries_a_stage_and_its_own_title(feature_row):
    """The stage used to need a cached tracker snapshot, so 31 features read
    `null` because nobody had fetched them. It is two local reads now, and
    every feature has both."""
    from lib.features import events
    feature = feature_row("A real title", stage=events.SHIPPED)

    row = {r.id: r for r in index_module.page().items}[feature.id]

    assert row.title == "A real title"
    assert row.stage == "shipped"
    assert row.rounds == 1


def test_a_shipped_feature_with_an_escape_reads_escaped_in_the_index(
        feature_row, defect_row):
    """The index computes the stage from the counts it already has, rather
    than re-reading the defect ledger per row — and must reach the same answer
    the ledger page does."""
    from lib.features import events, ledger as ledger_module
    feature = feature_row(stage=events.SHIPPED)
    defect_row(feature.id)

    row = {r.id: r for r in index_module.page().items}[feature.id]

    assert row.stage == "escaped"
    assert row.stage == ledger_module.build(feature.id).stage


def test_total_describes_the_population_not_the_match(feature_row):
    for n in range(3):
        feature_row(f"feature {n}")

    page = index_module.page(q="feature 0")

    assert len(page.items) == 1
    assert page.total >= 3


def test_q_matches_the_title(feature_row):
    feature = feature_row("Zebra crossing")

    assert feature.id in _ids(index_module.page(q="zebra"))


def test_q_matches_the_external_reference(feature_row):
    feature = feature_row("something", origin="linear", external_ref="CAI-1995")

    assert feature.id in _ids(index_module.page(q="1995"))


def test_q_escapes_like_wildcards(feature_row):
    feature_row("anything")

    assert index_module.page(q="%").items == []


def _forbidden(*args, **kwargs):
    raise AssertionError("this call must not happen on an index read")
