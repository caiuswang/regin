"""File references: the selected project's tracked files, and nothing else."""

from __future__ import annotations

from tests.refs.conftest import EDITOR, groups, keys

TRACKED = ["frontend/src/composables/useFileMentions.js",
           "frontend/src/composables/useDetailSpine.js",
           "docs/a b.md", "README.md", "lib/usefile_helpers.py"]


def _files(client, project_id, q):
    response = client.get("/api/refs", headers=EDITOR,
                          query_string={"q": q, "project_id": project_id})
    assert response.status_code == 200, response.get_json()
    return groups(response.get_json()).get("file")


def test_search_file_kind_lists_tracked_repo_files_only(
        flask_client, project, git_repo):
    root = git_repo("p", tracked=TRACKED,
                    untracked=["frontend/src/composables/useFileSecret.js"])
    repo = project("p", root)

    found = _files(flask_client, repo.id, "file:usefilemen")

    assert keys(found) == ["frontend/src/composables/useFileMentions.js"]
    assert found["items"][0]["title"] == TRACKED[0]
    assert found["items"][0]["project"] == "p"
    # Basename prefix outranks a match spread across the path.
    assert keys(_files(flask_client, repo.id, "file:usefile")) \
        == ["lib/usefile_helpers.py", TRACKED[0]]
    # Keys are percent-encoded; labels and titles read decoded.
    spaced = _files(flask_client, repo.id, "file:a b")["items"][0]
    assert (spaced["key"], spaced["label"]) == ("docs/a%20b.md",
                                                "docs/a b.md")
    # A `/` in the query searches files only; empty lists alphabetically.
    response = flask_client.get("/api/refs", headers=EDITOR, query_string={
        "q": "src/comp", "project_id": repo.id}).get_json()
    assert [g["kind"] for g in response["groups"]] == ["file"]
    assert keys(_files(flask_client, repo.id, "file:"))[:2] \
        == ["README.md", "docs/a%20b.md"]


def test_search_file_query_cannot_leave_repo(flask_client, project, git_repo,
                                             tmp_path):
    repo = project("p", git_repo("p", tracked=["README.md"]))
    (tmp_path / "secret.txt").write_text("outside the repo\n")

    for q in ("file:../", "file:../secret.txt", "file:a/../README.md",
              "file:/etc/passwd", "file:~/.ssh", "../secret.txt",
              "/etc/passwd", "file:secret"):
        assert _files(flask_client, repo.id, q) is None, q

    refused = flask_client.get(
        "/api/refs/resolve?ref=file:../secret.txt", headers=EDITOR)
    assert (refused.status_code, refused.get_json()["field"]) == (400, "ref")
