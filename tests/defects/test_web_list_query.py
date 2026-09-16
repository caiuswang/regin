"""The defect list's order, its bound, and its narrowings (regin:73, S5).

Four scopes used to be four readers: `all` came back `id DESC` and the other
three `id ASC`, so pressing a scope pill reversed the list with nothing on
screen saying so. Every scenario in the feature's acceptance table for the
defects API is one test here.

The N+1 test is the load-bearing one, and it is written as a *shape* assertion
rather than a threshold: a page of 3 and a page of 30 must cost the same
number of statements. A ceiling ("under 12 queries") would keep passing the
day somebody re-adds a per-row lookup to a short fixture.
"""

from __future__ import annotations

import pytest
from sqlalchemy import event

from lib.auth import create_token
from lib.defects import query as defect_query
from lib.defects import store
from lib.orm import SessionLocal, get_engine
from lib.orm.models.features import Feature
from lib.utils.pagination import MAX_PAGE_SIZE

EDITOR = {"Authorization": f"Bearer {create_token(2, 'ed', 'editor')}"}


def _ids(body) -> list[int]:
    return [item["id"] for item in body["items"]]


def _get(client, url):
    resp = client.get(url)
    assert resp.status_code == 200, resp.get_json()
    return resp.get_json()


def _open(n: int, *, prefix: str = "bug") -> list:
    return [store.record(f"{prefix} {i}", session_id=f"t-{i}")
            for i in range(n)]


def _project(feature_id: int, project_id):
    with SessionLocal() as session:
        row = session.get(Feature, feature_id)
        row.project_id = project_id
        session.add(row)
        session.commit()


# ── Ordering — the reported bug ───────────────────────────────


def test_defects_scope_switch_keeps_sort_direction(flask_client, feature_row):
    """Both scopes are newest-first, and both say so in the same words."""
    feature = feature_row()
    attributed = [store.record(f"attributed {i}", feature_id=feature.id)
                  for i in range(4)]
    loose = _open(4, prefix="loose")

    every = _get(flask_client, "/api/defects")
    unattributed = _get(flask_client, "/api/defects?scope=unattributed")

    expected = [r.id for r in reversed(loose + attributed)]
    assert _ids(every) == sorted(expected, reverse=True)
    assert _ids(unattributed) == [r.id for r in reversed(loose)]
    # The control reads the same in both — the whole defect was that it could
    # not, because the direction was never a parameter.
    assert every["sort"] == unattributed["sort"] == "opened:desc"


def test_every_scope_reads_the_same_direction(flask_client, feature_row,
                                              native_task):
    feature = feature_row()
    task_id = native_task(title="the fix")
    rows = [store.record(f"scoped {i}", feature_id=feature.id)
            for i in range(3)]
    for row in rows:
        store.set_fix_item(row.id, task_id)
    newest_first = [r.id for r in reversed(rows)]

    for url in ("/api/defects",
                f"/api/defects?feature_id={feature.id}",
                f"/api/defects?fix_item={task_id}"):
        body = _get(flask_client, url)
        assert _ids(body) == newest_first, url
        assert body["sort"] == "opened:desc", url


def test_an_unknown_sort_key_falls_back_instead_of_erroring(flask_client):
    rows = _open(3)

    body = _get(flask_client, "/api/defects?sort=whenever:asc")

    assert body["sort"] == "opened:desc"
    assert _ids(body) == [r.id for r in reversed(rows)]


@pytest.mark.parametrize("key", defect_query.SORTS)
def test_every_sort_key_is_a_total_order(flask_client, feature_row, key):
    """No row on two pages and none on neither, whatever the key ties on."""
    feature = feature_row()
    rows = _open(6) + [store.record(f"f {i}", feature_id=feature.id)
                       for i in range(6)]

    for direction in ("asc", "desc"):
        first = _get(flask_client,
                     f"/api/defects?sort={key}:{direction}&size=5&page=0")
        second = _get(flask_client,
                      f"/api/defects?sort={key}:{direction}&size=5&page=1")
        assert not set(_ids(first)) & set(_ids(second))
        assert len(_ids(first) + _ids(second)) == 10
    assert len(rows) == 12


