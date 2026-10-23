"""F172 — a task run's output language, and the launch-prompt preview."""

from __future__ import annotations

import json

import pytest
from sqlmodel import select

from lib.auth import create_token
from lib.defects import store as defect_store
from lib.features import store as feature_store
from lib.orm import SessionLocal
from lib.orm.models.prompts import PromptTemplate
from lib.orm.models.sync import Repo
from lib.orm.models.tasks import (AgentTask, AgentTaskRun, RUN_STATUS_ARMED,
                                  TASK_KIND_SPEC)
from lib.prompts.surfaces.tasks import LAUNCH_SURFACE_ID, _LAUNCH_BODY
from lib.settings import TasksConfig, settings
from lib.tasks import output_language, run_prompt, store

EDITOR = {"Authorization": f"Bearer {create_token(2, 'ed', 'editor')}"}
VIEWER = {"Authorization": f"Bearer {create_token(3, 'vw', 'viewer')}"}
OLD_LAUNCH_BODY = ("{{invocation}}{{body}}{{tracker}}{{followup}}{{feature}}"
                   "{{spec}}{{references}}{{finish}}")


@pytest.fixture
def project(tmp_db) -> int:
    with SessionLocal() as session:
        row = Repo(name="regin", path="/tmp/regin")
        session.add(row)
        session.commit()
        session.refresh(row)
        return row.id


@pytest.fixture
def default_language(monkeypatch):
    def _set(value: str):
        monkeypatch.setattr(settings, "tasks",
                            TasksConfig(output_language=value))
    return _set


def _set_project(project_id, value):
    with SessionLocal() as session:
        repo = session.get(Repo, project_id)
        repo.output_language = value
        session.add(repo)
        session.commit()


def _task(*, project_id=None, feature_id=None, body="do the thing",
          kind=None, status="idea") -> dict:
    with SessionLocal() as session:
        row = AgentTask(title="t", body=body, status=status, source="test",
                        feature_id=feature_id, project_id=project_id)
        if kind:
            row.kind = kind
        session.add(row)
        session.commit()
        session.refresh(row)
        task_id = row.id
    return store.get_task(task_id)


def _arm(task_id: int, params: dict) -> int:
    with SessionLocal() as session:
        row = AgentTaskRun(task_id=task_id, attempt=1, status=RUN_STATUS_ARMED,
                           params_json=json.dumps(params))
        session.add(row)
        session.commit()
        session.refresh(row)
        return row.id


def _block(language: str) -> str:
    return f"<language>\nWrite everything meant for a person in {language}:"


def test_unset_everywhere_adds_nothing(project, default_language):
    default_language("")
    task = _task(project_id=project)
    prompt = run_prompt._prompt(task, 7)

    assert "<language>" not in prompt
    assert prompt.endswith("</finish>")


def test_project_language_wins_over_default(project, default_language):
    default_language("English")
    _set_project(project, "简体中文")
    prompt = run_prompt._prompt(_task(project_id=project), 7)

    assert _block("简体中文") in prompt
    assert "in English" not in prompt


def test_unset_project_falls_back_for_every_task_shape(project,
                                                       default_language):
    default_language("简体中文")
    feature = feature_store.create("f")
    feature_store.set_project(feature.id, project)
    spec = _task(feature_id=feature.id, kind=TASK_KIND_SPEC)
    custom = _task(project_id=project)
    fix = _task(project_id=project)
    defect = defect_store.record("broken", feature_id=feature.id)
    defect_store.set_fix_item(defect.id, fix["id"])

    for task in (spec, custom, fix):
        assert _block("简体中文") in run_prompt._prompt(task, 7)
    assert output_language.resolve(project) == ("简体中文", "default")


def test_a_task_under_no_project_reads_the_default(tmp_db, default_language):
    default_language("Deutsch")
    assert _block("Deutsch") in run_prompt._prompt(_task(), 7)
    assert output_language.resolve(None) == ("Deutsch", "default")


def test_language_survives_an_overlong_body(project):
    _set_project(project, "简体中文")
    body = "x" * (run_prompt.PROMPT_MAX + 500)
    prompt = run_prompt._prompt(_task(project_id=project, body=body), 7)

    assert prompt.rstrip().endswith("</language>")
    assert "</finish>" in prompt
    assert prompt.index("</finish>") < prompt.index("<language>")


def test_an_envelope_without_language_drops_only_the_language(project):
    from lib.prompt_templates import seed_builtin_skeletons

    seed_builtin_skeletons()
    _set_project(project, "简体中文")
    with SessionLocal() as session:
        row = session.exec(select(PromptTemplate).where(
            PromptTemplate.slug == LAUNCH_SURFACE_ID)).one()
        row.body = "{{body}}"
        session.add(row)
        session.commit()
    prompt = run_prompt._prompt(_task(project_id=project), 7)

    assert "<language>" not in prompt
    assert "</finish>" in prompt


