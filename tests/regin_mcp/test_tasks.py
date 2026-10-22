"""Task tools beyond capture/read: list filters, update, deps, comments and
the run-finished report — each over the function its route calls."""

from __future__ import annotations

import io
import urllib.error

import pytest
from mcp.server.fastmcp.exceptions import ToolError
from typer.testing import CliRunner

from cli.app import app
from lib.orm import SessionLocal
from lib.orm.models.tasks import AgentTask, AgentTaskRun, TASK_STATUS_RUNNING
from lib.tasks import runs, store
from tests.regin_mcp.conftest import EDITOR


def test_list_filters_by_feature_project_and_query(call, project):
    feature = call("feature_create", title="F")["id"]
    in_f = call("task_create", title="alpha step", feature_id=feature)
    filed = call("task_create", title="beta", project=project)
    call("task_create", title="gamma")
    ids = lambda **kw: [t["id"] for t in call("task_list", **kw)["tasks"]]
    assert ids(feature_id=feature) == [in_f["id"]]
    assert ids(project=project) == [filed["id"]]
    assert ids(query="alpha") == [in_f["id"]]
    assert len(ids()) == 3
    call("task_update", task_id=filed["id"], status="archived")
    assert len(ids(status="")) == 3
    assert filed["id"] not in ids()
    with pytest.raises(ToolError, match="unknown status"):
        call("task_list", status="bogus")


def test_update_moves_a_task_under_a_feature_and_archives(call):
    feature = call("feature_create", title="F")["id"]
    task = call("task_create", title="loose")
    moved = call("task_update", task_id=task["id"], feature_id=feature,
                 priority="high", title="placed")
    assert (moved["feature_id"], moved["priority"], moved["title"]) == (
        feature, "high", "placed")
    assert [t["id"] for t in call("feature_get",
                                  feature_id=feature)["tasks"]] == [task["id"]]
    assert call("task_update", task_id=task["id"],
                status="archived")["status"] == "archived"
    detached = call("task_update", task_id=task["id"], feature_id=0)
    assert detached["feature_id"] is None


@pytest.mark.parametrize("args,needle", [
    ({"title": "  "}, "title"),
    ({"priority": "asap"}, "priority"),
    ({"feature_id": 999}, "feature_id"),
    ({"project": "nope"}, "no project named"),
])
def test_bad_update_names_the_field(call, args, needle):
    task = call("task_create", title="t")
    with pytest.raises(ToolError, match=needle):
        call("task_update", task_id=task["id"], **args)


def test_mcp_and_web_edits_land_the_same_row(call, flask_client):
    feature = call("feature_create", title="F")["id"]
    via_mcp = call("task_create", title="t")["id"]
    via_web = call("task_create", title="t")["id"]
    edit = {"title": "edited", "body": "new body", "priority": "urgent",
            "kind": "fix", "feature_id": feature}
    call("task_update", task_id=via_mcp, **edit)
    resp = flask_client.patch(f"/api/tasks/{via_web}", json=edit,
                              headers=EDITOR)
    assert resp.status_code == 200, resp.get_json()
    volatile = {"id", "created_at", "updated_at"}
    rows = [store.get_task(i) for i in (via_mcp, via_web)]
    assert [{k: v for k, v in r.items() if k not in volatile}
            for r in rows] == [
        {k: v for k, v in rows[1].items() if k not in volatile}] * 2


def test_a_dependency_cycle_is_refused_and_nothing_changes(call):
    a = call("task_create", title="A")["id"]
    b = call("task_create", title="B")["id"]
    assert call("task_set_deps", task_id=a, blockers=[b])["depends_on"] == [b]
    with pytest.raises(ToolError, match=r"pair: \[") as err:
        call("task_set_deps", task_id=b, blockers=[a])
    assert str(a) in str(err.value) and str(b) in str(err.value)
    assert call("task_get", task_id=b)["deps"] == []
    assert call("task_set_deps", task_id=a, blockers=[]) == {
        "task_id": a, "depends_on": [], "deps": []}


def test_comment_opens_replies_and_resolves(call):
    task = call("task_create", title="t")["id"]
    first = call("task_comment", task_id=task, body="question?")
    thread = first["thread"]["id"]
    assert first["comment"]["author_kind"] == "agent"
    reply = call("task_comment", task_id=task, body="answer",
                 thread_id=thread, resolve=True)
    assert [c["body"] for c in reply["thread"]["comments"]] == [
        "question?", "answer"]
    assert reply["thread"]["resolution_state"] == "resolved"
    reopened = call("task_comment", task_id=task, thread_id=thread,
                    resolve=False)
    assert (reopened["comment"], reopened["thread"]["resolution_state"]) == (
        None, "open")
    shown = call("task_get", task_id=task)["comments"]
    assert [len(t["comments"]) for t in shown] == [2]
    with pytest.raises(ToolError, match="needs a body"):
        call("task_comment", task_id=task, body=" ")
    with pytest.raises(ToolError, match="has no thread 999"):
        call("task_comment", task_id=task, body="x", thread_id=999)