def test_the_tiebreaker_follows_the_sort_direction(flask_client):
    """`opened_at` has second resolution, so a page filed in one second is
    ordered entirely by the tiebreaker."""
    rows = _open(5)

    ascending = _get(flask_client, "/api/defects?sort=opened:asc")

    assert _ids(ascending) == [r.id for r in rows]


# ── Pagination ────────────────────────────────────────────────


def test_defects_index_is_paginated(flask_client):
    opened = _open(120)

    body = _get(flask_client, "/api/defects?size=50")

    assert len(body["items"]) == 50
    assert body["pagination"]["total"] == len(opened) == 120
    assert body["pagination"]["has_next"] is True
    assert body["pagination"]["has_prev"] is False
    assert body["pagination"]["strategy"] == "offset"


def test_offset_pages_are_disjoint_and_contiguous(flask_client):
    _open(120)

    first = _ids(_get(flask_client, "/api/defects?size=50&page=0"))
    second = _ids(_get(flask_client, "/api/defects?size=50&page=1"))
    whole = _ids(_get(flask_client, "/api/defects?size=200"))

    assert not set(first) & set(second)
    assert first + second == whole[:100]


def test_size_above_max_is_clamped_not_rejected(flask_client):
    _open(MAX_PAGE_SIZE + 5)

    body = _get(flask_client, "/api/defects?size=100000")

    assert len(body["items"]) == MAX_PAGE_SIZE
    assert body["pagination"]["size"] == MAX_PAGE_SIZE


def test_a_page_past_the_end_is_empty_but_still_counts(flask_client):
    _open(5)

    body = _get(flask_client, "/api/defects?size=50&page=9")

    assert body["items"] == []
    assert body["pagination"]["total"] == 5
    assert body["pagination"]["has_next"] is False


# ── No per-row query ──────────────────────────────────────────


class _Counter:
    """Every statement the engine executed while it was armed."""

    def __init__(self):
        self.statements: list[str] = []

    def __call__(self, conn, cursor, statement, *_rest):
        self.statements.append(statement)


def _statements_for(client, url) -> int:
    counter = _Counter()
    engine = get_engine()
    event.listen(engine, "before_cursor_execute", counter)
    try:
        assert client.get(url).status_code == 200
    finally:
        event.remove(engine, "before_cursor_execute", counter)
    return len(counter.statements)


def test_no_list_figure_issues_a_per_row_query(flask_client, feature_row):
    """Invariant 8: the cost of the page does not scale with its rows.

    `feature_title` and `blamed_event_kind` are figures on every row. They
    used to cost `feature_store.get` plus a `SessionLocal()` of their own per
    row, on top of a query that returned the whole table.
    """
    feature = feature_row()
    for i in range(3):
        store.record(f"small {i}", feature_id=feature.id)
    small = _statements_for(flask_client, "/api/defects?size=200")

    for i in range(30):
        store.record(f"large {i}", feature_id=feature.id)
    large = _statements_for(flask_client, "/api/defects?size=200")

    assert large == small, (
        f"{large - small} extra statements for 30 more rows — the list grew "
        "a per-row query")


def test_the_list_still_renders_both_row_figures(flask_client, feature_row):
    feature = feature_row(title="the pipeline page")
    store.record("it reverses", feature_id=feature.id)

    item = _get(flask_client, f"/api/defects?feature_id={feature.id}")["items"][0]

    assert item["feature_title"] == "the pipeline page"
    assert item["blamed_event_kind"] is not None


# ── Status ────────────────────────────────────────────────────


