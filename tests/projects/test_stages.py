"""A project's step stages — feature #148, part B (slice S1).

One test per acceptance scenario the slice owns, plus the edges they lean on.
"""

from __future__ import annotations

import sqlite3
from types import SimpleNamespace

import pytest
from alembic import command
from sqlmodel import select

from lib import db_migrate
from lib.auth import create_token
from lib.features import events, store as feature_store
from lib.orm import SessionLocal
from lib.orm.models.prompts import PromptTemplate
from lib.orm.models.stages import ProjectStage
from lib.orm.models.sync import Repo
from lib.orm.models.tasks import AgentTask
from lib.tasks import runs

VIEWER = {"Authorization": f"Bearer {create_token(3, 'vw', 'viewer')}"}
EDITOR = {"Authorization": f"Bearer {create_token(2, 'ed', 'editor')}"}


@pytest.fixture()
def project(tmp_db) -> int:
    with SessionLocal() as session:
        row = Repo(name="regin", path="/tmp/regin")
        session.add(row)
        session.commit()
        session.refresh(row)
        return row.id


@pytest.fixture
def launcher(monkeypatch):
    state = SimpleNamespace(calls=[])

    def _fake(prompt, **kwargs):
        state.calls.append(prompt)
        return SimpleNamespace(trace_id=f"sdk-fake{len(state.calls)}",
                               add_done_callback=lambda callback: None)

    monkeypatch.setattr(runs.supervisor, "launch_run", _fake)
    return state


def _rows(project_id):
    with SessionLocal() as session:
        clause = (ProjectStage.project_id.is_(None) if project_id is None
                  else ProjectStage.project_id == project_id)
        return [(r.key, r.label, r.role, r.position, r.builtin, r.prompt_slug)
                for r in session.exec(select(ProjectStage).where(clause)
                                      .order_by(ProjectStage.position)).all()]


def _feature(project_id, title="staged"):
    row = feature_store.create(title)
    if project_id is not None:
        feature_store.set_project(row.id, project_id)
    return row


def _add_design(client, project_id, **extra):
    resp = client.post(f"/api/projects/{project_id}/stages", json={
        "key": "design", "label": "Design", "role": "spec", **extra})
    assert resp.status_code == 201, resp.get_json()
    return resp.get_json()["stage"]


def _step(client, feature_id, **payload):
    return client.post(f"/api/features/{feature_id}/steps", json=payload)


def _run(client, launcher, task_id) -> str:
    assert client.post(f"/api/tasks/{task_id}/arm", json={}).status_code == 200
    resp = client.post(f"/api/tasks/{task_id}/run")
    assert resp.status_code == 201, resp.get_json()
    return launcher.calls[-1]


def test_unconfigured_project_reads_default_stages(flask_client, project):
    body = flask_client.get(f"/api/projects/{project}/stages").get_json()

    assert body["inherited"] is True
    assert [(s["key"], s["label"], s["role"], s["builtin"])
            for s in body["stages"]] == [("spec", "Spec", "spec", True),
                                         ("build", "Build", "build", True)]
    assert _rows(project) == []


def test_default_scope_is_addressable(flask_client):
    body = flask_client.get("/api/projects/default/stages").get_json()

    assert body["project_id"] is None and body["inherited"] is False
    assert [s["key"] for s in body["stages"]] == ["spec", "build"]
    assert flask_client.get("/api/projects/9999/stages").status_code == 404
    assert flask_client.get("/api/projects/nope/stages").status_code == 404


def test_add_stage_copies_defaults_on_write(flask_client, project):
    defaults = _rows(None)

    stage = _add_design(flask_client, project)

    assert stage["key"] == "design" and stage["builtin"] is False
    assert [r[0] for r in _rows(project)] == ["spec", "build", "design"]
    assert _rows(None) == defaults
    copied = {r[0]: r[5] for r in _rows(project)}
    assert copied["spec"] == f"stage-{project}-spec"
    with SessionLocal() as session:
        global_spec = session.exec(select(PromptTemplate).where(
            PromptTemplate.slug == "stage-global-spec")).one()
        own_spec = session.exec(select(PromptTemplate).where(
            PromptTemplate.slug == f"stage-{project}-spec")).one()
    assert own_spec.body == global_spec.body
    listed = flask_client.get(f"/api/projects/{project}/stages").get_json()
    assert listed["inherited"] is False


def test_editing_a_project_prompt_leaves_the_default_alone(flask_client,
                                                           project):
    resp = flask_client.patch(f"/api/projects/{project}/stages/spec",
                              json={"label": "Specify", "prompt": "mine"})

    assert resp.status_code == 200, resp.get_json()
    assert resp.get_json()["stage"]["prompt_body"] == "mine"
    default = flask_client.get("/api/projects/default/stages").get_json()
    spec = default["stages"][0]
    assert (spec["label"], spec["prompt_body"]) == (
        "Spec", "{{include:task-run-spec}}")


