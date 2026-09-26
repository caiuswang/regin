"""Bounding the feature index: one page, one order, one refusal.

The index used to return every row in `features` under a single `q`. Three
things follow from that and none of them is a nicety: a 2 000-feature install
shipped 2 000 rows on mount, the operator had no way to ask "what is in
flight", and there was nothing to sort by — so the page could only ever be
read from the top.

What is pinned here is the *contract*, not the plumbing. A page is bounded and
its bound is clamped rather than refused; two adjacent pages are disjoint and
together are exactly the ordering, which is only true because every sort ends
in `id`; a facet value the tab does not know is a 400 naming the parameter,
while a sort key it does not know is the default sort — the split
`lib/tasks/query.py` already draws, and the reason for it is that a dropped
filter shows a list disagreeing with its own controls where a dropped sort
shows the same rows in another order.
"""

from __future__ import annotations

import pytest

from lib.auth import create_token
from lib.features import events, index as index_module, store as feature_store
from lib.utils.pagination import DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE


def _headers(role: str = "viewer") -> dict:
    return {"Authorization": f"Bearer {create_token(1, role, role)}"}


def _ids(payload) -> list:
    return [row["id"] for row in payload["items"]]


@pytest.fixture
def many_features():
    """More features than one default page holds."""
    def _make(count: int) -> list:
        return [feature_store.create(f"feature {n:03d}").id
                for n in range(count)]
    return _make


# ── the page is bounded ──────────────────────────────────────

def test_features_index_is_paginated(flask_client, tmp_db, many_features):
    """The acceptance for the whole slice: the response is an offset page,
    and its size is the module's default rather than the population."""
    many_features(DEFAULT_PAGE_SIZE + 7)

    body = flask_client.get("/api/features", headers=_headers()).get_json()

    assert body["pagination"]["strategy"] == "offset"
    assert len(body["items"]) == DEFAULT_PAGE_SIZE
    assert body["pagination"]["total"] == DEFAULT_PAGE_SIZE + 7
    assert body["pagination"]["has_next"] is True
    assert body["pagination"]["has_prev"] is False


def test_offset_pages_are_disjoint_and_contiguous(flask_client, tmp_db,
                                                  many_features):
    """Invariant 3. Two pages share no row and skip none between them, which
    holds only because the ordering is total — every sort ends in `id`, so no
    two rows can tie and swap places as the offset moves."""
    made = many_features(11)

    pages = [flask_client.get(f"/api/features?size=4&page={n}",
                              headers=_headers()).get_json() for n in range(3)]
    seen = [i for payload in pages for i in _ids(payload)]

    assert [len(p["items"]) for p in pages] == [4, 4, 3]
    assert len(set(seen)) == len(seen)
    assert sorted(seen) == sorted(made)


def test_a_tie_on_the_sort_key_still_pages_without_loss(flask_client, tmp_db,
                                                        many_features):
    """Every row here has the same round count, so `rounds` alone cannot
    order them — the `id` tiebreaker is the only thing keeping a row off two
    pages at once."""
    made = many_features(9)

    seen = [i for n in range(3) for i in _ids(flask_client.get(
        f"/api/features?sort=rounds:desc&size=3&page={n}",
        headers=_headers()).get_json())]

    assert sorted(seen) == sorted(made)


def test_size_above_max_is_clamped_not_rejected(flask_client, tmp_db,
                                                many_features):
    """A rogue `size` is a bound to apply, not a request to refuse: refusing
    leaves an operator on an error page over a link that merely asked for too
    much."""
    many_features(3)

    resp = flask_client.get(f"/api/features?size={MAX_PAGE_SIZE * 50}",
                            headers=_headers())

    assert resp.status_code == 200
    assert resp.get_json()["pagination"]["size"] == MAX_PAGE_SIZE


def test_an_offset_past_the_end_still_reports_the_true_total(
        flask_client, tmp_db, many_features):
    """R2: empty `items`, and `total` unchanged. A pager that reads "page 9
    of 1" is how someone gets stranded with no way back."""
    many_features(3)

    body = flask_client.get("/api/features?size=5&page=9",
                            headers=_headers()).get_json()

    assert body["items"] == []
    assert body["pagination"]["total"] == 3
    assert body["pagination"]["has_prev"] is True
    assert body["pagination"]["has_next"] is False


