"""Unit tests for web.blueprints.repos.

GET /api/repos is unauthenticated. POST /api/repos and DELETE
/api/repos/<name> require the editor role.
"""

from __future__ import annotations

import os
import subprocess

import pytest


def _editor_auth_header():
    from lib.auth import create_token
    token = create_token(1, "editor", "editor")
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def isolated_settings(tmp_path, monkeypatch):
    """Redirect settings file paths so add/remove writes go to tmp."""
    shared = tmp_path / "settings.json"
    local = tmp_path / "settings.local.json"
    from lib import settings as _cfg
    monkeypatch.setattr(_cfg, "SETTINGS_PATH", str(shared))
    monkeypatch.setattr(_cfg, "SETTINGS_LOCAL_PATH", str(local))
    monkeypatch.setattr(_cfg, "CONFIG_DIR", str(tmp_path))
    return tmp_path


def _make_git_repo(path):
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", "-b", "main", str(path)], check=True)
    subprocess.run(
        ["git", "-C", str(path), "commit", "--allow-empty", "-m", "init", "-q"],
        check=True,
        env={**os.environ,
             "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
             "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"},
    )


# ── GET /api/repos ──────────────────────────────────────────

def test_list_repos_empty(flask_client, tmp_db, isolated_settings):
    body = flask_client.get("/api/repos").get_json()
    assert body == {"repos": []}


def test_list_repos_includes_registered(flask_client, tmp_db, tmp_path, isolated_settings):
    from lib.sync.repo_discovery import add_repo
    repo_path = tmp_path / "svc"
    _make_git_repo(repo_path)
    add_repo(str(repo_path))

    body = flask_client.get("/api/repos").get_json()
    names = [r["name"] for r in body["repos"]]
    assert names == ["svc"]


# ── POST /api/repos ─────────────────────────────────────────

def test_add_repo_requires_auth(anon_client):
    resp = anon_client.post("/api/repos", json={"path": "/tmp/x"})
    assert resp.status_code == 401


def test_add_repo_rejects_missing_path_field(flask_client, tmp_db, isolated_settings):
    resp = flask_client.post(
        "/api/repos", json={}, headers=_editor_auth_header(),
    )
    assert resp.status_code == 400


def test_add_repo_rejects_non_git_path(flask_client, tmp_db, tmp_path, isolated_settings):
    plain = tmp_path / "plain"
    plain.mkdir()
    resp = flask_client.post(
        "/api/repos", json={"path": str(plain)},
        headers=_editor_auth_header(),
    )
    assert resp.status_code == 400


def test_add_repo_persists(flask_client, tmp_db, tmp_path, isolated_settings):
    repo_path = tmp_path / "svc"
    _make_git_repo(repo_path)
    resp = flask_client.post(
        "/api/repos", json={"path": str(repo_path)},
        headers=_editor_auth_header(),
    )
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["repo"]["name"] == "svc"
    assert body["repo"]["default_branch"] == "main"


def test_add_repo_duplicate_returns_409(flask_client, tmp_db, tmp_path, isolated_settings):
    repo_path = tmp_path / "svc"
    _make_git_repo(repo_path)
    flask_client.post(
        "/api/repos", json={"path": str(repo_path)},
        headers=_editor_auth_header(),
    )
    resp = flask_client.post(
        "/api/repos", json={"path": str(repo_path)},
        headers=_editor_auth_header(),
    )
    assert resp.status_code == 409


# ── DELETE /api/repos/<name> ────────────────────────────────

def test_remove_repo_requires_auth(anon_client):
    resp = anon_client.delete("/api/repos/svc")
    assert resp.status_code == 401


def test_remove_repo_not_found(flask_client, tmp_db, isolated_settings):
    resp = flask_client.delete("/api/repos/missing",
                               headers=_editor_auth_header())
    assert resp.status_code == 404


def test_remove_repo_drops_db_rows(flask_client, tmp_db, tmp_path, isolated_settings):
    from lib.sync.repo_discovery import add_repo
    repo_path = tmp_path / "svc"
    _make_git_repo(repo_path)
    add_repo(str(repo_path))

    resp = flask_client.delete("/api/repos/svc",
                               headers=_editor_auth_header())
    assert resp.status_code == 200

    body = flask_client.get("/api/repos").get_json()
    assert body == {"repos": []}