def test_custom_stage_step_injects_stage_prompt(flask_client, project,
                                                launcher):
    _add_design(flask_client, project)
    resp = flask_client.patch(
        f"/api/projects/{project}/stages/design",
        json={"prompt": "只出设计，不写代码 {{stage_label}}"})
    assert resp.status_code == 200, resp.get_json()
    feature = _feature(project)

    first = _step(flask_client, feature.id, stage="design")
    again = _step(flask_client, feature.id, stage="design")

    assert first.status_code == 201, first.get_json()
    task = first.get_json()["task"]
    assert (task["external_id"], task["kind"], task["stage_key"]) == (
        f"feature:{feature.id}:design", "spec", "design")
    assert task["title"].startswith("Design · ")
    assert again.status_code == 200
    assert again.get_json()["created"] is False
    assert again.get_json()["task"]["id"] == task["id"]
    prompt = _run(flask_client, launcher, task["id"])
    assert "只出设计，不写代码 Design" in prompt
    assert "<spec>" not in prompt


def test_default_stages_prompt_as_before(flask_client, project, launcher):
    feature = _feature(project)
    spec = _step(flask_client, feature.id, stage="spec").get_json()["task"]
    build = _step(flask_client, feature.id, stage="build").get_json()["task"]

    assert "<spec>" in _run(flask_client, launcher, spec["id"])
    assert "<spec>" not in _run(flask_client, launcher, build["id"])


def test_a_broken_stage_prompt_falls_back(flask_client, project, launcher):
    flask_client.patch(f"/api/projects/{project}/stages/spec",
                       json={"prompt": "{{no_such_variable}}"})
    feature = _feature(project)
    spec = _step(flask_client, feature.id, stage="spec").get_json()["task"]

    prompt = _run(flask_client, launcher, spec["id"])

    assert "<spec>" in prompt and "no_such_variable" not in prompt


@pytest.fixture
def at_0037(tmp_db):
    """`tmp_db` set back to the pre-0038 shape and stamped there, holding a
    feature and its spec step as the old verb created it."""
    conn = sqlite3.connect(str(tmp_db))
    try:
        conn.execute("DROP TABLE project_stages")
        conn.execute("DELETE FROM prompt_templates WHERE kind = 'stage'")
        conn.execute("ALTER TABLE agent_tasks DROP COLUMN stage_key")
        conn.execute("INSERT INTO features (id, title) VALUES (7, 'F')")
        conn.execute(
            "INSERT INTO agent_tasks (id, title, source, external_id, "
            "feature_id, kind) VALUES "
            "(1, 'Spec · F', 'feature', 'feature:7:spec', 7, 'spec'), "
            "(2, 'hand typed', 'web', NULL, NULL, 'task')")
        conn.commit()
    finally:
        conn.close()
    command.stamp(db_migrate._config(), "0037")
    return tmp_db


def test_legacy_spec_step_matches_without_migration(at_0037, flask_client):
    command.upgrade(db_migrate._config(), "head")

    resp = _step(flask_client, 7, kind="spec")

    assert resp.status_code == 200, resp.get_json()
    assert resp.get_json()["created"] is False
    assert resp.get_json()["task"]["id"] == 1
    with SessionLocal() as session:
        keys = {t.id: t.stage_key for t in session.exec(select(AgentTask))}
    assert keys == {1: "spec", 2: None}
    assert _rows(None) == [
        ("spec", "Spec", "spec", 0, 1, "stage-global-spec"),
        ("build", "Build", "build", 1, 1, "stage-global-build")]


def test_unknown_stage_is_rejected(flask_client, project):
    feature = _feature(project)

    for payload in ({"stage": "nope"}, {"kind": "fix"}, {}):
        resp = _step(flask_client, feature.id, **payload)
        assert resp.status_code == 400, payload
        assert resp.get_json()["field"] == "stage"
    assert _step(flask_client, 99999, stage="build").status_code == 404


def test_another_projects_stage_is_rejected(flask_client, project):
    _add_design(flask_client, project)
    unfiled = _feature(None, "no project")

    resp = _step(flask_client, unfiled.id, stage="design")

    assert resp.status_code == 400
    assert resp.get_json()["field"] == "stage"


def test_builtin_stage_cannot_be_deleted(flask_client, project):
    resp = flask_client.delete(f"/api/projects/{project}/stages/build")

    assert 400 <= resp.status_code < 500
    assert _rows(project) == []
    keys = [s["key"] for s in flask_client.get(
        f"/api/projects/{project}/stages").get_json()["stages"]]
    assert "build" in keys
    patch = flask_client.patch(f"/api/projects/{project}/stages/build",
                               json={"role": "spec"})
    assert patch.status_code == 400 and patch.get_json()["field"] == "role"


def test_stage_with_tasks_is_archived(flask_client, project):
    _add_design(flask_client, project)
    feature = _feature(project)
    task = _step(flask_client, feature.id, stage="design").get_json()["task"]

    resp = flask_client.delete(f"/api/projects/{project}/stages/design")

    assert resp.status_code == 200, resp.get_json()
    assert resp.get_json()["archived"] is True
    stages = {s["key"]: s for s in flask_client.get(
        f"/api/projects/{project}/stages").get_json()["stages"]}
    assert stages["design"]["archived_at"] is not None
    assert _step(flask_client, feature.id, stage="design").status_code == 400
    with SessionLocal() as session:
        assert session.get(AgentTask, task["id"]).stage_key == "design"


