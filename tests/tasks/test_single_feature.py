"""A task belongs to at most one feature — structurally (regin:82).

The old shape reached the same rule through three refusals in `links.py`, an
`ambiguous` CTE in `lane.py`, and an exclusion clause in every reader. A single
nullable foreign key makes "two keys" unwritable, so there is nothing left for
a reader to defend against.
"""

from __future__ import annotations

from lib.features import store as feature_store
from lib.orm import SessionLocal
from lib.orm.models.tasks import AgentTask


def test_one_task_one_feature(tmp_db, native_task):
    """Re-homing a task is an UPDATE; there is no second row to write."""
    first = feature_store.create("feature A")
    second = feature_store.create("feature B")
    task_id = native_task(title="a task")

    feature_store.adopt_task(first.id, task_id)
    feature_store.adopt_task(second.id, task_id)

    with SessionLocal() as session:
        row = session.get(AgentTask, task_id)
        assert row.feature_id == second.id
    assert feature_store.tasks_of(first.id) == []
    assert [t.id for t in feature_store.tasks_of(second.id)] == [task_id]