def test_the_population_counter_still_describes_the_scope(
        flask_client, tmp_db, many_features):
    """`population` and `pagination.total` answer two questions — how many features
    there are, and how many this query matched. One number cannot."""
    many_features(6)

    body = flask_client.get("/api/features?size=2&q=feature 001",
                            headers=_headers()).get_json()

    assert body["population"] == 6
    assert body["pagination"]["total"] == 1


# ── the refusal line ─────────────────────────────────────────

def test_unknown_facet_value_is_refused_not_dropped(flask_client, tmp_db,
                                                    many_features):
    """A silently dropped filter shows a list that does not match the
    controls above it. The 400 names the parameter so the surface can say
    which control it cannot honour."""
    many_features(2)

    resp = flask_client.get("/api/features?stage=in-flight",
                            headers=_headers())

    assert resp.status_code == 400
    assert resp.get_json()["field"] == "stage"


def test_a_non_numeric_project_is_refused_by_name(flask_client, tmp_db):
    resp = flask_client.get("/api/features?project_id=regin",
                            headers=_headers())

    assert resp.status_code == 400
    assert resp.get_json()["field"] == "project_id"


def test_unknown_sort_key_falls_back_to_the_default(flask_client, tmp_db,
                                                    many_features):
    """The other half of the line: a sort is the same rows in another order,
    so a link naming a column this tab dropped still shows the list."""
    many_features(3)

    resp = flask_client.get("/api/features?sort=nonsense:sideways",
                            headers=_headers())
    default = flask_client.get("/api/features", headers=_headers())

    assert resp.status_code == 200
    assert _ids(resp.get_json()) == _ids(default.get_json())


# ── sorting ──────────────────────────────────────────────────

def test_every_declared_sort_key_orders_the_page(flask_client, tmp_db,
                                                 many_features):
    made = many_features(3)

    for key in index_module.SORT_KEYS:
        for direction in ("asc", "desc"):
            body = flask_client.get(f"/api/features?sort={key}:{direction}",
                                    headers=_headers()).get_json()
            assert sorted(_ids(body)) == sorted(made), (key, direction)


def test_created_descending_puts_the_newest_first(flask_client, tmp_db,
                                                  many_features):
    """`created_at` has second resolution, so a test that made four rows in
    one tick would be reading the `id` tiebreaker and calling it a sort."""
    from lib.orm import SessionLocal
    from lib.orm.models.features import Feature

    made = many_features(4)
    with SessionLocal() as session:
        for offset_days, feature_id in enumerate(made):
            row = session.get(Feature, feature_id)
            row.created_at = f"2026-01-0{offset_days + 1} 00:00:00"
            session.add(row)
        session.commit()

    body = flask_client.get("/api/features?sort=created:desc",
                            headers=_headers()).get_json()

    assert _ids(body) == list(reversed(made))


def test_the_id_tiebreaker_is_what_makes_a_tied_sort_total(
        flask_client, tmp_db, many_features):
    """Four rows created in one tick tie on `created_at`. The page still has
    one order, and it is the same order on every read — which is the whole
    requirement, since a tie that resolved differently per request would put
    a row on two pages."""
    many_features(4)

    reads = [_ids(flask_client.get("/api/features?sort=created:desc",
                                   headers=_headers()).get_json())
             for _ in range(3)]

    assert reads[0] == reads[1] == reads[2]
    assert len(set(reads[0])) == 4


def test_sorting_by_stage_follows_the_lifecycle_not_the_alphabet(
        flask_client, tmp_db, feature_row):
    """Ordered by the string, the column reads abandoned, building, escaped,
    shipped, spec — five names in an order nobody can name."""
    building = feature_row("b", stage=events.BUILD_STARTED)
    shipped = feature_row("s", stage=events.SHIPPED)
    spec = feature_row("p")

    body = flask_client.get("/api/features?sort=stage:asc",
                            headers=_headers()).get_json()

    assert _ids(body) == [spec.id, building.id, shipped.id]


