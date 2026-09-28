"""The task feed by next step, and the per-status board (feature #110, S1)."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest
from sqlmodel import text

from lib.orm import SessionLocal
from lib.orm.models.tasks import AgentTaskDep


def _depends(task_id: int, on: int, kind: str = "requires"):
    with SessionLocal() as session:
        session.add(AgentTaskDep(task_id=task_id, depends_on_id=on, kind=kind))
        session.commit()


def _priority(task_id: int, priority: str, rank: int):
    with SessionLocal() as session:
        session.exec(text("UPDATE agent_tasks SET priority = :p,"
                          " priority_rank = :r WHERE id = :i")
                     .bindparams(p=priority, r=rank, i=task_id))
        session.commit()


def _get(client, url):
    response = client.get(url)
    assert response.status_code == 200, response.get_json()
    return response.get_json()


def _groups(body) -> dict:
    return {group["key"]: group for group in body["groups"]}


@pytest.fixture
def backlog(native_task, task_run_row):
    """One task in every next-step position. Returns the ids by role."""
    ids = {"running": native_task(title="running", status="running")}
    task_run_row(ids["running"], trace_id="t-live", status="running",
                 ended_at=None)
    ids["blocked"] = native_task(title="blocked", status="armed")
    _depends(ids["blocked"], ids["running"])
    ids["blocked2"] = native_task(title="also behind", status="armed")
    _depends(ids["blocked2"], ids["running"])
    ids["low"] = native_task(title="low ready", status="armed")
    _priority(ids["low"], "low", 3)
    ids["urgent"] = native_task(title="urgent ready", status="armed")
    _priority(ids["urgent"], "urgent", 0)
    ids["ideas"] = [native_task(title=f"idea {i}") for i in range(5)]
    for status in ("done", "archived"):
        native_task(title=status, status=status)
    ids["failed"] = native_task(title="failed", status="failed")
    return ids


def _ids(rows) -> list[int]:
    return [r["id"] for r in rows]


def test_tasks_next_step_groups(flask_client, backlog):
    body = _get(flask_client, "/api/tasks?group=next_step")
    groups = _groups(body)

    assert list(groups) == ["ready", "running", "blocked", "failed", "ideas"]
    assert [groups[k]["n"] for k in groups] == [2, 1, 2, 1, 5]
    assert (body["open"], body["done_hidden"]) == (11, 1)
    assert _ids(groups["ready"]["rows"]) == [backlog["urgent"], backlog["low"]]
    assert groups["ready"]["bulk"] == 2
    assert set(_ids(groups["blocked"]["rows"])) == {
        backlog["blocked"], backlog["blocked2"]}
    assert (len(groups["ideas"]["rows"]), groups["ideas"]["more"]) == (3, 2)


def test_next_step_rows_offer_one_action(flask_client, backlog):
    groups = _groups(_get(flask_client, "/api/tasks?group=next_step"))
    actions = {k: {r["action"] for r in g["rows"]} for k, g in groups.items()}
    running = groups["running"]["rows"][0]

    assert actions == {"ready": {"run"}, "running": {"watch"},
                       "blocked": {None}, "failed": {"retry"},
                       "ideas": {"arm"}}
    assert running["live"]["trace_id"] == "t-live"
    assert groups["blocked"]["rows"][0]["waits_on"] == {
        "id": backlog["running"], "status": "running"}


def test_next_step_pages_one_group_and_honours_filters(flask_client,
                                                       backlog):
    page = _get(flask_client,
                "/api/tasks?group=next_step&only=ideas&size=2&page=2")
    searched = _groups(_get(flask_client,
                            "/api/tasks?group=next_step&q=ready&status=idea"))

    assert [g["key"] for g in page["groups"]] == ["ideas"]
    assert _ids(page["groups"][0]["rows"]) == [backlog["ideas"][4]]
    assert (page["groups"][0]["more"], page["open"]) == (0, 11)
    # Every non-status filter narrows the counts; status is the groups' own.
    assert [searched[k]["n"] for k in searched] == [2, 0, 0, 0, 0]


def test_next_step_rejects_an_unknown_group(flask_client):
    response = flask_client.get("/api/tasks?group=next_step&only=someday")

    assert response.status_code == 400
    assert response.get_json()["field"] == "only"


def test_next_step_rows_carry_feature_step(flask_client, native_task,
                                           feature_row):
    feature = feature_row("f")
    first = native_task(title="s1", status="done", feature_id=feature.id)
    second = native_task(title="s2", status="armed", feature_id=feature.id)
    _depends(second, first)

    ready = _groups(_get(flask_client, "/api/tasks?group=next_step"))["ready"]

    assert ready["rows"][0]["step"] == {"n": 2, "of": 2}
    assert ready["rows"][0]["feature_title"] == "f"


@pytest.fixture
def board_rows(native_task):
    ids = {"ideas": [native_task(title=f"idea {i}") for i in range(4)]}
    ids["armed"] = native_task(title="armed", status="armed")
    waiting = native_task(title="waiting", status="armed")
    _depends(waiting, ids["armed"])
    native_task(title="failed", status="failed")
    native_task(title="done", status="done")
    return ids


def test_board_columns_count_and_cap_on_their_own(flask_client, board_rows):
    board = _get(flask_client, "/api/tasks/board?size=3")
    columns = {c["status"]: c for c in board["columns"]}
    idea = columns["idea"]

    assert list(columns) == ["idea", "armed", "running", "done"]
    assert (idea["n"], len(idea["cards"]), idea["more"]) == (4, 3, 1)
    assert columns["armed"]["blocked"] == 1
    assert board["failed"] == 1


def test_board_pages_one_column(flask_client, board_rows):
    more = _get(flask_client, "/api/tasks/board?column=idea&size=3&page=1")
    failed = _get(flask_client, "/api/tasks/board?column=failed")
    refused = flask_client.get("/api/tasks/board?column=archived")

    assert [c["status"] for c in more["columns"]] == ["idea"]
    assert _ids(more["columns"][0]["cards"]) == [board_rows["ideas"][3]]
    assert failed["columns"][0]["n"] == 1
    assert refused.status_code == 400


def _stamp(days_ago: float) -> str:
    moment = datetime.now(timezone.utc) - timedelta(days=days_ago)
    return moment.strftime("%Y-%m-%d %H:%M:%S")


def _touched(task_id: int, days_ago: float):
    with SessionLocal() as session:
        session.exec(text("UPDATE agent_tasks SET updated_at = :u WHERE id = :i")
                     .bindparams(u=_stamp(days_ago), i=task_id))
        session.commit()


def test_task_board_done_week(flask_client, native_task, task_run_row):
    tag = json.dumps(["wk"])
    recent_run = native_task(title="recent run", status="done", tags_json=tag)
    task_run_row(recent_run, ended_at=_stamp(2))
    _touched(recent_run, 30)
    old_run = native_task(title="old run", status="done", tags_json=tag)
    task_run_row(old_run, ended_at=_stamp(10))
    retried = native_task(title="retried", status="done", tags_json=tag)
    task_run_row(retried, attempt=1, ended_at=_stamp(12))
    task_run_row(retried, attempt=2, ended_at=_stamp(1))
    _touched(retried, 30)
    no_run = native_task(title="no run", status="done", tags_json=tag)
    stale = native_task(title="stale, no run", status="done", tags_json=tag)
    _touched(stale, 30)
    task_run_row(native_task(title="failed", status="failed", tags_json=tag),
                 ended_at=_stamp(1))
    native_task(title="other tag", status="done", tags_json='["other"]')

    tagged = _get(flask_client, "/api/tasks/board?tag=wk")
    everything = _get(flask_client, "/api/tasks/board")
    done = {c["status"]: c for c in tagged["columns"]}["done"]

    assert tagged["done_week"] == 3
    assert done["n"] == 5
    assert everything["done_week"] == 4
