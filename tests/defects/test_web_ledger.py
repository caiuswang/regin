"""The defect ledger's reads and writes on the web (regin:81).

`POST /api/defects` shipped the open verb alone, which left the ledger
write-only on the browser: a defect's text was readable only through
`regin defects list`, so the `/features` unattributed counter pointed at a
remedy no page offered. These are the routes that earn the list.

Every refusal asserted here is the store's, not the route's — the blueprint
holds no `SessionLocal()` and adds no rule, it only maps `DefectError` onto a
status code. Each test is one Gherkin scenario from the feature's acceptance
list.
"""

from __future__ import annotations

import pytest
from sqlmodel import select

from lib.auth import create_token
from lib.defects import store
from lib.orm import SessionLocal
from lib.orm.models.defects import Defect

EDITOR = {"Authorization": f"Bearer {create_token(2, 'ed', 'editor')}"}
VIEWER = {"Authorization": f"Bearer {create_token(3, 'vw', 'viewer')}"}

#: Exactly the `DefectView` shape. Asserted as a set so a field nobody
#: designed — a suggested feature key above all — cannot arrive unnoticed.
VIEW_FIELDS = {
    "id", "feature_key", "symptom", "status", "round", "blamed_against_state",
    "missing_check", "suspect_paths", "fix_provider", "fix_external_key",
    "opened_at", "closed_at",
}


def _row(defect_id: int) -> Defect:
    with SessionLocal() as session:
        return session.get(Defect, defect_id)


def _all_rows():
    with SessionLocal() as session:
        return session.exec(select(Defect)).all()


# ── 1.1 / 1.2 — the list ──────────────────────────────────────


def test_the_unattributed_list_is_readable_from_the_browser(flask_client):
    """1.1 — every unattributed defect, with its full symptom, status, round."""
    long_symptom = "the gutter folds the newest span " * 6
    opened = [store.record(long_symptom + str(n), session_id=f"t-{n}")
              for n in range(8)]
    store.close(opened[2].id, missing_check="a test")

    resp = flask_client.get("/api/defects?scope=unattributed")

    assert resp.status_code == 200, resp.get_json()
    body = resp.get_json()
    assert body["scope"] == "unattributed"
    assert [item["id"] for item in body["items"]] == [r.id for r in opened]
    first = body["items"][0]
    assert set(first) == VIEW_FIELDS
    assert first["symptom"] == opened[0].symptom  # verbatim, never truncated
    assert first["status"] == "open" and first["round"] == 1
    assert [i["status"] for i in body["items"]][2] == "closed"


def test_the_counter_and_the_list_report_the_same_number(flask_client):
    """1.2 — `/features`' counter and `/defects` read one function."""
    for n in range(3):
        store.record(f"symptom {n}", session_id=f"t-{n}")

    counter = flask_client.get("/api/features").get_json()
    listed = flask_client.get("/api/defects?scope=unattributed").get_json()

    assert counter["unattributed_defects"] == len(listed["items"]) == 3


def test_a_feature_scope_lists_that_features_defects(flask_client,
                                                     native_task):
    key = f"regin:{native_task()}"
    mine = store.record("mine broke", feature_key=key)
    store.record("something else broke", session_id="t-1")

    body = flask_client.get(f"/api/defects?feature={key}").get_json()

    assert body["scope"] == "feature"
    assert [i["id"] for i in body["items"]] == [mine.id]


def test_a_fix_item_scope_lists_every_defect_one_item_should_close(
        flask_client):
    first = store.record("one", session_id="t-1")
    second = store.record("two", session_id="t-2")
    store.record("three", session_id="t-3")
    for row in (first, second):
        store.set_fix_item(row.id, "linear", "CAI-9")

    body = flask_client.get("/api/defects?fix_item=linear:CAI-9").get_json()

    assert body["scope"] == "fix-item"
    assert [i["id"] for i in body["items"]] == [first.id, second.id]
    assert body["items"][0]["fix_external_key"] == "CAI-9"


def test_an_unparseable_fix_item_is_a_bad_request(flask_client):
    """A string no tracker recognizes and that carries no `<provider>:<key>`
    shape names nothing. `CAI-9` is no longer one of these: the route now
    accepts every spelling the WRITE accepts."""
    assert flask_client.get("/api/defects?fix_item=nonsense").status_code == 400


def test_the_default_scope_is_the_whole_ledger(flask_client, native_task):
    """The repair: a defect attributed at the moment it was reported used to
    belong to no scope any surface opened, so the page that filed it said
    saved and then could not show it."""
    key = f"regin:{native_task()}"
    blamed = store.record("filed from the task page", feature_key=key)
    loose = store.record("filed by nobody", session_id="t-1")

    body = flask_client.get("/api/defects").get_json()

    assert body["scope"] == "all"
    assert {i["id"] for i in body["items"]} == {blamed.id, loose.id}
    assert [i["id"] for i in body["items"]] == [loose.id, blamed.id]


