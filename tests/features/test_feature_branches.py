"""feature ↔ branch: the integration branch and the run branches, read live
from a real (temporary) git repo — C-R1–C-R4, C-R6, C-R7."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from lib.auth import create_token
from lib.features import store as feature_store
from lib.orm import SessionLocal
from lib.orm.models.sync import Repo
from lib.orm.models.tasks import AgentTaskRun

EDITOR = {"Authorization": f"Bearer {create_token(2, 'ed', 'editor')}"}


def _git(root: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=root, check=True,
                          capture_output=True, text=True).stdout.strip()


@pytest.fixture()
def git_repo(tmp_path, tmp_db) -> Path:
    if not shutil.which("git"):
        pytest.skip("git not installed")
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init", "-q", "-b", "main")
    _git(root, "config", "user.email", "t@example.com")
    _git(root, "config", "user.name", "t")
    (root / "a.txt").write_text("a\n")
    _git(root, "add", "a.txt")
    _git(root, "commit", "-q", "-m", "init")
    return root


def _commit_on(root: Path, branch: str, base: str, name: str) -> None:
    _git(root, "branch", branch, base)
    _git(root, "checkout", "-q", branch)
    (root / name).write_text(name)
    _git(root, "add", name)
    _git(root, "commit", "-q", "-m", name)
    _git(root, "checkout", "-q", "main")


def _feature_in(path) -> int:
    with SessionLocal() as session:
        repo = Repo(name="proj", path=str(path), default_branch="main")
        session.add(repo)
        session.commit()
        session.refresh(repo)
    row = feature_store.create("a feature")
    feature_store.set_project(row.id, repo.id)
    return row.id


def _armed_run(task_id: int, attempt: int, branch: str, base: str) -> int:
    with SessionLocal() as session:
        run = AgentTaskRun(task_id=task_id, attempt=attempt, status="done",
                           started_at="2026-10-01 10:00:00",
                           params_json=json.dumps(
                               {"worktree": {"branch": branch, "base": base}}))
        session.add(run)
        session.commit()
        session.refresh(run)
        return run.id


def _branches(client, fid: int):
    resp = client.get(f"/api/features/{fid}/branches")
    assert resp.status_code == 200
    return resp.get_json()


def test_convention_branch_is_shown(flask_client, git_repo):
    fid = _feature_in(git_repo)
    _git(git_repo, "branch", f"f{fid}/integration", "main")

    body = _branches(flask_client, fid)

    assert body["integration"] == {
        "name": f"f{fid}/integration", "source": "convention",
        "exists": True, "merged_into_default": True}
    assert body["runs"] == []
    assert "error" not in body


def test_set_branch_wins_over_convention_and_need_not_exist(
        flask_client, git_repo):
    fid = _feature_in(git_repo)
    _git(git_repo, "branch", f"f{fid}/integration", "main")
    feature_store.set_branch(fid, "release/x")

    integration = _branches(flask_client, fid)["integration"]

    assert integration == {"name": "release/x", "source": "set",
                           "exists": False, "merged_into_default": None}


def test_no_set_and_no_convention_branch_leaves_integration_empty(
        flask_client, git_repo):
    fid = _feature_in(git_repo)

    assert _branches(flask_client, fid)["integration"] == {
        "name": "", "source": None, "exists": False,
        "merged_into_default": None}


def test_run_branches_are_derived_from_run_params(
        flask_client, git_repo, native_task):
    fid = _feature_in(git_repo)
    integration = f"f{fid}/integration"
    _git(git_repo, "branch", integration, "main")
    _commit_on(git_repo, f"f{fid}/s1", integration, "s1.txt")
    _commit_on(git_repo, f"f{fid}/s2", integration, "s2.txt")
    _git(git_repo, "checkout", "-q", integration)
    _git(git_repo, "merge", "-q", "--no-edit", f"f{fid}/s1")
    _git(git_repo, "checkout", "-q", "main")
    wt = git_repo.parent / "wt-s2"
    _git(git_repo, "worktree", "add", "-q", str(wt), f"f{fid}/s2")

    t1 = native_task(feature_id=fid)
    t2 = native_task(feature_id=fid)
    _armed_run(t1, 1, f"f{fid}/s1", integration)
    last_s1 = _armed_run(t1, 2, f"f{fid}/s1", integration)
    last_s2 = _armed_run(t2, 1, f"f{fid}/s2", integration)
    other = native_task(feature_id=None)
    _armed_run(other, 1, "elsewhere", "main")
    with SessionLocal() as session:
        session.add(AgentTaskRun(task_id=t2, attempt=2, status="done",
                                 started_at="2026-10-01 11:00:00"))
        session.commit()

    runs = {r["branch"]: r for r in _branches(flask_client, fid)["runs"]}

    assert set(runs) == {f"f{fid}/s1", f"f{fid}/s2"}
    assert runs[f"f{fid}/s1"] == {
        "branch": f"f{fid}/s1", "base": integration, "task_id": t1,
        "last_run_id": last_s1, "worktree_path": None, "exists": True,
        "merged_into_base": True}
    s2 = runs[f"f{fid}/s2"]
    assert (s2["task_id"], s2["last_run_id"]) == (t2, last_s2)
    assert Path(s2["worktree_path"]).resolve() == wt.resolve()
    assert (s2["exists"], s2["merged_into_base"]) == (True, False)


def test_a_deleted_run_branch_reads_as_absent(
        flask_client, git_repo, native_task):
    fid = _feature_in(git_repo)
    task = native_task(feature_id=fid)
    _armed_run(task, 1, f"f{fid}/gone", "main")

    (run,) = _branches(flask_client, fid)["runs"]

    assert (run["exists"], run["merged_into_base"]) == (False, None)


@pytest.mark.parametrize("where", ["missing", "not_git"])
def test_bad_repo_does_not_500(flask_client, tmp_path, tmp_db, native_task,
                               where):
    path = tmp_path / "nope"
    if where == "not_git":
        path.mkdir()
    fid = _feature_in(path)
    feature_store.set_branch(fid, "release/x")
    _armed_run(native_task(feature_id=fid), 1, f"f{fid}/s1", "main")

    body = _branches(flask_client, fid)

    assert body["runs"] == []
    assert body["error"]
    assert body["integration"]["exists"] is False
    assert body["integration"]["name"] == "release/x"


def test_unknown_feature_is_404(flask_client):
    assert flask_client.get("/api/features/999/branches").status_code == 404


@pytest.mark.parametrize("bad", [
    "bad..name", "-x", "@{-1}", "HEAD", "has space", "ends/", 5])
def test_set_branch_validates_ref_format(flask_client, bad):
    fid = feature_store.create("a feature").id
    feature_store.set_branch(fid, "keep/me")

    resp = flask_client.patch(f"/api/features/{fid}", json={"branch": bad},
                              headers=EDITOR)

    assert resp.status_code == 400
    assert resp.get_json()["field"] == "branch"
    assert feature_store.require(fid).branch == "keep/me"


def test_set_branch_stores_a_valid_name_and_clears_on_empty(flask_client):
    fid = feature_store.create("a feature").id

    resp = flask_client.patch(f"/api/features/{fid}",
                              json={"branch": " release/x "}, headers=EDITOR)
    assert resp.status_code == 200
    assert resp.get_json()["branch"] == "release/x"
    assert feature_store.require(fid).branch == "release/x"

    for cleared in ("", None):
        feature_store.set_branch(fid, "release/x")
        resp = flask_client.patch(f"/api/features/{fid}",
                                  json={"branch": cleared}, headers=EDITOR)
        assert resp.status_code == 200
        assert feature_store.require(fid).branch is None


def test_a_rejected_branch_writes_no_other_field(flask_client):
    fid = feature_store.create("original").id

    resp = flask_client.patch(f"/api/features/{fid}",
                              json={"title": "changed", "branch": "bad..x"},
                              headers=EDITOR)

    assert resp.status_code == 400
    assert feature_store.require(fid).title == "original"


@pytest.mark.parametrize("params", [
    '{"worktree": "x"}', '{"worktree": {"branch": 5}}',
    '{"worktree": {"branch": "b", "base": 7}}', '[1]', 'null', 'not json'])
def test_a_malformed_run_params_row_is_skipped(
        flask_client, git_repo, native_task, params):
    fid = _feature_in(git_repo)
    task = native_task(feature_id=fid)
    _armed_run(task, 1, f"f{fid}/s1", "main")
    with SessionLocal() as session:
        session.add(AgentTaskRun(task_id=task, attempt=2, status="done",
                                 started_at="2026-10-01 11:00:00",
                                 params_json=params))
        session.commit()

    runs = _branches(flask_client, fid)["runs"]

    assert [r["branch"] for r in runs] == [f"f{fid}/s1"]


def test_a_git_timeout_reads_as_an_error_not_a_500(
        flask_client, git_repo, monkeypatch):
    import subprocess as sp
    from lib.agent_sdk import worktree

    fid = feature_store.create("unfiled").id
    real = worktree._run

    def slow(args, cwd):
        if "symbolic-ref" in args:
            raise sp.TimeoutExpired(args, 60)
        return real(args, cwd)

    monkeypatch.setattr(worktree, "_run", slow)
    monkeypatch.setattr("lib.tasks.feature_slices._repo_root",
                        lambda feature: str(git_repo))
    _git(git_repo, "branch", f"f{fid}/integration", "main")

    body = _branches(flask_client, fid)

    assert body["error"] and body["runs"] == []
