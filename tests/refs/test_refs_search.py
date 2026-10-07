"""`GET /api/refs`: groups, ranking, the query grammar and Recent."""

from __future__ import annotations

from tests.refs.conftest import ADMIN, EDITOR, groups, keys

ALL_KINDS = ["feature", "task", "defect", "session", "file", "memory"]
FILE_HINT = {"kind": "file", "total": 0, "items": [],
             "hint": "Pick a project to search its files"}


def _search(client, headers=EDITOR, **params):
    response = client.get("/api/refs", query_string=params, headers=headers)
    assert response.status_code == 200, response.get_json()
    return response.get_json()


def _kinds(body) -> list[str]:
    return [group["kind"] for group in body["groups"]]


def _rows(client, **params) -> int:
    return sum(len(group["items"]) for group in _search(client, **params)
               ["groups"])


def test_search_groups_by_kind_with_exact_id_first(
        flask_client, project, git_repo, feature, task, defect, session_trace,
        lesson):
    repo = project("p", git_repo("p", tracked=["notes/alpha.md"]))
    a = feature("Alpha", project=repo, at="2026-09-01 10:00:00")
    b = feature("Beta alpha", project=repo, at="2026-09-03 10:00:00")
    c = feature("Gamma", body="mentions alpha in the spec", project=repo,
                at="2026-09-05 10:00:00")
    feature("Unrelated", project=repo)
    task("Alpha step", project=repo)
    defect("alpha broke", feature_id=a.id)
    session_trace("3fd8137e-e0fd-4a5b-9c1d-0123456789ab",
                  title="alpha session", project=repo)
    lesson("Alpha lesson", project=repo)

    body = _search(flask_client, headers=ADMIN, q="alpha", project_id=repo.id)

    assert (body["query"], body["kind"], _kinds(body)) \
        == ("alpha", None, ALL_KINDS)
    found = groups(body)
    # Title prefix, then title substring, then the body.
    assert keys(found["feature"]) == [str(a.id), str(b.id), str(c.id)]
    assert found["feature"]["total"] == 3
    assert found["feature"]["items"][0] == {
        "ref": f"feature:{a.id}", "kind": "feature", "key": str(a.id),
        "label": f"F{a.id} Alpha", "title": "Alpha", "status": "spec",
        "project_id": repo.id, "project": "p", "feature_id": None, "feature_title": None,
        "url": f"/features/{a.id}"}
    assert keys(found["file"]) == ["notes/alpha.md"]

    # An id leads its group even over a better title match.
    numbered = feature(f"{a.id} monkeys", project=repo)
    by_id = groups(_search(flask_client, q=str(a.id)))
    assert keys(by_id["feature"])[:2] == [str(a.id), str(numbered.id)]
    assert set(by_id) <= {"feature", "task", "defect"}


def test_search_draws_no_empty_group(flask_client, project, git_repo,
                                     feature):
    repo = project("p", git_repo("p", tracked=["README.md"]))
    feature("Beta")

    assert _kinds(_search(flask_client, q="beta", project_id=repo.id)) \
        == ["feature"]
    assert _search(flask_client, q="nothing-matches-this",
                   project_id=repo.id)["groups"] == []
    # Without a project the File group is the hint, matches or not.
    assert _search(flask_client, q="nothing-matches-this")["groups"] \
        == [FILE_HINT]


def test_search_id_shortcut_searches_one_kind(flask_client, feature, task):
    feature("Alpha")
    wanted = feature("Gamma")
    step = task("Gamma step")

    shortcut = _search(flask_client, q=f"F{wanted.id}")
    assert _kinds(shortcut) == ["feature"]
    assert keys(shortcut["groups"][0])[0] == str(wanted.id)
    lower = _search(flask_client, q=f"t{step['id']}")
    assert (_kinds(lower), keys(lower["groups"][0])) \
        == (["task"], [str(step["id"])])


def test_search_kind_filter_and_aliases(flask_client, project, feature, task,
                                        lesson):
    repo = project("p")
    feature("Alpha feature", project=repo)
    for n in range(25):
        task(f"Alpha task {n}", project=repo,
             updated=f"2026-09-01 10:{n:02d}:00")
    lesson("Alpha lesson")

    for params in ({"kind": "task", "q": "alpha"}, {"kind": "t", "q": "alpha"},
                   {"q": "task:alpha"}, {"q": "t:alpha"}, {"q": "Task:alpha"}):
        body = _search(flask_client, **params)
        # One kind lists up to 20 rows; the total is every match.
        assert (body["kind"], _kinds(body), len(body["groups"][0]["items"]),
                body["groups"][0]["total"]) == ("task", ["task"], 20, 25), \
            params

    assert _kinds(_search(flask_client, q="lesson:alpha")) == ["memory"]
    assert _kinds(_search(flask_client, kind="m")) == ["memory"]
    # `file:` has no alias, and no project means one inert hint row.
    assert _search(flask_client, q="file:x")["groups"] == [FILE_HINT]

    refused = flask_client.get("/api/refs?kind=widget", headers=EDITOR)
    assert (refused.status_code, refused.get_json()["field"]) \
        == (400, "kind")


