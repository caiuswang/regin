"""F167 Rules C: a workspace root is a run's working directory (arm row, run row)."""

from __future__ import annotations

import os
import subprocess
from types import SimpleNamespace

import pytest

from lib.tasks import runs


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
def isolated_settings(tmp_path, monkeypatch):
    from lib import settings as _cfg
    monkeypatch.setattr(_cfg, "SETTINGS_PATH", str(tmp_path / "settings.json"))
    monkeypatch.setattr(_cfg, "SETTINGS_LOCAL_PATH", str(tmp_path / "settings.local.json"))
    monkeypatch.setattr(_cfg, "CONFIG_DIR", str(tmp_path))
    return tmp_path


@pytest.fixture
def workspace(flask_client, tmp_db, isolated_settings, tmp_path):
    root = tmp_path / "ws"
    _git_tree(root / "a")
    _git_tree(root / "b")
    resp = flask_client.post("/api/workspaces", json={"path": str(root)})
    assert resp.status_code == 201, resp.get_json()
    return resp.get_json()["workspace"]


class _Handle:
    def __init__(self, trace_id: str) -> None:
        self.trace_id = trace_id

    def add_done_callback(self, callback) -> None:
        pass


@pytest.fixture
def launcher(monkeypatch):
    state = SimpleNamespace(calls=[])

    def _fake(prompt, **kwargs):
        state.calls.append(kwargs)
        return _Handle(f"sdk-ws{len(state.calls)}")

    monkeypatch.setattr(runs.supervisor, "launch_run", _fake)
    return state


def test_launch_options_list_each_workspace_root(flask_client, workspace):
    [row] = flask_client.get("/api/agent-runs/launch-options").get_json()["workspaces"]

    assert row == {"id": workspace["id"], "name": "ws",
                   "root_path": workspace["root_path"], "repos": 2, "is_git": False}


def test_run_in_a_workspace_root_launches_with_that_cwd(
        flask_client, workspace, native_task, launcher):
    task_id = native_task()
    root = workspace["root_path"]

    armed = flask_client.post(f"/api/tasks/{task_id}/arm", json={"cwd": root})
    assert armed.status_code == 200, armed.get_json()
    runs.start_run(task_id)

    [call] = launcher.calls
    assert call["cwd"] == root
    assert "add_dirs" not in call
