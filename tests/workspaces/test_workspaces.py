"""Workspaces: create / list / remove over the API and CLI, plus the path probe."""

from __future__ import annotations

import json
import os
import subprocess

import pytest
from sqlmodel import select
from typer.testing import CliRunner


@pytest.fixture
def isolated_settings(tmp_path, monkeypatch):
    from lib import settings as _cfg
    monkeypatch.setattr(_cfg, "SETTINGS_PATH", str(tmp_path / "settings.json"))
    monkeypatch.setattr(_cfg, "SETTINGS_LOCAL_PATH", str(tmp_path / "settings.local.json"))
    monkeypatch.setattr(_cfg, "CONFIG_DIR", str(tmp_path))
    return tmp_path


def _git_tree(path):
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", "-b", "main", str(path)], check=True)
    subprocess.run(
        ["git", "-C", str(path), "commit", "--allow-empty", "-m", "init", "-q"],
        check=True,
        env={**os.environ,
             "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
             "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"},
    )
    return path


@pytest.fixture
def folder_of_repos(tmp_path):
    root = tmp_path / "ws"
    _git_tree(root / "a")
    _git_tree(root / "b")
    (root / "notes").mkdir()
    return root


def _registered_names():
    from lib.orm import SessionLocal
    from lib.orm.models import Repo
    with SessionLocal() as s:
        return sorted(r.name for r in s.exec(select(Repo)).all())


def _member_names(ws):
    return sorted(m["name"] for m in ws["members"])


def _only_workspace(client):
    rows = client.get("/api/workspaces").get_json()["workspaces"]
    assert len(rows) == 1
    return rows[0]


def test_creating_a_workspace_registers_its_child_repos(
        flask_client, tmp_db, isolated_settings, folder_of_repos):
    resp = flask_client.post("/api/workspaces", json={"path": str(folder_of_repos)})

    assert resp.status_code == 201, resp.get_json()
    ws = _only_workspace(flask_client)
    assert ws["root_path"] == os.path.realpath(folder_of_repos)
    assert ws["name"] == "ws"
    assert _member_names(ws) == ["a", "b"]
    assert _registered_names() == ["a", "b"]


def test_creating_a_workspace_also_registers_a_git_root(
        flask_client, tmp_db, isolated_settings, tmp_path):
    root = _git_tree(tmp_path / "mono")
    _git_tree(root / "sub")

    assert flask_client.post("/api/workspaces", json={"path": str(root)}).status_code == 201
    assert _member_names(_only_workspace(flask_client)) == ["mono", "sub"]


def test_creating_a_workspace_with_no_repos_is_refused(
        flask_client, tmp_db, isolated_settings, tmp_path):
    empty = tmp_path / "empty"
    (empty / "plain").mkdir(parents=True)

    resp = flask_client.post("/api/workspaces", json={"path": str(empty)})

    assert resp.status_code == 400
    assert resp.get_json()["field"] == "path"
    assert flask_client.get("/api/workspaces").get_json()["workspaces"] == []


@pytest.mark.parametrize("path", ["", "relative/dir", "~", "/no/such/dir/anywhere"])
def test_creating_a_workspace_needs_an_existing_absolute_dir(
        flask_client, tmp_db, isolated_settings, path):
    resp = flask_client.post("/api/workspaces", json={"path": path})
    assert resp.status_code == 400
    assert resp.get_json()["field"] == "path"


def test_a_workspace_whose_only_repo_cannot_register_is_refused(
        flask_client, tmp_db, isolated_settings, tmp_path):
    from lib.sync.repo_discovery import add_repo
    add_repo(str(_git_tree(tmp_path / "elsewhere" / "a")))
    root = tmp_path / "ws"
    _git_tree(root / "a")

    resp = flask_client.post("/api/workspaces", json={"path": str(root)})

    assert resp.status_code == 400
    assert resp.get_json()["field"] == "path"
    assert flask_client.get("/api/workspaces").get_json()["workspaces"] == []


def test_conflicting_root_or_name_is_refused_with_the_field(
        flask_client, tmp_db, isolated_settings, folder_of_repos, tmp_path):
    assert flask_client.post("/api/workspaces",
                             json={"path": str(folder_of_repos)}).status_code == 201

    same_root = flask_client.post("/api/workspaces", json={"path": str(folder_of_repos)})
    assert same_root.status_code == 409
    assert same_root.get_json()["field"] == "root_path"

    other = tmp_path / "other"
    _git_tree(other / "c")
    same_name = flask_client.post("/api/workspaces",
                                  json={"path": str(other), "name": "ws"})
    assert same_name.status_code == 409
    assert same_name.get_json()["field"] == "name"