def test_the_two_ledger_wide_scopes_partition_the_table(flask_client,
                                                        native_task):
    key = f"regin:{native_task()}"
    store.record("blamed", feature_key=key)
    store.record("unblamed", session_id="t-1")

    every = flask_client.get("/api/defects").get_json()["items"]
    loose = flask_client.get(
        "/api/defects?scope=unattributed").get_json()["items"]
    blamed = flask_client.get(f"/api/defects?feature={key}").get_json()["items"]

    assert len(every) == len(loose) + len(blamed)
    assert {i["id"] for i in every} == {i["id"] for i in loose + blamed}


def test_an_unknown_scope_is_a_bad_request(flask_client):
    assert flask_client.get("/api/defects?scope=closed").status_code == 400


def test_anonymous_cannot_read_the_ledger(anon_client):
    assert anon_client.get("/api/defects").status_code == 401


# ── 2.1 / 2.2 — attribution ───────────────────────────────────


def test_the_list_never_suggests_a_feature_key(flask_client):
    """2.1 — an unattributed row carries its paths and no proposed parent."""
    store.record("cost is inverted", session_id="t-1",
                 suspect_paths=["lib/features/cost.py"])

    item = flask_client.get("/api/defects").get_json()["items"][0]

    assert item["feature_key"] is None
    assert item["suspect_paths"] == ["lib/features/cost.py"]
    assert set(item) == VIEW_FIELDS


def test_attributing_binds_the_feature_and_freezes_the_blame_state(
        flask_client, native_task):
    key = f"regin:{native_task()}"
    row = store.record("it broke", session_id="t-1")

    resp = flask_client.post(f"/api/defects/{row.id}/attribute",
                             json={"feature_key": f"  {key} "},
                             headers=EDITOR)

    assert resp.status_code == 200, resp.get_json()
    assert resp.get_json()["defect"]["feature_key"] == key
    assert _row(row.id).feature_key == key


def test_attribution_with_a_blank_key_is_refused(flask_client):
    row = store.record("it broke", session_id="t-1")

    resp = flask_client.post(f"/api/defects/{row.id}/attribute",
                             json={"feature_key": "   "}, headers=EDITOR)

    assert resp.status_code == 400
    assert "feature key" in resp.get_json()["error"]
    assert _row(row.id).feature_key is None


def test_attributing_a_symptom_the_feature_already_reports_is_refused(
        flask_client, native_task):
    """2.2 — the collision names `merge` and absorbs nothing."""
    key = f"regin:{native_task()}"
    store.record("it broke", feature_key=key)
    mine = store.record("It   Broke ", session_id="t-1")

    resp = flask_client.post(f"/api/defects/{mine.id}/attribute",
                             json={"feature_key": key}, headers=EDITOR)

    assert resp.status_code == 409
    assert "merge" in resp.get_json()["error"]
    assert _row(mine.id).feature_key is None


def test_attributing_an_unknown_defect_is_a_404(flask_client, native_task):
    resp = flask_client.post("/api/defects/9999/attribute",
                             json={"feature_key": f"regin:{native_task()}"},
                             headers=EDITOR)
    assert resp.status_code == 404


# ── 3.1 / 3.2 — close and reopen ──────────────────────────────


def test_closing_names_the_check_that_would_have_caught_it(flask_client):
    row = store.record("it broke", session_id="t-1")

    resp = flask_client.post(f"/api/defects/{row.id}/close",
                             json={"missing_check": " a route test "},
                             headers=EDITOR)

    assert resp.status_code == 200, resp.get_json()
    body = resp.get_json()["defect"]
    assert body["status"] == "closed" and body["missing_check"] == "a route test"
    assert _row(row.id).missing_check == "a route test"


def test_closing_with_an_empty_check_is_refused(flask_client):
    """3.1 — the check is the only output that changes future behaviour."""
    row = store.record("it broke", session_id="t-1")

    resp = flask_client.post(f"/api/defects/{row.id}/close",
                             json={"missing_check": "  "}, headers=EDITOR)

    assert resp.status_code == 400
    assert _row(row.id).status == "open"
    assert _row(row.id).missing_check is None


def test_reopening_a_closed_defect_moves_the_round_and_clears_the_check(
        flask_client):
    row = store.record("it broke", session_id="t-1")
    store.close(row.id, missing_check="a route test")

    resp = flask_client.post(f"/api/defects/{row.id}/reopen", headers=EDITOR)

    assert resp.status_code == 200, resp.get_json()
    body = resp.get_json()["defect"]
    assert body["status"] == "open" and body["round"] == 2
    assert body["missing_check"] is None and body["closed_at"] is None


