"""Enumerating every feature regin knows (CAI-248, CAI-255).

No distinct-feature-key query existed anywhere before this: every read was
keyed by a key the caller already held. The index is a three-table union, and
the reason it is a union rather than a scan of `session_features` is measured —
a quarter of the population exists only because a defect was blamed on it.
"""

from __future__ import annotations

from lib.features import index as index_module


def _keys(page):
    return [row.key for row in page.items]


def test_union_includes_keys_known_only_through_defects(defect_row):
    defect_row("CAI-920")

    assert "CAI-920" in _keys(index_module.page())


def test_union_includes_keys_known_only_through_task_links(
        session_row, task_lane):
    session_row("t-idx-1")
    task_lane(820, ["CAI-921"], [("t-idx-1", "done")])

    assert "CAI-921" in _keys(index_module.page())


def test_union_includes_keys_known_only_through_declarations(session_row):
    from lib.features import attribution
    session_row("t-idx-2")
    attribution.attribute("CAI-922", "t-idx-2")

    assert "CAI-922" in _keys(index_module.page())


def test_a_key_known_three_ways_is_one_row(session_row, task_lane, defect_row):
    from lib.features import attribution
    session_row("t-idx-3")
    task_lane(821, ["CAI-923"], [("t-idx-3", "done")])
    attribution.attribute("CAI-923", "t-idx-3")
    defect_row("CAI-923")

    assert _keys(index_module.page()).count("CAI-923") == 1


def test_index_is_one_query_regardless_of_row_count(
        session_row, task_lane, counted_queries):
    """A row must add no query. `ledger.build()` measured 726-1679 ms per key,
    so a page that fired one read per row would be 15-35 s on the real
    population — the cost of a list has to be flat in its length."""
    session_row("t-idx-one")
    task_lane(829, ["CAI-929"], [("t-idx-one", "done")])
    with counted_queries() as first:
        index_module.page()

    for n in range(5):
        session_row(f"t-idx-n{n}")
        task_lane(830 + n, [f"CAI-93{n}"], [(f"t-idx-n{n}", "done")])
    with counted_queries() as many:
        page = index_module.page()

    assert len(page.items) >= 6
    assert many.value == first.value


def test_coverage_is_computed_once_per_page_not_once_per_row(
        session_row, task_lane, monkeypatch):
    from lib.features import cost as cost_module
    calls = []
    real = cost_module.coverage
    monkeypatch.setattr(cost_module, "coverage",
                        lambda: (calls.append(1), real())[1])
    for n in range(3):
        session_row(f"t-cov-{n}")
        task_lane(840 + n, [f"CAI-94{n}"], [(f"t-cov-{n}", "done")])

    index_module.page()

    assert len(calls) == 1


def test_index_never_calls_ledger_build(session_row, task_lane, monkeypatch):
    """`ledger.build()` measured 726-1679 ms per key; a 21-key loop is 15-35 s,
    which is why the row is a join and not a ledger."""
    from lib.features import ledger as ledger_module
    monkeypatch.setattr(ledger_module, "build", _forbidden)
    session_row("t-idx-4")
    task_lane(850, ["CAI-950"], [("t-idx-4", "done")])

    index_module.page()


def test_index_never_fetches_an_issue(session_row, task_lane, monkeypatch):
    import lib.defects.tracker as tracker_mod
    monkeypatch.setattr(tracker_mod, "resolve", _forbidden)
    session_row("t-idx-5")
    task_lane(851, ["CAI-951"], [("t-idx-5", "done")])

    index_module.page()


def test_unattributed_defects_is_a_counter_never_a_row(defect_row):
    defect_row(None)

    page = index_module.page()

    assert page.unattributed_defects == 1
    assert None not in _keys(page)
    assert "" not in _keys(page)


def test_ordering_is_most_recent_activity_descending(session_row, task_lane):
    session_row("t-old", started="2026-01-01 10:00:00")
    session_row("t-new", started="2026-06-01 10:00:00")
    task_lane(860, ["CAI-960"], [("t-old", "done")])
    task_lane(861, ["CAI-961"], [("t-new", "done")])

    keys = _keys(index_module.page())

    assert keys.index("CAI-961") < keys.index("CAI-960")


