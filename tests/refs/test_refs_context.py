"""The composer's own feature first, readable titles, and one item's preview."""

from __future__ import annotations

from tests.refs.conftest import ADMIN, EDITOR, VIEWER, groups, keys


def _get(client, path, headers=EDITOR, status=200, **params):
    response = client.get(path, query_string=params, headers=headers)
    assert response.status_code == status, response.get_json()
    return response.get_json()


def test_recent_screen_pins_the_composers_feature_and_its_work(
        flask_client, project, feature, task, defect):
    repo = project("p")
    mine = feature("Pixel redesign", project=repo, at="2026-09-01 10:00:00")
    other = feature("Other work", project=repo, at="2026-09-09 10:00:00")
    step = task("S14 — verify", project=repo, feature_id=mine.id,
                updated="2026-09-02 10:00:00")
    loose = task("Loose task", project=repo, updated="2026-09-05 10:00:00")
    broke = defect("menu clipped", feature_id=mine.id)

    body = _get(flask_client, "/api/refs", feature_id=mine.id)

    assert [item["ref"] for item in body["context"]] == [
        f"feature:{mine.id}", f"task:{step['id']}", f"defect:{broke.id}"]
    found = groups(body)
    # Recent leaves out what the pinned section already shows.
    assert keys(found["feature"]) == [str(other.id)]
    assert keys(found["task"]) == [str(loose["id"])]


def test_text_search_ranks_the_composers_feature_above_title_tiers(
        flask_client, project, feature, task, defect):
    repo = project("p")
    mine = feature("Pixel redesign", project=repo)
    elsewhere = feature("Elsewhere", project=repo)
    theirs = task("verify everything", project=repo, feature_id=elsewhere.id,
                  updated="2026-09-09 10:00:00")
    ours = task("S14 — verify", project=repo, feature_id=mine.id)
    defect("verify step broke elsewhere", feature_id=elsewhere.id)
    our_defect = defect("the verify chip overflows", feature_id=mine.id)

    found = groups(_get(flask_client, "/api/refs", q="verify",
                        feature_id=mine.id))

    assert keys(found["task"]) == [str(ours["id"]), str(theirs["id"])]
    assert keys(found["defect"])[0] == str(our_defect.id)
    # Without the context the title-prefix tier wins.
    plain = groups(_get(flask_client, "/api/refs", q="verify"))
    assert keys(plain["task"])[0] == str(theirs["id"])


def test_feature_context_lends_its_project_to_file_search(
        flask_client, project, git_repo, feature):
    repo = project("p", git_repo("p", tracked=["docs/a.md"]))
    mine = feature("Has a repo", project=repo)

    found = groups(_get(flask_client, "/api/refs", q="file:a.md",
                        feature_id=mine.id))

    assert keys(found["file"]) == ["docs/a.md"]


def test_unknown_context_feature_is_refused(flask_client):
    body = _get(flask_client, "/api/refs", status=400, feature_id=999999)
    assert body["field"] == "feature_id"


def test_rows_read_references_as_labels_and_name_their_feature(
        flask_client, project, feature, task):
    repo = project("p")
    parent = feature("Pixel redesign", project=repo)
    step = task(f"Build · [F{parent.id} Pixel redesign](feature:{parent.id})"
                "\n  second line", project=repo, feature_id=parent.id)

    item = _get(flask_client, "/api/refs/resolve",
                ref=f"task:{step['id']}")["refs"][f"task:{step['id']}"]

    assert item["title"] == f"Build · F{parent.id} Pixel redesign second line"
    assert item["label"] == f"T{step['id']} Build · F{parent.id} Pixel redesign second line"
    assert item["feature_title"] == "Pixel redesign"


def test_preview_returns_the_body_with_references_as_text(
        flask_client, project, feature, task):
    repo = project("p")
    parent = feature("Pixel redesign", project=repo)
    step = task("Step", project=repo, body=(
        f"Builds on [F{parent.id} Pixel \\[x\\]](feature:{parent.id}).\n\n"
        "![](image:1)\n\n`[F1 a](feature:1)`"))

    body = _get(flask_client, "/api/refs/preview", headers=VIEWER,
                ref=f"task:{step['id']}")

    assert body["item"]["ref"] == f"task:{step['id']}"
    assert body["format"] == "markdown"
    assert body["truncated"] is False
    assert body["body"] == (f"Builds on F{parent.id} Pixel \\[x\\].\n\n"
                            "*(image)*\n\n`[F1 a](feature:1)`")


def test_preview_cuts_a_long_body(flask_client, feature):
    row = feature("Long", body="line\n" * 2000)
    body = _get(flask_client, "/api/refs/preview", ref=f"feature:{row.id}")
    assert body["truncated"] is True
    assert len(body["body"]) <= 4000


def test_preview_of_a_file_reads_head(flask_client, project, git_repo):
    repo = project("p", git_repo("p", tracked=["docs/a.md"]))
    body = _get(flask_client, "/api/refs/preview", ref="file:docs/a.md",
                project_id=repo.id)
    assert (body["format"], body["body"]) == ("code", "docs/a.md")


def test_preview_hides_a_session_from_a_non_admin(flask_client, session_trace):
    trace = session_trace("3fd8137e-e0fd-4a5b-9c1d-0123456789ab",
                          prompt="secret first prompt")
    hidden = _get(flask_client, "/api/refs/preview", ref=f"session:{trace}")
    assert hidden["item"]["restricted"] is True and hidden["body"] == ""
    shown = _get(flask_client, "/api/refs/preview", headers=ADMIN,
                 ref=f"session:{trace}")
    assert shown["body"] == "secret first prompt"


def test_preview_of_nothing(flask_client):
    assert _get(flask_client, "/api/refs/preview",
                ref="feature:999999")["item"] is None
    assert _get(flask_client, "/api/refs/preview", status=400,
                ref="nope")["field"] == "ref"