def test_reopening_an_open_defect_does_not_move_the_round(flask_client):
    """3.2 — a round counts fix attempts walked back, not ones never made."""
    row = store.record("it broke", session_id="t-1")

    resp = flask_client.post(f"/api/defects/{row.id}/reopen", headers=EDITOR)

    assert resp.status_code == 409
    assert _row(row.id).round == 1
    assert _row(row.id).status == "open"


def test_closing_or_reopening_an_unknown_defect_is_a_404(flask_client):
    assert flask_client.post("/api/defects/9999/close",
                             json={"missing_check": "x"},
                             headers=EDITOR).status_code == 404
    assert flask_client.post("/api/defects/9999/reopen",
                             headers=EDITOR).status_code == 404


# ── 4.1 — the guard ───────────────────────────────────────────


def _write_acts(defect_id: int, key: str):
    return ((f"/api/defects/{defect_id}/attribute", {"feature_key": key}),
            (f"/api/defects/{defect_id}/close", {"missing_check": "x"}),
            (f"/api/defects/{defect_id}/reopen", {}))


def test_a_viewer_cannot_attribute_close_or_reopen(flask_client, native_task):
    """4.1 — every write takes `require_editor`, and none of them lands."""
    key = f"regin:{native_task()}"
    row = store.record("it broke", session_id="t-1")
    store.close(row.id, missing_check="a route test")
    before = len(_all_rows())

    for path, payload in _write_acts(row.id, key):
        assert flask_client.post(path, json=payload,
                                 headers=VIEWER).status_code == 403

    after = _row(row.id)
    assert len(_all_rows()) == before
    assert after.feature_key is None
    assert after.status == "closed" and after.round == 1


def test_anonymous_cannot_attribute_close_or_reopen(anon_client):
    for path, payload in _write_acts(1, "regin:1"):
        assert anon_client.post(path, json=payload).status_code == 401


# ── the key is identity, so it is normalized at the one writer ─────


def test_a_padded_feature_key_is_stored_stripped(flask_client, native_task):
    """`' CAI-206'` is a key `for_feature('CAI-206')` cannot find and a
    SECOND feature in the index's population — the same row counted on a key
    nobody can spell."""
    key = f"regin:{native_task()}"
    opened = store.record("padded", feature_key=f"  {key} ")

    assert _row(opened.id).feature_key == key
    assert [r.id for r in store.for_feature(key)] == [opened.id]


def test_a_blank_feature_key_is_unattributed_not_an_empty_string(flask_client):
    """An empty string belongs to no scope: `unattributed()` filters on NULL,
    so the row was in neither list while `/features` grew a nameless key."""
    opened = store.record("blank", feature_key="   ", session_id="t-1")

    assert _row(opened.id).feature_key is None
    assert opened.id in {r.id for r in store.unattributed()}


def test_the_symptom_cap_the_model_declares_is_enforced(flask_client):
    """`SYMPTOM_MAX` was declared and referenced nowhere — a claim about the
    data that the bare TEXT column did not make true."""
    with pytest.raises(store.DefectError, match="symptom"):
        store.record("x" * 4001, session_id="t-1")

    assert store.record("x" * 4000, session_id="t-1").id  # the cap itself fits


def test_the_feature_key_cap_the_model_declares_is_enforced(flask_client):
    with pytest.raises(store.DefectError, match="feature key"):
        store.record("fine", feature_key="k" * 201)


# ── the fix-item scope accepts what the write accepted ─────


def test_a_fix_item_is_found_by_any_spelling_the_write_accepts(flask_client):
    """The writer stores the tracker's canonical key, so a query that reads
    the raw text answers "nothing points at this item" for rows that do."""
    row = store.record("one", session_id="t-1")
    store.set_fix_item(row.id, "linear", "CAI-241")

    for spelling in ("linear:CAI-241", "CAI-241",
                     "https://linear.app/acme/issue/CAI-241/some-slug"):
        body = flask_client.get(f"/api/defects?fix_item={spelling}").get_json()
        assert [i["id"] for i in body["items"]] == [row.id], spelling


def test_a_native_fix_item_is_found_by_its_natural_spelling(flask_client,
                                                            native_task):
    """A native key contains a colon of its own, so partitioning on the first
    one read `regin:42` as provider `regin`, key `42`."""
    task_id = native_task()
    row = store.record("two", session_id="t-2")
    store.set_fix_item(row.id, "regin", f"regin:{task_id}")

    body = flask_client.get(
        f"/api/defects?fix_item=regin:{task_id}").get_json()

    assert [i["id"] for i in body["items"]] == [row.id]