def _running(call) -> tuple[int, int]:
    task_id = call("task_create", title="a slice")["id"]
    runs.arm(task_id, params={}, autorun=0)
    run = runs.open_attempt(task_id)
    store.set_status(task_id, TASK_STATUS_RUNNING)
    return task_id, run["id"]


def _outcome(task_id: int, run_id: int) -> tuple:
    with SessionLocal() as s:
        task, run = s.get(AgentTask, task_id), s.get(AgentTaskRun, run_id)
        return (task.status, run.status, run.settled_by, run.exit_reason)


def _no_server(*_a, **_k):
    raise urllib.error.URLError(ConnectionRefusedError())


def test_finish_matches_the_cli_report(call, monkeypatch):
    monkeypatch.setattr("cli.commands.tasks._open_loopback", _no_server)
    mcp_task, mcp_run = _running(call)
    cli_task, cli_run = _running(call)
    out = call("task_finish", run_id=mcp_run, status="done",
               reason="built it", task_id=mcp_task)
    result = CliRunner().invoke(app, ["task", "finish", str(cli_task),
                                      "--run", str(cli_run),
                                      "--reason", "built it", "--json"])
    assert result.exit_code == 0, result.output
    assert (out["settled"], out["task_id"]) == (True, mcp_task)
    assert _outcome(mcp_task, mcp_run) == _outcome(cli_task, cli_run)
    assert _outcome(mcp_task, mcp_run)[:2] == ("done", "done")


def test_finish_refusals(call):
    task, run = _running(call)
    other = call("task_create", title="other")["id"]
    with pytest.raises(ToolError, match=f"belongs to task #{task}"):
        call("task_finish", run_id=run, status="done", reason="r",
             task_id=other)
    with pytest.raises(ToolError, match="no run #999"):
        call("task_finish", run_id=999, status="failed", reason="r")


def test_finish_done_over_open_defects_names_them(call):
    feature = call("feature_create", title="F")["id"]
    defect = call("defect_open", symptom="broken", feature_id=feature)["id"]
    fix = call("defect_set_fix_task", defect_ids=[defect], create=True)
    task_id = fix["task_id"]
    runs.arm(task_id, params={}, autorun=0)
    run = runs.open_attempt(task_id)
    store.set_status(task_id, TASK_STATUS_RUNNING)
    with pytest.raises(ToolError, match=rf"open_defects: \[{defect}\]"):
        call("task_finish", run_id=run["id"], status="done", reason="r")


class _Reply:
    def __init__(self, data: bytes):
        self._data = data

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def read(self):
        return self._data


def test_finish_reports_to_a_running_server(call, tmp_db, monkeypatch):
    from web.app import create_app
    # Not the shared `flask_client`: that one holds its context open, and a
    # request pushed from inside the tool's event loop cannot pop it later.
    server = create_app().test_client()
    hits = []

    def forward(request, timeout=None):
        hits.append(request.full_url)
        resp = server.post(request.full_url, data=request.data,
                           content_type="application/json",
                           environ_base={"REMOTE_ADDR": "127.0.0.1"})
        if resp.status_code >= 400:
            raise urllib.error.HTTPError(request.full_url, resp.status_code,
                                         "refused", None,
                                         io.BytesIO(resp.data))
        return _Reply(resp.data)

    monkeypatch.setattr("lib.tasks.finish_report.open_loopback", forward)
    task, run = _running(call)
    out = call("task_finish", run_id=run, status="done", reason="via server")
    assert hits and hits[0].endswith(f"/api/tasks/runs/{run}/finish")
    assert (out["settled"], out["task_id"]) == (True, task)
    assert _outcome(task, run)[:2] == ("done", "done")


def test_a_server_refusal_is_relayed_with_its_open_defects(call, monkeypatch):
    def refuse(request, timeout=None):
        body = b'{"error": "fix task has open defects", "open_defects": [7]}'
        raise urllib.error.HTTPError(request.full_url, 409, "refused", None,
                                     io.BytesIO(body))

    monkeypatch.setattr("lib.tasks.finish_report.open_loopback", refuse)
    with pytest.raises(ToolError, match=r"server refused \(409\): fix task "
                                        r"has open defects \(open_defects: \[7\]\)"):
        call("task_finish", run_id=1, status="done", reason="r")
