"""The spec on the web — read back, and edited in place.

`features.body` was write-only from the browser: the declare dialog collected
it, `POST /api/features` stored it, and nothing ever served it again. A spec
nobody can read is a spec nobody can check the build against, which is the one
thing the acceptance list is for.

An edit is a correction to the same claim, not a new one. It appends no
`feature_events` row, because rounds count *builds* — a typo fixed in the
prose is not a round, and a stage that moved because someone reworded a
sentence would make rounds-to-done measure typing.
"""

from __future__ import annotations

from sqlmodel import select

from lib.auth import create_token
from lib.features import store as feature_store
from lib.orm import SessionLocal
from lib.orm.models.features import FeatureEvent

EDITOR = {"Authorization": f"Bearer {create_token(2, 'ed', 'editor')}"}
VIEWER = {"Authorization": f"Bearer {create_token(3, 'vw', 'viewer')}"}

SPEC = "## Intent\nthree edges the data layer already has\n"


def _events(feature_id: int):
    with SessionLocal() as session:
        return session.exec(select(FeatureEvent).where(
            FeatureEvent.feature_id == feature_id)).all()


def test_the_ledger_carries_the_spec_it_was_declared_with(flask_client):
    """The field the declare dialog writes is the field the page reads."""
    row = feature_store.create("a workbench", body=SPEC)

    resp = flask_client.get(f"/api/features/{row.id}", headers=EDITOR)

    assert resp.status_code == 200, resp.get_json()
    assert resp.get_json()["body"] == SPEC


def test_the_ledger_carries_a_null_spec_as_null(flask_client):
    """Absent, not empty-string: "nobody wrote one" and "somebody wrote
    nothing" are different facts and the page renders them differently."""
    row = feature_store.create("no spec yet")

    body = flask_client.get(f"/api/features/{row.id}", headers=EDITOR).get_json()

    assert "body" in body
    assert body["body"] is None


def test_editing_the_spec_writes_it_back(flask_client):
    """PATCH stores it, returns the fresh ledger, and a re-read agrees."""
    row = feature_store.create("a workbench", body=SPEC)

    resp = flask_client.patch(f"/api/features/{row.id}",
                              json={"body": "## Intent\nrewritten"},
                              headers=EDITOR)

    assert resp.status_code == 200, resp.get_json()
    assert resp.get_json()["body"] == "## Intent\nrewritten"
    assert feature_store.require(row.id).body == "## Intent\nrewritten"
    assert flask_client.get(f"/api/features/{row.id}",
                            headers=EDITOR).get_json()["body"] == (
        "## Intent\nrewritten")


def test_a_spec_edit_appends_no_event(flask_client):
    """Rounds count builds. A reworded sentence is not one."""
    row = feature_store.create("a workbench", body=SPEC)
    before = [(e.kind, e.declared_by) for e in _events(row.id)]

    flask_client.patch(f"/api/features/{row.id}",
                       json={"body": "## Intent\nrewritten"}, headers=EDITOR)

    assert [(e.kind, e.declared_by) for e in _events(row.id)] == before


def test_editing_the_spec_is_editor_only(flask_client):
    """A viewer reading the ledger cannot rewrite what it was declared with."""
    row = feature_store.create("a workbench", body=SPEC)

    resp = flask_client.patch(f"/api/features/{row.id}",
                              json={"body": "not mine"}, headers=VIEWER)

    assert resp.status_code == 403
    assert feature_store.require(row.id).body == SPEC