def test_creating_a_workspace_requires_an_editor(anon_client, folder_of_repos):
    assert anon_client.post("/api/workspaces",
                            json={"path": str(folder_of_repos)}).status_code == 401


def test_repo_registered_later_under_the_root_becomes_a_member(
        flask_client, tmp_db, isolated_settings, folder_of_repos, tmp_path):
    flask_client.post("/api/workspaces", json={"path": str(folder_of_repos)})
    later = _git_tree(folder_of_repos / "c")
    outside = _git_tree(tmp_path / "ws-sibling")

    for p in (later, outside):
        assert flask_client.post("/api/repos", json={"path": str(p)}).status_code == 200

    assert _member_names(_only_workspace(flask_client)) == ["a", "b", "c"]


def test_removing_a_workspace_keeps_its_repos(
        flask_client, tmp_db, isolated_settings, folder_of_repos):
    from lib.orm import SessionLocal
    from lib.orm.models import Feature, Repo

    ws = flask_client.post("/api/workspaces",
                           json={"path": str(folder_of_repos)}).get_json()["workspace"]
    with SessionLocal() as s:
        a = s.exec(select(Repo).where(Repo.name == "a")).one()
        s.add(Feature(title="lives in a", project_id=a.id))
        s.commit()

    assert flask_client.delete(f"/api/workspaces/{ws['id']}").status_code == 200

    assert flask_client.get("/api/workspaces").get_json()["workspaces"] == []
    assert _registered_names() == ["a", "b"]
    with SessionLocal() as s:
        feature = s.exec(select(Feature)).one()
        assert feature.project_id == a.id
    assert flask_client.delete(f"/api/workspaces/{ws['id']}").status_code == 404


def test_cli_workspace_commands_round_trip(tmp_db, isolated_settings, folder_of_repos):
    from cli.app import app
    runner = CliRunner()

    added = runner.invoke(app, ["workspace", "add", str(folder_of_repos), "--name", "mine"])
    assert added.exit_code == 0, added.output

    listed = runner.invoke(app, ["workspace", "list", "--json"])
    assert listed.exit_code == 0, listed.output
    [ws] = json.loads(listed.stdout)
    assert ws["name"] == "mine"
    assert _member_names(ws) == ["a", "b"]

    removed = runner.invoke(app, ["workspace", "remove", "mine"])
    assert removed.exit_code == 0, removed.output
    assert json.loads(runner.invoke(app, ["workspace", "list", "--json"]).stdout) == []
    assert _registered_names() == ["a", "b"]

    assert runner.invoke(app, ["workspace", "remove", "mine"]).exit_code == 1


def test_probe_classifies_a_folder_of_repos_as_a_workspace(
        flask_client, tmp_db, isolated_settings, folder_of_repos):
    from lib.sync.repo_discovery import add_repo
    add_repo(str(folder_of_repos / "a"))

    body = flask_client.get(f"/api/repos/probe?path={folder_of_repos}").get_json()

    assert body["exists"] is True
    assert body["is_git"] is False
    assert body["workspace_id"] is None
    assert [(c["name"], c["registered"]) for c in body["child_repos"]] == [
        ("a", True), ("b", False)]


def test_probe_reports_a_repo_a_missing_path_and_an_existing_workspace(
        flask_client, tmp_db, isolated_settings, folder_of_repos, tmp_path):
    repo = flask_client.get(f"/api/repos/probe?path={folder_of_repos / 'a'}").get_json()
    assert (repo["exists"], repo["is_git"], repo["child_repos"]) == (True, True, [])

    missing = flask_client.get(f"/api/repos/probe?path={tmp_path / 'nope'}").get_json()
    assert missing["exists"] is False

    ws = flask_client.post("/api/workspaces",
                           json={"path": str(folder_of_repos)}).get_json()["workspace"]
    again = flask_client.get(f"/api/repos/probe?path={folder_of_repos}").get_json()
    assert again["workspace_id"] == ws["id"]


def test_probe_requires_an_editor(anon_client, tmp_path):
    assert anon_client.get(f"/api/repos/probe?path={tmp_path}").status_code == 401
