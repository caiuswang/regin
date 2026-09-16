"""Deleting a task — the irreversible counterpart to archiving.

Archiving files a task away and keeps it; delete removes the row and
everything keyed to it. The two are not interchangeable, which is why these
tests pin the cascade explicitly: a left-behind `agent_task_deps` edge whose
`depends_on_id` no longer resolves blocks its dependent forever, and nothing
downstream would report why.
"""

from __future__ import annotations

import json

import pytest
from sqlmodel import select

from lib.auth import create_token
from lib.orm import SessionLocal
from lib.orm.models.tasks import (
    AgentTask, AgentTaskDep, AgentTaskImage, AgentTaskLink, AgentTaskRun,
)
from lib.tasks import deps, runs, store

EDITOR = {"Authorization": f"Bearer {create_token(2, 'ed', 'editor')}"}
VIEWER = {"Authorization": f"Bearer {create_token(3, 'vw', 'viewer')}"}


@pytest.fixture
def no_live_runs(monkeypatch):
    """No agent is reachable — the state in which a delete is allowed."""
    monkeypatch.setattr(runs, "live_run", lambda task_id: None)


def _populate(task_id: int) -> None:
    """One row in every table keyed to a task."""
    with SessionLocal() as session:
        session.add(AgentTaskRun(task_id=task_id, attempt=1, status="done"))
        session.add(AgentTaskImage(task_id=task_id, idx=0, media_type="image/png",
                                   bytes_=b"x", byte_size=1, sha256="abc"))
        session.add(AgentTaskLink(task_id=task_id, provider="linear",
                                  external_key="CAI-1",
                                  url="https://example.test/CAI-1"))
        session.commit()


def _counts(task_id: int) -> dict[str, int]:
    with SessionLocal() as session:
        return {
            "runs": len(session.exec(select(AgentTaskRun).where(
                AgentTaskRun.task_id == task_id)).all()),
            "images": len(session.exec(select(AgentTaskImage).where(
                AgentTaskImage.task_id == task_id)).all()),
            "links": len(session.exec(select(AgentTaskLink).where(
                AgentTaskLink.task_id == task_id)).all()),
            "deps": len(session.exec(select(AgentTaskDep).where(
                (AgentTaskDep.task_id == task_id)
                | (AgentTaskDep.depends_on_id == task_id))).all()),
        }


# ── the store ────────────────────────────────────────────────

def test_delete_removes_the_row_and_returns_what_was_deleted(tmp_db,
                                                             no_live_runs):
    task, _ = store.capture_task(title="throwaway", source="cli")

    deleted = store.delete_task(task["id"])

    assert deleted["id"] == task["id"]
    assert deleted["title"] == "throwaway"
    assert store.get_task(task["id"]) is None


def test_delete_of_an_unknown_task_is_none_not_an_error(tmp_db, no_live_runs):
    assert store.delete_task(4321) is None


def test_delete_cascades_runs_images_and_links(tmp_db, no_live_runs):
    task, _ = store.capture_task(title="has children", source="cli")
    _populate(task["id"])
    assert _counts(task["id"]) == {"runs": 1, "images": 1, "links": 1, "deps": 0}

    store.delete_task(task["id"])

    assert _counts(task["id"]) == {"runs": 0, "images": 0, "links": 0, "deps": 0}


def test_delete_removes_edges_in_both_directions(tmp_db, no_live_runs):
    """The dependent's edge goes too, or it waits on an id that cannot resolve."""
    blocker, _ = store.capture_task(title="blocker", source="cli")
    dependent, _ = store.capture_task(title="dependent", source="cli")
    downstream, _ = store.capture_task(title="downstream", source="cli")
    deps.set_deps(dependent["id"], [blocker["id"]])
    deps.set_deps(blocker["id"], [downstream["id"]])

    store.delete_task(blocker["id"])

    assert deps.depends_on(dependent["id"]) == []
    assert deps.blocking(dependent["id"]) == []
    assert deps.dependents(downstream["id"]) == []


def test_delete_leaves_every_other_task_untouched(tmp_db, no_live_runs):
    keep, _ = store.capture_task(title="keep", source="cli")
    drop, _ = store.capture_task(title="drop", source="cli")
    _populate(keep["id"])
    _populate(drop["id"])

    store.delete_task(drop["id"])

    assert store.get_task(keep["id"])["title"] == "keep"
    assert _counts(keep["id"]) == {"runs": 1, "images": 1, "links": 1, "deps": 0}


def test_delete_is_refused_while_a_run_is_live(tmp_db, monkeypatch):
    """The same probe that blocks archiving — an agent still reachable."""
    task, _ = store.capture_task(title="running", source="cli")
    monkeypatch.setattr(runs, "live_run", lambda task_id: {"id": 1})

    with pytest.raises(store.DeleteRefused) as excinfo:
        store.delete_task(task["id"])

    assert "live run" in str(excinfo.value)
    assert store.get_task(task["id"]) is not None