def test_a_null_cost_sorts_last_in_both_directions(session_row, task_lane,
                                                   feature_row):
    """`none declared` is not the cheapest. An ORDER BY that ranks a NULL as
    $0.00 answers a question nobody asked of that row."""
    priced = feature_row("priced")
    session_row("t-cost-1", cost=9.0)
    task_lane(910, priced.id, [("t-cost-1", "done")])
    gap = feature_row("no cost")

    for direction in ("asc", "desc"):
        page = index_module.page(filters={"sort": f"cost:{direction}"})
        assert [row.id for row in page.items] == [priced.id, gap.id], direction


def test_created_is_the_default_so_a_new_feature_leads(session_row, task_lane,
                                                       feature_row):
    """Defect #179: under a last-activity default, a feature declared a moment
    ago sank below every older feature that had run a session since."""
    busy = feature_row("older, with a session")
    session_row("t-busy-1", cost=1.0, started="2099-01-01T00:00:00")
    task_lane(911, busy.id, [("t-busy-1", "done")])
    fresh = feature_row("just declared")

    default = index_module.page()
    named = index_module.page(filters={"sort": "created:desc"})

    assert default.items[0].id == fresh.id
    assert [row.id for row in default.items] == [row.id for row in named.items]


def test_same_second_features_list_newest_id_first(feature_row):
    """`created_at` has second resolution; the tiebreak must not undo the
    newest-first promise for two features declared in one tick."""
    made = [feature_row(f"f{n}").id for n in range(3)]

    assert [row.id for row in index_module.page().items] == list(reversed(made))


# ── the new facets ───────────────────────────────────────────

def test_stage_filters_to_what_is_in_flight(flask_client, tmp_db,
                                            feature_row):
    """The question the page could not ask at all before this."""
    building = feature_row("building", stage=events.BUILD_STARTED)
    feature_row("still a spec")
    feature_row("shipped", stage=events.SHIPPED)

    body = flask_client.get("/api/features?stage=building",
                            headers=_headers()).get_json()

    assert _ids(body) == [building.id]


def test_stage_filters_on_the_escape_question_too(flask_client, tmp_db,
                                                  feature_row, defect_row):
    """`escaped` is not an event kind — it is `shipped` plus a blamed defect,
    so filtering by it proves the stage is computed in the statement rather
    than read off a column that does not exist."""
    escaped = feature_row("escaped", stage=events.SHIPPED)
    defect_row(escaped.id)
    feature_row("clean", stage=events.SHIPPED)

    body = flask_client.get("/api/features?stage=escaped",
                            headers=_headers()).get_json()

    assert _ids(body) == [escaped.id]


def test_project_scope_is_declared_never_inferred(flask_client, tmp_db,
                                                  feature_row):
    """A feature's project is a column an operator set. `project_id=none` is
    a real scope — a feature in no project is visible under it, never hidden
    from every scope (invariant 5)."""
    from lib.orm import SessionLocal
    from lib.orm.models.features import Feature

    scoped = feature_row("in a project")
    loose = feature_row("in no project")
    with SessionLocal() as session:
        row = session.get(Feature, scoped.id)
        row.project_id = 42
        session.add(row)
        session.commit()

    everything = flask_client.get("/api/features", headers=_headers())
    named = flask_client.get("/api/features?project_id=42", headers=_headers())
    unscoped = flask_client.get("/api/features?project_id=none",
                                headers=_headers())

    assert sorted(_ids(everything.get_json())) == sorted([scoped.id, loose.id])
    assert _ids(named.get_json()) == [scoped.id]
    assert _ids(unscoped.get_json()) == [loose.id]


def test_q_reaches_the_spec_body(flask_client, tmp_db):
    """`q` matched title and `external_ref` only, so the one place a feature
    is actually described was unsearchable."""
    feature_store.create("opaque title",
                         body="the pipeline pages every tab")
    feature_store.create("another")

    body = flask_client.get("/api/features?q=pipeline pages",
                            headers=_headers()).get_json()

    assert [row["title"] for row in body["items"]] == ["opaque title"]


def test_a_filtered_page_is_still_one_query(many_features, counted_queries):
    """Invariant 8 survives the facets: the filters and the matched count ride
    the page's own statement, never a read per row."""
    many_features(8)
    with counted_queries() as narrow:
        index_module.page(filters={"stage": "spec", "sort": "cost:asc"},
                          size=4)
    with counted_queries() as plain:
        index_module.page(size=4)

    assert narrow.value == plain.value