def test_stage_without_tasks_is_deleted(flask_client, project):
    _add_design(flask_client, project, prompt="p")

    resp = flask_client.delete(f"/api/projects/{project}/stages/design")

    assert resp.get_json()["deleted"] is True
    assert "design" not in [r[0] for r in _rows(project)]
    with SessionLocal() as session:
        assert session.exec(select(PromptTemplate).where(
            PromptTemplate.slug == f"stage-{project}-design")).first() is None


def test_reorder_sets_positions(flask_client, project):
    _add_design(flask_client, project)

    resp = flask_client.put(f"/api/projects/{project}/stages/order",
                            json={"keys": ["spec", "design", "build"]})

    assert resp.status_code == 200, resp.get_json()
    assert [s["key"] for s in resp.get_json()["stages"]] == [
        "spec", "design", "build"]
    bad = flask_client.put(f"/api/projects/{project}/stages/order",
                           json={"keys": ["spec", "build"]})
    assert bad.status_code == 400 and bad.get_json()["field"] == "keys"


def test_a_refused_write_copies_nothing(flask_client, project):
    base = f"/api/projects/{project}/stages"

    assert flask_client.patch(f"{base}/nope",
                              json={"label": "x"}).status_code == 404
    assert flask_client.put(f"{base}/order",
                            json={"keys": ["spec"]}).status_code == 400
    assert flask_client.post(base, json={"key": "spec", "label": "x",
                                         "role": "spec"}).status_code == 409

    assert _rows(project) == []
    with SessionLocal() as session:
        assert session.exec(select(PromptTemplate).where(
            PromptTemplate.slug == f"stage-{project}-spec")).first() is None


def test_a_concurrent_first_write_is_retried(flask_client, project,
                                             monkeypatch):
    """The other writer copies the defaults between this one's read and its
    copy, so this copy collides on the unique key and the write retries."""
    from lib.features import stages

    real = stages._own_rows
    raced = []

    def _late_read(session, project_id):
        rows = real(session, project_id)
        if project_id == project and not rows and not raced:
            raced.append(True)
            with SessionLocal() as other:
                stages._copy_defaults(other, project, stages._now())
                other.commit()
        return rows

    monkeypatch.setattr(stages, "_own_rows", _late_read)

    stage = _add_design(flask_client, project)

    assert raced and stage["key"] == "design"
    assert [r[0] for r in _rows(project)] == ["spec", "build", "design"]


def test_stage_keys_are_validated(flask_client, project):
    for payload, field in (
            ({"key": "Bad Key", "label": "x", "role": "spec"}, "key"),
            ({"key": "ok", "label": "", "role": "spec"}, "label"),
            ({"key": "ok", "label": "x", "role": "fix"}, "role"),
            ({"key": "spec", "label": "x", "role": "spec"}, "key")):
        resp = flask_client.post(f"/api/projects/{project}/stages",
                                 json=payload)
        assert resp.status_code in (400, 409), payload
        assert resp.get_json()["field"] == field, payload


def test_viewer_cannot_edit_stages(flask_client, project):
    base = f"/api/projects/{project}/stages"

    assert flask_client.get(base, headers=VIEWER).status_code == 200
    assert flask_client.patch(f"{base}/spec", json={"label": "x"},
                              headers=VIEWER).status_code == 403
    assert flask_client.post(base, json={"key": "d", "label": "D",
                                         "role": "spec"},
                             headers=VIEWER).status_code == 403
    assert flask_client.delete(f"{base}/spec",
                               headers=VIEWER).status_code == 403
    assert flask_client.put(f"{base}/order", json={"keys": []},
                            headers=VIEWER).status_code == 403
    assert _rows(project) == []
    assert flask_client.patch(f"{base}/spec", json={"label": "x"},
                              headers=EDITOR).status_code == 200


def test_custom_spec_role_stage_writes_same_events(flask_client, project,
                                                   launcher):
    _add_design(flask_client, project)
    with_spec = _feature(project, "uses spec")
    with_design = _feature(project, "uses design")
    assert events.stage(with_spec.id) == events.stage(with_design.id) == \
        events.STAGE_SPEC
    spec = _step(flask_client, with_spec.id, stage="spec").get_json()["task"]
    design = _step(flask_client, with_design.id,
                   stage="design").get_json()["task"]

    _run(flask_client, launcher, spec["id"])
    _run(flask_client, launcher, design["id"])

    def _shape(feature_id):
        return [(e.kind, (e.declared_by or "").split(":")[0])
                for e in events.history(feature_id)]

    assert _shape(with_design.id) == _shape(with_spec.id)
    assert events.stage(with_design.id) == events.stage(with_spec.id)
