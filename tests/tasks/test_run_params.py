"""A rerun with different parameters is a run, not a child task (regin:82).

`params_json` / `verify_cmd` / `autorun` lived on the task, so the only way to
attempt the same work with different parameters was to create another row —
which is how "a sub-task is a run" became a shape the model had to carry.
"""

from __future__ import annotations

import json

from lib.orm import SessionLocal
from lib.orm.models.tasks import AgentTask, AgentTaskRun
from lib.tasks import runs
from sqlmodel import select


def test_reparametrised_rerun_creates_a_run_not_a_task(tmp_db, native_task):
    """Two runs, two parameter sets, one task."""
    task_id = native_task(title="a task")

    first = runs.open_attempt(task_id, params={"model": "opus"})
    second = runs.open_attempt(task_id, params={"model": "haiku"})

    with SessionLocal() as session:
        rows = list(session.exec(
            select(AgentTaskRun).where(AgentTaskRun.task_id == task_id)
            .order_by(AgentTaskRun.id)).all())
        tasks = list(session.exec(select(AgentTask)).all())
    assert [row.id for row in rows] == [first["id"], second["id"]]
    assert [json.loads(row.params_json)["model"] for row in rows] == [
        "opus", "haiku"]
    assert [row.id for row in tasks] == [task_id]
