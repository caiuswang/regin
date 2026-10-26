"""Graph nodes carry the fix relationship (F180 Model §4, Invariant 5).

`fixes_defects` / `fixes_open_defects` are derived at read time from
`defects.fix_task_id`, through the same `rows.fix_defects` the task list uses.
"""

from __future__ import annotations

from lib.defects import store as defect_store
from lib.orm import SessionLocal
from lib.orm.models.tasks import AgentTask


def _set_kind(task_id: int, kind: str) -> None:
    with SessionLocal() as session:
        task = session.get(AgentTask, task_id)
        task.kind = kind
        session.add(task)
        session.commit()


def _nodes(client, qs: str) -> dict[int, dict]:
    response = client.get(f"/api/tasks/graph?{qs}")
    assert response.status_code == 200, response.get_json()
    return {n["id"]: n for n in response.get_json()["nodes"]}


def test_graph_node_marks_fix_task(flask_client, native_task, feature_row):
    feature = feature_row("Graph fix marks")
    fixer = native_task(title="fix it", status="armed", feature_id=feature.id)
    settled = native_task(title="fixed long ago", status="done",
                          feature_id=feature.id)
    plain = native_task(title="plain build", status="armed",
                        feature_id=feature.id)
    _set_kind(fixer, "fix")
    open_d = defect_store.record("button overlaps", feature_id=feature.id)
    closed_d = defect_store.record("old crash", feature_id=feature.id)
    defect_store.set_fix_item(open_d.id, fixer)
    defect_store.set_fix_item(closed_d.id, settled)
    defect_store.close(closed_d.id, missing_check="graph e2e")

    nodes = _nodes(flask_client, f"lanes=1&open=0&feature_id={feature.id}")

    assert nodes[fixer]["kind"] == "fix"
    assert nodes[fixer]["fixes_defects"] == [open_d.id]
    assert nodes[fixer]["fixes_open_defects"] == [open_d.id]
    assert nodes[settled]["fixes_defects"] == [closed_d.id]
    assert nodes[settled]["fixes_open_defects"] == []
    assert nodes[plain]["fixes_defects"] == []
    assert "kind" in nodes[plain]

    plain_graph = _nodes(flask_client, f"root={fixer}&depth=1")
    assert plain_graph[fixer]["fixes_open_defects"] == [open_d.id]
    assert plain_graph[fixer]["kind"] == "fix"
