"""`POST /api/features` — the browser's own way to declare a feature.

The endpoint predates any control that calls it: features were declared on the
command line, so the page that lists them had no way to add one, and the
natural act in the browser became "capture a task" — which is not the entry
point. A task under no feature has runs that count toward no round and defects
with nothing to blame.
"""

from __future__ import annotations

from sqlmodel import select

from lib.auth import create_token
from lib.features import events
from lib.orm import SessionLocal
from lib.orm.models.features import Feature, FeatureEvent

EDITOR = {"Authorization": f"Bearer {create_token(2, 'ed', 'editor')}"}
VIEWER = {"Authorization": f"Bearer {create_token(3, 'vw', 'viewer')}"}


def _features():
    with SessionLocal() as session:
        return session.exec(select(Feature)).all()


def test_web_create_declares_a_native_feature(flask_client):
    """One act, two tables: the row and the `specced` event that dates it."""
    resp = flask_client.post("/api/features",
                             json={"title": "declare from the browser",
                                   "body": "## Intent\nthin prose"},
                             headers=EDITOR)

    assert resp.status_code == 201, resp.get_json()
    body = resp.get_json()
    assert body["title"] == "declare from the browser"
    assert body["stage"]["stage"] == events.STAGE_SPEC
    assert body["origin"] == "native"

    rows = _features()
    assert len(rows) == 1
    assert rows[0].id == body["id"]
    with SessionLocal() as session:
        log = session.exec(select(FeatureEvent).where(
            FeatureEvent.feature_id == rows[0].id)).all()
    assert [(e.kind, e.declared_by) for e in log] == [
        (events.SPECCED, events.OPERATOR)]


def test_web_create_refuses_an_empty_title(flask_client):
    """Refused by name, and nothing written — a titleless row is a feature
    no list can render and no operator can name."""
    resp = flask_client.post("/api/features", json={"title": "   "},
                             headers=EDITOR)

    assert resp.status_code == 400
    assert "title" in resp.get_json()["error"]
    assert _features() == []


def test_web_create_is_editor_only(flask_client):
    """A viewer reading the ledger cannot add to the population."""
    resp = flask_client.post("/api/features", json={"title": "not mine"},
                             headers=VIEWER)

    assert resp.status_code == 403
    assert _features() == []
