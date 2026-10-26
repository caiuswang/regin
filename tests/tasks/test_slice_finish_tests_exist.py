"""A build slice cannot finish done while a test it is named for is missing."""

from __future__ import annotations

import subprocess

from typer.testing import CliRunner

from cli.app import app
from cli.commands import tasks as tasks_cli
from lib.features import store as feature_store
from lib.orm import SessionLocal
from lib.orm.models.tasks import AgentTaskRun, TASK_STATUS_RUNNING
from lib.tasks import feature_slices, runs, store
from tests.spec_fixtures import gherkin, row, spec

runner = CliRunner()


def _checkout(tmp_path):
    root = tmp_path / "checkout"
    (root / "tests").mkdir(parents=True)
    (root / "tests" / "test_s1.py").write_text("def test_a():\n    pass\n")
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    return root


def _running_slice(root) -> tuple[int, int]:
    body = spec(gherkin("test_a", "test_b", "test_c"),
                row("S1", "test_a, test_b"), row("S2", "test_c", "S1"))
    fid = feature_store.create("finishing", body=body).id
    result = feature_slices.transcribe_feature(fid)
    task_id = result.outcomes[0].task_id
    runs.arm(task_id, params={"cwd": str(root)}, autorun=0)
    run = runs.open_attempt(task_id)
    store.set_status(task_id, TASK_STATUS_RUNNING)
    with SessionLocal() as session:
        attempt = session.get(AgentTaskRun, run["id"])
        attempt.status = "running"
        session.add(attempt)
        session.commit()
    return task_id, run["id"]


def _no_server(request, timeout):
    raise ConnectionRefusedError("regin is not running")


def _finish(task_id, run_id, status):
    return runner.invoke(app, ["task", "finish", str(task_id), "--run", str(run_id),
                               "--status", status, "--reason", "r"])


def _run_status(run_id) -> str:
    with SessionLocal() as session:
        return session.get(AgentTaskRun, run_id).status


def test_slice_finish_refused_when_named_test_missing(tmp_db, tmp_path, monkeypatch):
    monkeypatch.setattr(tasks_cli, "_open_loopback", _no_server)
    task_id, run_id = _running_slice(_checkout(tmp_path))

    refused = _finish(task_id, run_id, "done")
    assert refused.exit_code == 1
    assert "test_b" in refused.output
    assert "test_a," not in refused.output.split("exist in")[-1]
    assert _run_status(run_id) == "running"

    accepted = _finish(task_id, run_id, "failed")
    assert accepted.exit_code == 0, accepted.output
    assert _run_status(run_id) == "failed"


def test_slice_finish_accepted_once_its_tests_exist(tmp_db, tmp_path, monkeypatch):
    monkeypatch.setattr(tasks_cli, "_open_loopback", _no_server)
    root = _checkout(tmp_path)
    (root / "tests" / "test_more.py").write_text("def test_b():\n    pass\n")
    task_id, run_id = _running_slice(root)

    assert _finish(task_id, run_id, "done").exit_code == 0
    assert _run_status(run_id) == "done"


def test_slice_finish_reads_the_runs_worktree_not_its_cwd(tmp_db, tmp_path, monkeypatch):
    """The named tests are looked up in the worktree the run used; a worktree
    that is gone refuses rather than falling back to the main checkout."""
    from lib.agent_sdk import worktree
    from lib.tasks import slice_tests

    main = _checkout(tmp_path)
    tree = tmp_path / "tree"
    (tree / "tests").mkdir(parents=True)
    (tree / "tests" / "test_s1.py").write_text(
        "def test_a():\n    pass\n\n\ndef test_b():\n    pass\n")
    found = {"tree": {"path": str(tree)}}
    monkeypatch.setattr(worktree, "existing_worktree",
                        lambda cwd, branch: found.get("tree"))
    task_id, run_id = _running_slice(main)
    with SessionLocal() as session:
        attempt = session.get(AgentTaskRun, run_id)
        attempt.params_json = (
            '{"cwd": "%s", "worktree": {"branch": "f1/s1"}}' % main)
        session.add(attempt)
        session.commit()

    assert slice_tests.missing_tests(task_id, run_id) == ([], tree)

    found.clear()
    try:
        runs.finish_run(run_id, status="done", in_server=False)
    except runs.RunRefused as exc:
        assert "no longer registered" in str(exc)
    else:
        raise AssertionError("finish over a vanished worktree was accepted")
    assert _run_status(run_id) == "running"