def test_search_limit_is_clamped_and_project_checked(flask_client, task):
    for n in range(25):
        task(f"Alpha task {n}")

    assert _rows(flask_client, q="alpha") == 5
    assert _rows(flask_client, q="alpha", limit=100) == 25
    assert _rows(flask_client, q="alpha", limit=0) == 1
    assert _rows(flask_client, q="alpha", limit="many") == 5

    for raw in ("999999", "99999999999999999999", "abc"):
        unknown = flask_client.get(f"/api/refs?project_id={raw}",
                                   headers=EDITOR)
        assert (unknown.status_code, unknown.get_json()["field"]) \
            == (400, "project_id"), raw


def test_search_recent_is_scoped_to_the_selected_project(
        flask_client, project, feature, task):
    p, q = project("p"), project("q")
    older = feature("P older", project=p, at="2026-09-01 10:00:00")
    newer = feature("P newer", project=p, at="2026-09-03 10:00:00")
    newest = feature("P newest", project=p, at="2026-09-04 10:00:00")
    feature("P oldest", project=p, at="2026-08-01 10:00:00")
    feature("P dropped", project=p, abandoned=True)
    elsewhere = feature("Q newest of all", project=q, at="2026-09-09 10:00:00")
    t1 = task("P task 1", project=p, updated="2026-09-01 10:00:00")
    t2 = task("P task 2", project=p, updated="2026-09-02 10:00:00")
    t3 = task("P task 3", project=p, updated="2026-09-03 10:00:00")
    task("P task 0", project=p, updated="2026-08-01 10:00:00")
    task("P archived", project=p, updated="2026-09-08 10:00:00",
         status="archived")
    other = task("Q task", project=q, updated="2026-09-09 10:00:00")

    scoped = _search(flask_client, project_id=p.id)

    assert _kinds(scoped) == ["feature", "task"]
    found = groups(scoped)
    assert keys(found["feature"]) == [str(newest.id), str(newer.id),
                                      str(older.id)]
    assert keys(found["task"]) == [str(t3["id"]), str(t2["id"]),
                                   str(t1["id"])]
    assert (found["feature"]["total"], found["task"]["total"]) == (4, 4)

    everywhere = groups(_search(flask_client))
    assert (keys(everywhere["feature"])[0], keys(everywhere["task"])[0]) \
        == (str(elsewhere.id), str(other["id"]))

    # An empty query with a kind lists that kind's most recent rows.
    one_kind = _search(flask_client, q="task:")
    assert keys(one_kind["groups"][0])[:2] == [str(other["id"]),
                                               str(t3["id"])]


def test_search_ranks_selected_project_first(flask_client, project, feature,
                                            task):
    p, q = project("p"), project("q")
    theirs = feature("Alpha one", project=q, at="2026-09-09 10:00:00")
    ours = feature("Alpha two", project=p, at="2026-09-01 10:00:00")
    ours_substring = feature("The alpha three", project=p,
                             at="2026-09-09 10:00:00")
    dropped = feature("Alpha dropped", project=p, abandoned=True)
    archived = task("Alpha archived", project=p, status="archived")

    found = groups(_search(flask_client, q="alpha", project_id=p.id))

    # Project first inside a tier; the tier still outranks the project.
    assert keys(found["feature"]) == [str(ours.id), str(theirs.id),
                                      str(ours_substring.id)]
    assert found["feature"]["items"][1]["project"] == "q"
    assert "task" not in found

    # Abandoned and archived rows answer to their exact id only.
    by_id = _search(flask_client, q=f"F{dropped.id}")["groups"][0]["items"]
    assert (by_id[0]["key"], by_id[0]["status"]) \
        == (str(dropped.id), "abandoned")
    archived_hit = _search(flask_client, q=f"T{archived['id']}")
    assert archived_hit["groups"][0]["items"][0]["status"] == "archived"


def test_search_keeps_a_prefix_that_conflicts_with_kind_as_text(
        flask_client, feature, task):
    feature("Alpha")
    labelled = task("feature:alpha checklist")

    body = _search(flask_client, kind="task", q="feature:alpha")

    assert (body["kind"], _kinds(body)) == ("task", ["task"])
    assert keys(body["groups"][0]) == [str(labelled["id"])]


def test_search_ranks_a_typed_wildcard_literally(flask_client, lesson):
    """Memory's own filter lets `%` match anything; the ranking must not, or
    `5%` would rank every title that merely starts with 5 as a prefix hit."""
    literal = lesson("5% off everything")
    lesson("50 ways to fail")  # newer, so recency alone would rank it first

    ranked = keys(groups(_search(flask_client, q="5%", kind="m"))["memory"])

    assert ranked[0] == literal
