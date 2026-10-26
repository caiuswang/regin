"""A viewer reads the task graph (F180 Rules §5): the feature page's preview
card draws for every signed-in role, so the read endpoint is `require_auth`,
like `GET /api/tasks`. Writes stay editor-only."""

from __future__ import annotations

from lib.auth import create_token
from lib.orm import SessionLocal
from lib.orm.models.tasks import AgentTask


def _viewer() -> dict:
    return {"Authorization": f"Bearer {create_token(3, 'vw', 'viewer')}"}


def _task(title: str) -> int:
    with SessionLocal() as session:
        row = AgentTask(title=title, status="armed", source="test")
        session.add(row)
        session.commit()
        session.refresh(row)
        return row.id


def test_viewer_can_read_task_graph(flask_client, tmp_db):
    task_id = _task("drawn for a viewer")

    lanes = flask_client.get("/api/tasks/graph?lanes=1&open=0",
                             headers=_viewer())
    plain = flask_client.get("/api/tasks/graph", headers=_viewer())

    assert lanes.status_code == 200, lanes.get_json()
    assert task_id in [n["id"] for n in lanes.get_json()["nodes"]]
    assert plain.status_code == 200


def test_viewer_still_cannot_write_task_deps(flask_client, tmp_db):
    a, b = _task("a"), _task("b")

    response = flask_client.put(f"/api/tasks/{a}/deps",
                                json={"depends_on": [b]}, headers=_viewer())

    assert response.status_code == 403