def test_an_unedited_old_envelope_is_healed(tmp_db):
    from lib.prompt_templates import seed_builtin_skeletons

    seed_builtin_skeletons()
    with SessionLocal() as session:
        row = session.exec(select(PromptTemplate).where(
            PromptTemplate.slug == LAUNCH_SURFACE_ID)).one()
        row.body = OLD_LAUNCH_BODY
        session.add(row)
        session.commit()
    seed_builtin_skeletons()
    with SessionLocal() as session:
        healed = session.exec(select(PromptTemplate).where(
            PromptTemplate.slug == LAUNCH_SURFACE_ID)).one().body

    assert healed == _LAUNCH_BODY and "{{language}}" in healed


def test_an_edited_envelope_is_left_alone(tmp_db):
    from lib.prompt_templates import seed_builtin_skeletons

    seed_builtin_skeletons()
    edited = OLD_LAUNCH_BODY + "\n\nmine"
    with SessionLocal() as session:
        row = session.exec(select(PromptTemplate).where(
            PromptTemplate.slug == LAUNCH_SURFACE_ID)).one()
        row.body = edited
        session.add(row)
        session.commit()
    seed_builtin_skeletons()
    with SessionLocal() as session:
        kept = session.exec(select(PromptTemplate).where(
            PromptTemplate.slug == LAUNCH_SURFACE_ID)).one().body

    assert kept == edited


def _counts(task_id):
    with SessionLocal() as session:
        runs = len(session.exec(select(AgentTaskRun)).all())
        status = session.get(AgentTask, task_id).status
    return runs, status


def test_armed_preview_equals_the_launch_prompt(flask_client, project,
                                                monkeypatch):
    from lib.agent_sdk import worktree

    def _no_tree(*args, **kwargs):
        raise AssertionError("preview must not build a worktree")

    monkeypatch.setattr(worktree, "ensure_worktree", _no_tree)
    _set_project(project, "简体中文")
    task = _task(project_id=project, status="armed")
    params = {"skill": {"name": "goal-verified"},
              "worktree": {"branch": "f1/s1", "base": "f1/integration"}}
    run_id = _arm(task["id"], params)
    before = _counts(task["id"])

    body = flask_client.get(
        f"/api/tasks/{task['id']}/prompt-preview").get_json()

    assert body["prompt"] == run_prompt._prompt(task, run_id, params)
    assert "`f1/integration`" in body["prompt"]
    assert body["run_id_placeholder"] is False
    assert body["language"] == "简体中文"
    assert body["language_source"] == "project"
    assert _counts(task["id"]) == before


def test_unarmed_preview_uses_the_placeholder(flask_client, tmp_db):
    task = _task()
    body = flask_client.get(
        f"/api/tasks/{task['id']}/prompt-preview").get_json()

    assert body["run_id_placeholder"] is True
    assert "--run <run id> --reason" in body["prompt"]
    assert body["language_source"] == "none"


def test_unknown_task_preview_is_404(flask_client, tmp_db):
    assert flask_client.get(
        "/api/tasks/999999/prompt-preview").status_code == 404


def test_editor_sets_and_clears_a_project_language(flask_client, project,
                                                   default_language):
    default_language("")
    url = f"/api/projects/{project}/output-language"
    resp = flask_client.patch(url, json={"output_language": " 简体中文 "},
                              headers=EDITOR)
    assert resp.status_code == 200
    assert output_language.project_language(project) == "简体中文"
    task = _task(project_id=project)
    preview = flask_client.get(
        f"/api/tasks/{task['id']}/prompt-preview").get_json()
    assert (preview["language"], preview["language_source"]) == (
        "简体中文", "project")

    flask_client.patch(url, json={"output_language": ""}, headers=EDITOR)
    with SessionLocal() as session:
        assert session.get(Repo, project).output_language is None
    assert flask_client.get(url).get_json()["source"] == "none"


def test_an_overlong_language_is_refused(flask_client, project):
    resp = flask_client.patch(f"/api/projects/{project}/output-language",
                              json={"output_language": "x" * 65},
                              headers=EDITOR)
    assert resp.status_code == 400
    assert resp.get_json()["field"] == "output_language"


def test_a_viewer_cannot_set_a_language(flask_client, project):
    resp = flask_client.patch(f"/api/projects/{project}/output-language",
                              json={"output_language": "English"},
                              headers=VIEWER)
    assert resp.status_code == 403


def test_unknown_project_language_is_404(flask_client, tmp_db):
    assert flask_client.patch("/api/projects/999/output-language",
                              json={"output_language": "x"},
                              headers=EDITOR).status_code == 404
    assert flask_client.get(
        "/api/projects/nope/output-language").status_code == 404


def test_the_default_setting_rejects_an_overlong_value():
    with pytest.raises(ValueError):
        TasksConfig(output_language="x" * 65)
    assert TasksConfig(output_language="  English ").output_language == \
        "English"
