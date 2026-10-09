"""Arm refuses a launch target git cannot serve (F155 R2, R3, I3).

Real `git` on a throwaway repo, like `tests/agent_sdk/test_worktree.py`: the
check is a reading of git's own answers, and a stub would pin the reading.
"""

from __future__ import annotations

import json
import subprocess
import urllib.error
from pathlib import Path

import pytest
from sqlmodel import select
from typer.testing import CliRunner

from cli.app import app
from lib.agent_sdk import worktree
from lib.auth import create_token
from lib.features import store as feature_store
from lib.orm import SessionLocal
from lib.orm.models.tasks import (
    AgentTask, AgentTaskRun, TASK_STATUS_ARMED, TASK_STATUS_IDEA,
)
from lib.tasks import runs, store

EDITOR = {"Authorization": f"Bearer {create_token(2, 'ed', 'editor')}"}

SLICED_SPEC = """## Acceptance

- test_a
- test_b

## Work slices

| # | Slice | Delivers | Spec refs | Tests | Depends on | Base | Skill |
|---|---|---|---|---|---|---|---|
| S1 | one | x | R1 | test_a | — | master | — |
| S2 | two | y | R2 | test_b | — | master | — |
"""


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=str(repo), check=True,
                   capture_output=True, text=True)


@pytest.fixture
def repo(tmp_path):
    """A repo with one commit on `master` and nothing else."""
    root = tmp_path / "proj"
    root.mkdir()
    subprocess.run(["git", "-c", "init.defaultBranch=master", "init",
                    str(root)], check=True, capture_output=True, text=True)
    _git(root, "config", "user.email", "t@example.com")
    _git(root, "config", "user.name", "T")
    (root / "README.md").write_text("hi\n")
    _git(root, "add", "README.md")
    _git(root, "commit", "-m", "init")
    return root


@pytest.fixture(autouse=True)
def no_claude_sync(monkeypatch):
    monkeypatch.setattr(worktree, "_sync_project_config", lambda path: None)


def _arm(client, task_id: int, body: dict):
    return client.post(f"/api/tasks/{task_id}/arm", headers=EDITOR, json=body)


def _status(task_id: int) -> str:
    return store.get_task(task_id)["status"]


def _attempts(task_id: int) -> list[AgentTaskRun]:
    with SessionLocal() as session:
        return list(session.exec(
            select(AgentTaskRun).where(AgentTaskRun.task_id == task_id)
            .order_by(AgentTaskRun.attempt)).all())


def test_arm_refuses_unknown_base_ref(flask_client, native_task, repo):
    task_id = native_task()

    res = _arm(flask_client, task_id, {
        "cwd": str(repo),
        "worktree": {"branch": "f999/s1", "base": "f999/integration"}})

    assert res.status_code == 400
    body = res.get_json()
    assert body["field"] == "worktree.base"
    assert "git branch f999/integration" in body["error"]
    assert _status(task_id) == TASK_STATUS_IDEA
    assert _attempts(task_id) == []


def test_arm_accepts_existing_branch_without_base(flask_client, native_task,
                                                  repo):
    _git(repo, "branch", "f999/s1")
    task_id = native_task()

    res = _arm(flask_client, task_id, {
        "cwd": str(repo),
        "worktree": {"branch": "f999/s1", "base": "f999/missing"}})

    assert res.status_code == 200, res.get_json()
    assert _status(task_id) == TASK_STATUS_ARMED


def test_arm_accepts_new_branch_on_default_base(flask_client, native_task,
                                                repo):
    task_id = native_task()

    res = _arm(flask_client, task_id, {
        "cwd": str(repo), "worktree": {"branch": "f999/s1", "base": ""}})

    assert res.status_code == 200, res.get_json()


def test_arm_refuses_missing_cwd(flask_client, native_task):
    task_id = native_task()

    res = _arm(flask_client, task_id, {"cwd": "/no/such/dir"})

    assert res.status_code == 400
    assert res.get_json()["field"] == "cwd"
    assert "no such directory: /no/such/dir" in res.get_json()["error"]
    assert _status(task_id) == TASK_STATUS_IDEA


def test_arm_refuses_worktree_outside_a_checkout(flask_client, native_task,
                                                 tmp_path):
    task_id = native_task()

    res = _arm(flask_client, task_id, {
        "cwd": str(tmp_path), "worktree": {"branch": "f999/s1"}})

    assert res.status_code == 400
    assert res.get_json()["field"] == "worktree"
    assert "not a git checkout" in res.get_json()["error"]


def test_arm_slices_refuses_bad_base_per_row(flask_client, repo, monkeypatch):
    monkeypatch.setattr(runs, "start_run", lambda task_id: None)
    fid = feature_store.create("sliced", body=SLICED_SPEC).id
    s1, s2 = flask_client.post(f"/api/features/{fid}/transcribe",
                               headers=EDITOR).get_json()["created"]

    res = flask_client.post(f"/api/features/{fid}/arm-slices", headers=EDITOR,
                            json={"items": [
        {"task_id": s1, "cwd": str(repo),
         "worktree": {"branch": f"f{fid}/s1", "base": "master"}},
        {"task_id": s2, "cwd": str(repo),
         "worktree": {"branch": f"f{fid}/s2", "base": f"f{fid}/integration"}},
    ]})

    assert res.status_code == 200, res.get_json()
    body = res.get_json()
    assert body["armed"] == [s1]
    [refused] = body["refused"]
    assert refused["task_id"] == s2
    assert refused["field"] == "worktree.base"
    assert f"git branch f{fid}/integration" in refused["reason"]
    assert _status(s2) == TASK_STATUS_IDEA


