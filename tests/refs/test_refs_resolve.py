"""`GET /api/refs/resolve`: live items, nulls, and refusals."""

from __future__ import annotations

from urllib.parse import parse_qs, urlsplit

from lib.features import store as feature_store
from lib.refs.grammar import encode_file_key
from tests.refs.conftest import ANON, EDITOR, VIEWER


def _resolve(client, *refs, headers=EDITOR, **params):
    response = client.get("/api/refs/resolve", headers=headers,
                          query_string={"ref": list(refs), **params})
    assert response.status_code == 200, response.get_json()
    return response.get_json()["refs"]


def test_resolve_returns_live_item_or_null(flask_client, project, git_repo,
                                          feature, task, defect, lesson):
    repo = project("p", git_repo("p", tracked=["docs/a b.md"]))
    renamed = feature("Old name", project=repo)
    feature_store.rename(renamed.id, "Alpha")
    step = task("Gamma", project=repo, feature_id=renamed.id)
    bug = defect("Delta broke", feature_id=renamed.id)
    note = lesson("A lesson title", project=repo)

    refs = _resolve(flask_client, f"feature:{renamed.id}", "feature:999999",
                    f"task:{step['id']}", f"defect:{bug.id}",
                    f"memory:{note}", "file:docs/a%20b.md", "file:gone.md",
                    f"feature:{renamed.id}", headers=VIEWER,
                    project_id=repo.id)

    assert set(refs) == {f"feature:{renamed.id}", "feature:999999",
                         f"task:{step['id']}", f"defect:{bug.id}",
                         f"memory:{note}", "file:docs/a%20b.md",
                         "file:gone.md"}
    assert refs[f"feature:{renamed.id}"] == {
        "ref": f"feature:{renamed.id}", "kind": "feature",
        "key": str(renamed.id), "label": f"F{renamed.id} Alpha",
        "title": "Alpha", "status": "spec", "project_id": repo.id,
        "project": "p", "feature_id": None, "url": f"/features/{renamed.id}"}
    assert refs["feature:999999"] is None
    assert (refs[f"task:{step['id']}"]["feature_id"],
            refs[f"task:{step['id']}"]["status"]) == (renamed.id, "idea")
    assert (refs[f"defect:{bug.id}"]["title"],
            refs[f"defect:{bug.id}"]["project"],
            refs[f"defect:{bug.id}"]["url"]) \
        == ("Delta broke", "p", f"/defects/{bug.id}")
    assert (refs[f"memory:{note}"]["title"], refs[f"memory:{note}"]["url"]) \
        == ("A lesson title", f"/memory?id={note}")
    assert (refs["file:docs/a%20b.md"]["exists"],
            refs["file:gone.md"]["exists"]) == (True, False)


def test_resolve_leaves_file_existence_unknown_without_a_project(
        flask_client):
    """No project means no tree to look in — unknown, not missing."""
    item = _resolve(flask_client, "file:docs/a%20b.md")["file:docs/a%20b.md"]

    assert (item["title"], item["project"]) == ("docs/a b.md", None)
    assert "exists" not in item
    assert item["url"] is None


def test_a_file_reference_with_a_project_resolves_to_a_viewer_url(
        flask_client, project, git_repo):
    """D10: the key is already percent-encoded, so it rides in the query as
    it stands and the server's decode gives back the path."""
    odd = "a&b/c#d+e%f g ü.md"
    repo = project("p", git_repo("p", tracked=["docs/a b.md", odd]))
    odd_key = encode_file_key(odd)

    refs = _resolve(flask_client, "file:docs/a%20b.md", "file:gone.md",
                    f"file:{odd_key}", project_id=repo.id)

    assert refs["file:docs/a%20b.md"]["url"] \
        == f"/files?project={repo.id}&path=docs/a%20b.md"
    assert refs["file:gone.md"]["url"] == f"/files?project={repo.id}&path=gone.md"
    query = parse_qs(urlsplit(refs[f"file:{odd_key}"]["url"]).query)
    assert query == {"project": [str(repo.id)], "path": [odd]}


def test_resolve_refuses_malformed_or_too_many_refs(flask_client):
    assert flask_client.get("/api/refs/resolve?ref=feature:1",
                            headers=ANON).status_code == 401

    hundred = [f"feature:{n}" for n in range(1, 101)]
    assert len(_resolve(flask_client, *hundred)) == 100
    too_many = flask_client.get("/api/refs/resolve", headers=EDITOR,
                                query_string={"ref": [*hundred, "task:1"]})
    assert (too_many.status_code, too_many.get_json()["field"]) \
        == (400, "ref")

    for bad in ("feature:0", "widget:1", "feature", "file:../x",
                "memory:8866E26A"):
        response = flask_client.get(
            "/api/refs/resolve", headers=EDITOR,
            query_string={"ref": ["feature:1", bad, "task:x"]})
        body = response.get_json()
        assert (response.status_code, body["field"]) == (400, "ref"), bad
        assert bad in body["error"], bad
    assert _resolve(flask_client) == {}


def test_resolve_refuses_an_unknown_project(flask_client):
    for raw in ("999999", "99999999999999999999", "abc"):
        unknown = flask_client.get(
            f"/api/refs/resolve?ref=feature:1&project_id={raw}",
            headers=EDITOR)
        assert (unknown.status_code, unknown.get_json()["field"]) \
            == (400, "project_id"), raw