def test_status_filter_answers_what_is_still_open(flask_client):
    rows = _open(4)
    store.close(rows[1].id, missing_check="a list test")

    still_open = _get(flask_client, "/api/defects?status=open")
    closed = _get(flask_client, "/api/defects?status=closed")

    assert rows[1].id not in _ids(still_open)
    assert _ids(closed) == [rows[1].id]
    assert still_open["pagination"]["total"] == 3


def test_an_unknown_status_is_refused_not_dropped(flask_client):
    _open(2)

    resp = flask_client.get("/api/defects?status=flapping")

    assert resp.status_code == 400
    assert "flapping" in resp.get_json()["error"]


def test_the_total_describes_the_filtered_population(flask_client):
    rows = _open(60)
    store.close(rows[0].id, missing_check="a list test")

    body = _get(flask_client, "/api/defects?status=open&size=10")

    assert len(body["items"]) == 10
    assert body["pagination"]["total"] == 59


# ── q over symptom ────────────────────────────────────────────


def test_q_narrows_over_the_symptom(flask_client):
    wanted = store.record("the gutter folds the newest span",
                          session_id="t-a")
    store.record("the pager loses its place", session_id="t-b")

    body = _get(flask_client, "/api/defects?q=gutter")

    assert _ids(body) == [wanted.id]
    assert body["pagination"]["total"] == 1


def test_q_treats_a_wildcard_as_a_literal(flask_client):
    literal = store.record("100% of the rows vanish", session_id="t-a")
    store.record("nothing vanishes", session_id="t-b")

    body = _get(flask_client, "/api/defects?q=100%25")

    assert _ids(body) == [literal.id]


# ── Project, derived through the feature ──────────────────────


def test_defect_project_follows_feature(flask_client, feature_row):
    feature = feature_row()
    _project(feature.id, 7)
    mine = store.record("in project 7", feature_id=feature.id)
    elsewhere = feature_row(title="another")
    _project(elsewhere.id, 9)
    store.record("in project 9", feature_id=elsewhere.id)

    body = _get(flask_client, "/api/defects?project=7")

    assert _ids(body) == [mine.id]

    _project(feature.id, None)
    assert _ids(_get(flask_client, "/api/defects?project=7")) == []


def test_unattributed_defect_has_no_project(flask_client, feature_row):
    feature = feature_row()
    _project(feature.id, 7)
    store.record("in project 7", feature_id=feature.id)
    loose = store.record("blamed on nobody", session_id="t-a")

    assert loose.id in _ids(_get(flask_client, "/api/defects"))
    assert _ids(_get(flask_client, "/api/defects?project=none")) == [loose.id]
    assert loose.id not in _ids(_get(flask_client, "/api/defects?project=7"))


def test_no_row_is_invisible_under_every_scope(flask_client, feature_row):
    """Invariant 5: `all` = `none` ⊎ the projects."""
    with_project = feature_row(title="placed")
    _project(with_project.id, 3)
    without = feature_row(title="unplaced")
    rows = [store.record("a", feature_id=with_project.id),
            store.record("b", feature_id=without.id),
            store.record("c", session_id="t-a")]

    everything = set(_ids(_get(flask_client, "/api/defects")))
    none = set(_ids(_get(flask_client, "/api/defects?project=none")))
    three = set(_ids(_get(flask_client, "/api/defects?project=3")))

    assert everything == {r.id for r in rows}
    assert none | three == everything
    assert not none & three


def test_an_unknown_project_is_refused(flask_client):
    resp = flask_client.get("/api/defects?project=everything")

    assert resp.status_code == 400


# ── The store keeps its semantics ─────────────────────────────


def test_the_list_adds_no_defect_semantics(flask_client, feature_row):
    """A read is a read: dedup, write-once blame and the missing-check
    requirement stay where the store enforces them."""
    feature = feature_row()
    first = store.record("one symptom", feature_id=feature.id)
    again = store.record("ONE   symptom", feature_id=feature.id)

    assert again.id == first.id
    with pytest.raises(store.DefectError):
        store.close(first.id, missing_check="   ")
    assert _ids(_get(flask_client, "/api/defects")) == [first.id]
