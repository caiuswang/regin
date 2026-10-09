"""Feature links — F155 R4 and I4: `follows` / `related` between features."""

from __future__ import annotations

import pytest
from sqlmodel import select
from typer.testing import CliRunner

from cli.app import app
from lib.auth import create_token
from lib.features import links, store
from lib.orm import SessionLocal
from lib.orm.models.features import FeatureLink

EDITOR = {"Authorization": f"Bearer {create_token(2, 'ed', 'editor')}"}
VIEWER = {"Authorization": f"Bearer {create_token(3, 'vw', 'viewer')}"}
runner = CliRunner()


def _rows() -> list[tuple[int, int, str]]:
    with SessionLocal() as session:
        return sorted((r.feature_id, r.other_id, r.kind)
                      for r in session.exec(select(FeatureLink)).all())


@pytest.fixture
def three(tmp_db, feature_row):
    return [feature_row(f"phase {n}").id for n in (1, 2, 3)]


def test_feature_link_rules(three):
    a, b, c = three

    with pytest.raises(links.LinkRefused, match="cannot link to itself"):
        links.link(a, a, "follows")
    with pytest.raises(links.LinkRefused, match="no feature #999"):
        links.link(a, 999, "related")

    links.link(c, a, "related")
    links.link(a, c, "related")
    assert _rows() == [(a, c, "related")]

    links.link(b, a, "follows")
    links.link(c, b, "follows")
    first = links.link(c, b, "follows")
    assert first.feature_id == c and first.other_id == b
    with pytest.raises(links.LinkRefused, match="would make a follows cycle"):
        links.link(a, c, "follows")
    with pytest.raises(links.LinkRefused, match="would make a follows cycle"):
        links.link(b, c, "follows")

    assert _rows() == [(a, c, "related"), (b, a, "follows"), (c, b, "follows")]


def test_unlink_is_idempotent(three):
    a, b, _ = three
    links.link(a, b, "related")

    assert links.unlink(b, a, "related") is True
    assert links.unlink(b, a, "related") is False
    assert _rows() == []


def test_unknown_kind_is_refused(three):
    with pytest.raises(links.LinkRefused) as exc:
        links.link(three[0], three[1], "parent")
    assert exc.value.field == "kind"


def test_ledger_carries_links_both_ways(flask_client, three):
    a, b, c = three
    links.link(b, a, "follows")
    links.link(c, b, "follows")
    links.link(b, c, "related")

    body = flask_client.get(f"/api/features/{b}", headers=VIEWER).get_json()

    assert [r["id"] for r in body["links"]["follows"]] == [a]
    assert [r["id"] for r in body["links"]["followed_by"]] == [c]
    assert body["links"]["related"] == [
        {"id": c, "title": "phase 3", "stage": "spec"}]


def test_link_api_writes_and_deletes(flask_client, three):
    a, b, _ = three

    resp = flask_client.post(f"/api/features/{b}/links",
                             json={"other_id": a, "kind": "follows"},
                             headers=EDITOR)
    assert resp.status_code == 201, resp.get_json()
    assert [r["id"] for r in resp.get_json()["links"]["follows"]] == [a]

    refused = flask_client.post(f"/api/features/{a}/links",
                                json={"other_id": b, "kind": "follows"},
                                headers=EDITOR)
    assert refused.status_code == 400
    assert refused.get_json()["error"] == "would make a follows cycle"

    gone = flask_client.delete(f"/api/features/{b}/links/{a}?kind=follows",
                               headers=EDITOR)
    assert gone.status_code == 200
    assert gone.get_json()["links"]["follows"] == []
    assert _rows() == []


def test_viewer_cannot_link(flask_client, three):
    a, b, _ = three
    post = flask_client.post(f"/api/features/{a}/links",
                             json={"other_id": b, "kind": "related"},
                             headers=VIEWER)
    delete = flask_client.delete(f"/api/features/{a}/links/{b}?kind=related",
                                 headers=VIEWER)
    assert (post.status_code, delete.status_code) == (403, 403)


def test_new_follow_up_links_feature(flask_client, three):
    earlier = three[1]

    resp = flask_client.post("/api/features",
                             json={"title": "next phase", "follows": earlier},
                             headers=EDITOR)

    assert resp.status_code == 201, resp.get_json()
    new_id = resp.get_json()["id"]
    ledger = flask_client.get(f"/api/features/{new_id}", headers=VIEWER)
    assert [r["id"] for r in ledger.get_json()["links"]["follows"]] == [earlier]
    assert _rows() == [(new_id, earlier, "follows")]


def test_follow_up_of_unknown_feature_creates_nothing(flask_client, three):
    before = len(store.population())

    resp = flask_client.post("/api/features",
                             json={"title": "orphan", "follows": 999},
                             headers=EDITOR)

    assert resp.status_code == 400
    assert resp.get_json() == {"error": "no feature #999", "field": "follows"}
    assert len(store.population()) == before


def test_index_rows_carry_follows(flask_client, three):
    a, b, _ = three
    links.link(b, a, "follows")

    items = flask_client.get("/api/features", headers=VIEWER).get_json()["items"]

    by_id = {row["id"]: row["follows"] for row in items}
    assert by_id[b] == [a]
    assert by_id[a] == []


def test_cli_link_unlink_and_new_follows(three):
    a, b, _ = three

    linked = runner.invoke(app, ["feature", "link", str(b), str(a),
                                 "--kind", "follows"])
    assert linked.exit_code == 0, linked.output
    cycle = runner.invoke(app, ["feature", "link", str(a), str(b),
                                "--kind", "follows"])
    assert cycle.exit_code == 1
    assert "would make a follows cycle" in cycle.output

    minted = runner.invoke(app, ["feature", "new", "phase 4", "--follows",
                                 str(b)])
    assert minted.exit_code == 0, minted.output
    new_id = int(minted.output.split()[0].lstrip("#"))
    assert (new_id, b, "follows") in _rows()

    unlinked = runner.invoke(app, ["feature", "unlink", str(b), str(a),
                                   "--kind", "follows"])
    assert unlinked.exit_code == 0
    assert (b, a, "follows") not in _rows()


def test_follow_up_with_a_non_integer_id_names_follows(flask_client, three):
    resp = flask_client.post("/api/features",
                             json={"title": "bad", "follows": "2"},
                             headers=EDITOR)
    assert resp.get_json() == {"error": "follows must be a feature id",
                               "field": "follows"}
