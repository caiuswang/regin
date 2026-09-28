"""R7 — every pipeline write raises one `pipeline` frame, and nothing else does."""

from __future__ import annotations

import queue

import pytest
from typer.testing import CliRunner

from lib.notifications import hub
from lib.pipeline import live


@pytest.fixture
def frames(tmp_db):
    """A stream declared on the `pipeline` topic; call it to drain its frames."""
    q, _ = hub.subscribe(user_id=1, role="viewer", stream_id="s-live")
    hub.set_topics("s-live", 1, [live.PIPELINE_TOPIC])

    def _drain():
        out = []
        while True:
            try:
                name, payload = q.get_nowait()
            except queue.Empty:
                return out
            if name == live.PIPELINE_EVENT:
                out.append(payload)
    yield _drain
    hub.unsubscribe(q)


def _keys(frames):
    return sorted((f["entity"], f["id"]) for f in frames)


def test_pipeline_events_broadcast_on_writes(frames):
    _feature_writes(frames)
    _task_writes(frames)
    _defect_writes(frames)


def _feature_writes(frames):
    from lib.features import events as feature_events
    from lib.features import store as features

    feature = features.create("live refresh", project_id=None)
    assert _keys(frames()) == [("feature", feature.id)]

    features.rename(feature.id, "live refresh 2")
    assert _keys(frames()) == [("feature", feature.id)]

    feature_events.append(feature.id, feature_events.SHIPPED,
                          declared_by="operator")
    assert _keys(frames()) == [("feature", feature.id)]


def _task_writes(frames):
    from lib.tasks import runs, store as tasks

    task, _ = tasks.capture_task(title="t", source="test")
    assert _keys(frames()) == [("task", task["id"])]

    tasks.update_task(task["id"], {"priority": "high"})
    assert _keys(frames()) == [("task", task["id"])]

    run = runs.open_attempt(task["id"])
    assert _keys(frames()) == [("task", task["id"])]
    tasks.set_status(task["id"], "running")
    assert _keys(frames()) == [("task", task["id"])]
    runs.complete_run(run["id"], status="done", task_status="done")
    assert _keys(frames()) == [("task", task["id"])]
    tasks.delete_task(task["id"])
    assert _keys(frames()) == [("task", task["id"])]


def _defect_writes(frames):
    from lib.defects import store as defects

    defect = defects.record("it broke")
    assert _keys(frames()) == [("defect", defect.id)]
    defects.snooze(defect.id)
    assert _keys(frames()) == [("defect", defect.id)]


def test_frame_names_the_rows_project(frames, tmp_db):
    from lib.defects import store as defects
    from lib.features import store as features
    from lib.orm import SessionLocal
    from lib.orm.models.sync import Repo

    with SessionLocal() as s:
        repo = Repo(name="p", path="/tmp/p-live")
        s.add(repo)
        s.commit()
        s.refresh(repo)
    frames()
    feature = features.create("scoped", project_id=repo.id)
    defect = defects.record("scoped defect", feature_id=feature.id)
    got = frames()
    assert {(f["entity"], f["project_id"]) for f in got} >= {
        ("feature", repo.id), ("defect", repo.id)}
    assert all(f["id"] in (feature.id, defect.id) for f in got)


def test_rolled_back_write_raises_nothing(frames):
    from lib.orm import SessionLocal
    from lib.orm.models.tasks import AgentTask

    with SessionLocal() as s:
        s.add(AgentTask(title="never", status="idea", source="test"))
        s.flush()
        s.rollback()
    assert frames() == []


def test_bulk_run_updates_raise_the_tasks_frame(frames, monkeypatch):
    """A statement-level `update(AgentTaskRun)` bypasses the flush, so the
    verify claim and the reopen have to announce their task themselves."""
    from datetime import datetime, timezone

    from lib.orm import SessionLocal
    from lib.orm.models.tasks import AgentTaskRun
    from lib.tasks import runs, store as tasks

    task, _ = tasks.capture_task(title="bulk", source="test")
    run = runs.open_attempt(task["id"])
    with SessionLocal() as s:
        s.get(AgentTaskRun, run["id"]).verify_cmd = "true"
        s.commit()
    frames()

    assert runs._claim_verify(run["id"])[0] == runs.VERIFY_MINE
    assert _keys(frames()) == [("task", task["id"])]

    with SessionLocal() as s:
        s.get(AgentTaskRun, run["id"]).ended_at = datetime.now(timezone.utc)
        s.commit()
    tasks.set_status(task["id"], "failed")
    frames()
    monkeypatch.setattr(runs.store, "set_status", lambda *a, **k: None)

    assert runs.reopen_for_agent_report(run["id"]) is True
    assert _keys(frames()) == [("task", task["id"])]


def test_unrelated_write_raises_nothing(frames):
    from lib.orm import SessionLocal
    from lib.orm.models.sync import Repo

    with SessionLocal() as s:
        s.add(Repo(name="r", path="/tmp/r-live"))
        s.commit()
    assert frames() == []


def test_frame_is_addressed_to_the_topic(tmp_db):
    from lib.tasks import store as tasks

    q, _ = hub.subscribe(user_id=2, role="viewer", stream_id="s-other")
    try:
        tasks.capture_task(title="t", source="test")
        assert q.empty()
    finally:
        hub.unsubscribe(q)


def test_cli_write_goes_over_the_loopback(frames, monkeypatch):
    from cli.app import app

    sent = []
    monkeypatch.setattr(live, "_loopback", sent.extend)
    result = CliRunner().invoke(app, ["task", "add", "from the cli"])
    assert result.exit_code == 0, result.output
    assert [f["entity"] for f in sent] == ["task"]
    assert frames() == []


def test_loopback_route_reraises_valid_frames(frames, flask_client):
    body = {"pipeline": [
        {"entity": "task", "id": 7, "project_id": None},
        {"entity": "bogus", "id": 1},
        {"entity": "defect", "id": "x"},
    ]}
    resp = flask_client.post("/api/internal/notify", json=body)
    assert resp.status_code == 200
    assert frames() == [{"entity": "task", "id": 7, "project_id": None}]


def test_notify_pipeline_posts_the_frames(monkeypatch):
    from lib.notifications import notify

    posted = []
    monkeypatch.setattr(notify, "_post_notify",
                        lambda port, body: posted.append(body))
    notify.notify_pipeline([{"entity": "task", "id": 1, "project_id": None}])
    notify.notify_pipeline([])
    assert posted == [{"pipeline": [
        {"entity": "task", "id": 1, "project_id": None}]}]