def test_a_same_day_defect_outranks_an_earlier_session(session_row, task_lane,
                                                       defect_row):
    """`sessions.started_at` is ISO with a `T` and `defects.opened_at`
    separates with a space, so MAX over the raw strings compared `T` (0x54)
    against ` ` (0x20) and the session always won inside one day."""
    session_row("t-early", started="2026-06-01T08:00:00.000000")
    task_lane(890, ["CAI-990"], [("t-early", "done")])
    defect_row("CAI-990", opened_at="2026-06-01 23:00:00")

    row = {r.key: r for r in index_module.page().items}["CAI-990"]

    assert row.last_activity.startswith("2026-06-01 23:00:00")


def test_a_later_session_still_outranks_an_earlier_defect(session_row,
                                                          task_lane,
                                                          defect_row):
    session_row("t-late", started="2026-06-01T23:00:00.000000")
    task_lane(891, ["CAI-991"], [("t-late", "done")])
    defect_row("CAI-991", opened_at="2026-06-01 08:00:00")

    row = {r.key: r for r in index_module.page().items}["CAI-991"]

    assert row.last_activity.startswith("2026-06-01 23:00:00")


def test_null_cost_never_sorts_as_cheapest(session_row, task_lane, defect_row):
    """A key with no session has no cost at all. Ordering by cost would file
    that absence beside `$0.00`, which is the one rendering this surface
    refuses — so the order must not be cost."""
    session_row("t-costly", cost=100.0, started="2026-01-01 10:00:00")
    task_lane(870, ["CAI-970"], [("t-costly", "done")])
    defect_row("CAI-971")

    page = index_module.page()
    rows = {row.key: row for row in page.items}

    assert rows["CAI-971"].cost_usd is None
    assert rows["CAI-971"].sessions == 0


def test_a_row_carries_the_cached_stage_and_title(session_row, task_lane,
                                                  cached_issue):
    session_row("t-idx-6")
    task_lane(880, ["CAI-980"], [("t-idx-6", "done")])
    cached_issue("CAI-980", title="A cached title", state_category="completed")

    row = {r.key: r for r in index_module.page().items}["CAI-980"]

    assert row.title == "A cached title"
    assert row.stage == "shipped"
    assert row.stage_synced_at is not None


def test_a_row_without_a_cached_issue_carries_no_stage(session_row, task_lane):
    session_row("t-idx-7")
    task_lane(881, ["CAI-981"], [("t-idx-7", "done")])

    row = {r.key: r for r in index_module.page().items}["CAI-981"]

    assert row.stage is None
    assert row.stage_synced_at is None
    assert row.title is None


def test_total_describes_the_population_not_the_match(session_row, task_lane):
    for n in range(3):
        session_row(f"t-q-{n}")
        task_lane(890 + n, [f"CAI-99{n}"], [(f"t-q-{n}", "done")])

    page = index_module.page(q="CAI-990")

    assert len(page.items) == 1
    assert page.total >= 3


def test_q_matches_the_key(session_row, task_lane):
    session_row("t-q-key")
    task_lane(895, ["CAI-1995"], [("t-q-key", "done")])

    assert "CAI-1995" in _keys(index_module.page(q="1995"))


def test_q_matches_the_cached_title(session_row, task_lane, cached_issue):
    session_row("t-q-title")
    task_lane(896, ["CAI-1996"], [("t-q-title", "done")])
    cached_issue("CAI-1996", title="Zebra crossing")

    assert "CAI-1996" in _keys(index_module.page(q="zebra"))


def test_q_escapes_like_wildcards(session_row, task_lane):
    session_row("t-q-pct")
    task_lane(897, ["CAI-1997"], [("t-q-pct", "done")])

    assert _keys(index_module.page(q="%")) == []


def _forbidden(*args, **kwargs):
    raise AssertionError("this call must not happen on an index read")