_LAUNCHES = {
    "sdk": {"provider": "claude", "launch": "sdk"},
    "tmux": {"provider": "claude", "launch": "tmux"},
    "channel": {"provider": "kimi", "launch": "sdk"},
}


@pytest.mark.parametrize("launch", sorted(_LAUNCHES))
def test_launch_refusal_leaves_no_starting_row(native_task, repo, monkeypatch,
                                               launch):
    """Every launch path builds its kwargs before it branches, so a base
    deleted after arming is refused — and settled — on all three."""
    reached = []
    monkeypatch.setattr(runs.supervisor, "launch_run",
                        lambda *a, **k: reached.append("sdk"))
    monkeypatch.setattr(runs.tmux_runs, "launch_attempt",
                        lambda *a, **k: reached.append("tmux"))
    monkeypatch.setattr(runs, "_channel_launcher",
                        lambda provider: (lambda *a, **k: reached.append(
                            "channel")) if provider != "claude" else None)
    _git(repo, "branch", "f999/integration")
    task_id = native_task()
    store.arm_task(task_id, autorun=0, params={
        "cwd": str(repo), "model": "", "permission_mode": "", "effort": "",
        "one_shot": False, "env": {}, "skill": None,
        "worktree": {"branch": "f999/s1", "base": "f999/integration"},
        **_LAUNCHES[launch]})
    _git(repo, "branch", "-D", "f999/integration")

    with pytest.raises(runs.RunRefused, match="unknown base ref"):
        runs.start_run(task_id)

    [attempt] = _attempts(task_id)
    assert attempt.status == runs.RUN_STATUS_FAILED
    assert "unknown base ref 'f999/integration'" in attempt.exit_reason
    assert attempt.ended_at is not None
    assert _status(task_id) == TASK_STATUS_ARMED
    assert reached == []


@pytest.fixture
def server_down(monkeypatch):
    from lib.settings import settings

    def _refused(request, timeout):
        raise urllib.error.URLError(ConnectionRefusedError())

    monkeypatch.setattr("cli.commands.tasks._open_loopback", _refused)
    return settings


def test_cli_task_arm(tmp_db, native_task, repo, monkeypatch, server_down):
    monkeypatch.chdir(repo)
    task_id = native_task()
    runner = CliRunner()

    ok = runner.invoke(app, ["task", "arm", str(task_id), "--cwd", str(repo),
                             "--branch", "f999/s1", "--base", "master",
                             "--json"])

    assert ok.exit_code == 0, ok.output
    assert _status(task_id) == TASK_STATUS_ARMED
    assert runs.armed_attempt(task_id)["params"]["worktree"] == {
        "branch": "f999/s1", "base": "master"}

    other = native_task()
    bad = runner.invoke(app, ["task", "arm", str(other), "--base", "nope"])
    assert bad.exit_code == 1
    assert "worktree.base" in bad.output
    assert _status(other) == TASK_STATUS_IDEA

    unknown = runner.invoke(app, ["task", "arm", str(other), "--cwd",
                                  str(repo), "--branch", "f999/s2",
                                  "--base", "nope"])
    assert unknown.exit_code == 1
    assert "worktree.base: unknown base ref 'nope'" in unknown.output
    assert _status(other) == TASK_STATUS_IDEA


def test_cli_task_arm_posts_to_the_server(flask_client, native_task, repo,
                                          monkeypatch):
    """Server up: the CLI's own signed token gets it past the editor guard,
    and a refusal comes back with the route's field."""
    sent = []

    class _Reply:
        def __init__(self, data):
            self._data = data

        def read(self):
            return self._data

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def _forward(request, timeout):
        import urllib.error

        path = request.full_url.split("://", 1)[1].split("/", 1)[1]
        sent.append(request.get_header("Authorization"))
        res = flask_client.post(
            "/" + path, data=request.data,
            headers={"Authorization": request.get_header("Authorization"),
                     "Content-Type": "application/json"})
        if res.status_code >= 400:
            import io
            raise urllib.error.HTTPError(request.full_url, res.status_code,
                                         "refused", {}, io.BytesIO(res.data))
        return _Reply(res.data)

    monkeypatch.setattr("cli.commands.tasks._open_loopback", _forward)
    task_id = native_task()
    runner = CliRunner()

    ok = runner.invoke(app, ["task", "arm", str(task_id), "--cwd", str(repo),
                             "--branch", "f999/s1", "--json"])
    assert ok.exit_code == 0, ok.output
    assert json.loads(ok.output)["task"]["status"] == TASK_STATUS_ARMED
    assert sent and sent[0].startswith("Bearer ")

    other = native_task()
    bad = runner.invoke(app, ["task", "arm", str(other), "--cwd", str(repo),
                              "--branch", "f999/s2", "--base", "nope"])
    assert bad.exit_code == 1
    assert "worktree.base: unknown base ref 'nope'" in bad.output
    with SessionLocal() as session:
        assert session.get(AgentTask, other).status == TASK_STATUS_IDEA


def test_run_route_names_the_field_of_a_launch_refusal(flask_client,
                                                       native_task, repo):
    _git(repo, "branch", "f999/integration")
    task_id = native_task()
    assert _arm(flask_client, task_id, {
        "cwd": str(repo), "worktree": {"branch": "f999/s1",
                                       "base": "f999/integration"},
    }).status_code == 200
    _git(repo, "branch", "-D", "f999/integration")

    res = flask_client.post(f"/api/tasks/{task_id}/run", headers=EDITOR)

    assert res.status_code == 400
    assert res.get_json()["field"] == "worktree.base"
    assert [a.status for a in _attempts(task_id)] == [runs.RUN_STATUS_FAILED]