def test_a_stale_running_claim_does_not_block_delete(tmp_db, no_live_runs):
    """`status == 'running'` outlives the process that wrote it; only a
    reachable agent blocks the delete."""
    task, _ = store.capture_task(title="stale", source="cli")
    store.set_status(task["id"], "running")

    assert store.delete_task(task["id"])["id"] == task["id"]


# ── bulk ─────────────────────────────────────────────────────

def test_bulk_delete_reports_deleted_and_names_what_it_refused(tmp_db,
                                                               monkeypatch):
    alive, _ = store.capture_task(title="live one", source="cli")
    gone_a, _ = store.capture_task(title="a", source="cli")
    gone_b, _ = store.capture_task(title="b", source="cli")
    monkeypatch.setattr(
        runs, "live_run",
        lambda task_id: {"id": 1} if task_id == alive["id"] else None)

    result = store.bulk_delete([gone_a["id"], alive["id"], gone_b["id"], 9999])

    assert result["deleted"] == [gone_a["id"], gone_b["id"]]
    assert [r["id"] for r in result["rejected"]] == [alive["id"], 9999]
    assert store.get_task(alive["id"]) is not None


# ── the HTTP surface ─────────────────────────────────────────

def test_editor_deletes_a_task_over_http(flask_client, no_live_runs):
    task, _ = store.capture_task(title="delete me", source="web")

    resp = flask_client.delete(f"/api/tasks/{task['id']}", headers=EDITOR)

    assert resp.status_code == 200, resp.get_json()
    assert resp.get_json()["deleted"]["id"] == task["id"]
    assert store.get_task(task["id"]) is None


def test_viewer_cannot_delete_and_anonymous_cannot_either(flask_client,
                                                          anon_client,
                                                          no_live_runs):
    task, _ = store.capture_task(title="protected", source="web")

    assert flask_client.delete(f"/api/tasks/{task['id']}",
                               headers=VIEWER).status_code == 403
    assert anon_client.delete(f"/api/tasks/{task['id']}").status_code == 401
    assert store.get_task(task["id"]) is not None


def test_deleting_an_unknown_task_is_404(flask_client, no_live_runs):
    assert flask_client.delete("/api/tasks/9999",
                               headers=EDITOR).status_code == 404


def test_http_delete_of_a_live_task_is_refused_with_the_reason(flask_client,
                                                               monkeypatch):
    task, _ = store.capture_task(title="running", source="web")
    monkeypatch.setattr(runs, "live_run", lambda task_id: {"id": 1})

    resp = flask_client.delete(f"/api/tasks/{task['id']}", headers=EDITOR)

    assert resp.status_code == 409
    assert "live run" in resp.get_json()["error"]
    assert store.get_task(task["id"]) is not None


def test_bulk_delete_over_http(flask_client, no_live_runs):
    first, _ = store.capture_task(title="a", source="web")
    second, _ = store.capture_task(title="b", source="web")

    resp = flask_client.delete("/api/tasks/bulk",
                               json={"ids": [first["id"], second["id"]]},
                               headers=EDITOR)

    assert resp.status_code == 200, resp.get_json()
    assert resp.get_json()["deleted"] == [first["id"], second["id"]]
    assert store.list_tasks() == []


def test_bulk_delete_refuses_a_body_that_is_not_a_list_of_ids(flask_client):
    resp = flask_client.delete("/api/tasks/bulk", json={"ids": "all"},
                               headers=EDITOR)
    assert resp.status_code == 400
    assert resp.get_json()["field"] == "ids"


# ── the CLI ──────────────────────────────────────────────────

def test_cli_delete_removes_the_task(tmp_db, no_live_runs):
    from typer.testing import CliRunner

    from cli.commands.tasks import tasks_app

    task, _ = store.capture_task(title="from the cli", source="cli")

    result = CliRunner().invoke(
        tasks_app, ["delete", str(task["id"]), "--yes", "--json"])

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["id"] == task["id"]
    assert store.get_task(task["id"]) is None


def test_cli_delete_of_an_unknown_task_exits_nonzero(tmp_db, no_live_runs):
    from typer.testing import CliRunner

    from cli.commands.tasks import tasks_app

    result = CliRunner().invoke(tasks_app, ["delete", "9999", "--yes"])

    assert result.exit_code == 1
    assert "9999" in result.output


def test_cli_delete_without_confirmation_leaves_the_task(tmp_db, no_live_runs):
    from typer.testing import CliRunner

    from cli.commands.tasks import tasks_app

    task, _ = store.capture_task(title="keep me", source="cli")

    result = CliRunner().invoke(tasks_app, ["delete", str(task["id"])],
                                input="n\n")

    assert result.exit_code == 1
    assert store.get_task(task["id"]) is not None
