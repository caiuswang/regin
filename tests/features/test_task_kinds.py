"""A task declares what kind of step it is, and a spec step is not a round.

One test per acceptance scenario of feature #102.
"""

from __future__ import annotations

import shlex
from types import SimpleNamespace

import pytest
from sqlmodel import select
from typer.testing import CliRunner

from cli.commands.defects import defects_app
from cli.commands.tasks import tasks_app
from lib.defects import store as defect_store
from lib.features import events, steps, store as feature_store
from lib.orm import SessionLocal
from lib.orm.models.tasks import AgentTask
from lib.tasks import runs, store as task_store
from lib.tasks.run_prompt import SPEC_FILE_PLACEHOLDER

runner = CliRunner()


@pytest.fixture
def launcher(monkeypatch):
    state = SimpleNamespace(calls=[])

    def _fake(prompt, **kwargs):
        state.calls.append(prompt)
        return SimpleNamespace(trace_id=f"sdk-fake{len(state.calls)}",
                               add_done_callback=lambda callback: None)

    monkeypatch.setattr(runs.supervisor, "launch_run", _fake)
    return state


def _post_step(client, feature_id, kind, body=None):
    return client.post(f"/api/features/{feature_id}/steps",
                       json={"kind": kind, "body": body})


def _run(client, launcher, task_id) -> str:
    assert client.post(f"/api/tasks/{task_id}/arm", json={}).status_code == 200
    resp = client.post(f"/api/tasks/{task_id}/run")
    assert resp.status_code == 201, resp.get_json()
    return launcher.calls[-1]


def _kinds(feature_id):
    return [e.kind for e in events.history(feature_id)]


def test_a_spec_run_does_not_start_the_build(flask_client, launcher):
    feature = feature_store.create("spec me")
    spec = _post_step(flask_client, feature.id, "spec").get_json()["task"]

    _run(flask_client, launcher, spec["id"])

    assert _kinds(feature.id) == [events.SPECCED]
    assert events.rounds(feature.id) == 1


def test_the_build_after_a_spec_starts_the_lifecycle(flask_client, launcher):
    feature = feature_store.create("spec then build")
    spec = _post_step(flask_client, feature.id, "spec").get_json()["task"]
    _run(flask_client, launcher, spec["id"])
    build = _post_step(flask_client, feature.id, "build").get_json()["task"]

    _run(flask_client, launcher, build["id"])

    last = events.last(feature.id)
    assert last.kind == events.BUILD_STARTED
    assert last.declared_by.startswith("run:")


def test_the_verb_is_idempotent(flask_client):
    feature = feature_store.create("once only")

    first = _post_step(flask_client, feature.id, "spec", "focus on X")
    second = _post_step(flask_client, feature.id, "spec")

    assert (first.status_code, second.status_code) == (201, 200)
    assert first.get_json()["task"]["id"] == second.get_json()["task"]["id"]
    with SessionLocal() as session:
        rows = session.exec(select(AgentTask).where(
            AgentTask.external_id == f"feature:{feature.id}:spec")).all()
    assert len(rows) == 1
    assert rows[0].title == f"Spec · [F{feature.id} once only](feature:{feature.id})"
    assert rows[0].kind == "spec"
    assert rows[0].body == "focus on X"
    assert first.get_json()["task"]["feature_title"] == "once only"


def test_the_verb_refuses_other_kinds_and_unknown_features(flask_client):
    feature = feature_store.create("strict")

    assert _post_step(flask_client, feature.id, "fix").status_code == 400
    assert _post_step(flask_client, 99999, "build").status_code == 404


def test_rerunning_rather_than_recreating(flask_client, launcher):
    feature = feature_store.create("shipped once")
    build = _post_step(flask_client, feature.id, "build").get_json()["task"]
    _run(flask_client, launcher, build["id"])
    task_store.update_task(build["id"], {"status": "done"})
    feature_store.ship(feature.id)

    again = _post_step(flask_client, feature.id, "build")
    assert again.status_code == 200
    assert again.get_json()["task"]["id"] == build["id"]
    _run(flask_client, launcher, build["id"])

    assert events.last(feature.id).kind == events.RESTARTED


def test_the_board_carries_the_feature_title(flask_client):
    feature = feature_store.create("the named feature")
    resp = flask_client.post("/api/tasks", json={
        "title": "Enrich the feature", "feature_id": feature.id})
    task_id = resp.get_json()["task"]["id"]

    listed = flask_client.get(
        f"/api/tasks?feature_id={feature.id}&root_only=").get_json()["items"]
    detail = flask_client.get(f"/api/tasks/{task_id}").get_json()["task"]

    assert [t["feature_title"] for t in listed] == ["the named feature"]
    assert detail["feature_title"] == "the named feature"
    assert detail["kind"] == "task"


def test_the_spec_prompt_block_is_kind_scoped(flask_client, launcher):
    feature = feature_store.create("prompted")
    spec = _post_step(flask_client, feature.id, "spec").get_json()["task"]
    build = _post_step(flask_client, feature.id, "build").get_json()["task"]

    spec_prompt = _run(flask_client, launcher, spec["id"])
    build_prompt = _run(flask_client, launcher, build["id"])

    assert "<spec>" in spec_prompt
    assert (f"feature spec {feature.id} --file "
            f"{shlex.quote(SPEC_FILE_PLACEHOLDER)}") in spec_prompt
    assert "<spec>" not in build_prompt


def test_a_title_never_decides_the_kind(flask_client):
    resp = flask_client.post("/api/tasks", json={"title": "Write the spec"})

    assert resp.get_json()["task"]["kind"] == "task"


def test_fix_tasks_carry_their_kind():
    feature = feature_store.create("buggy")
    row = defect_store.record("it breaks", feature_id=feature.id,
                              session_id="t-1")

    result = runner.invoke(defects_app, ["fix-item", str(row.id), "--create"])

    assert result.exit_code == 0, result.output
    with SessionLocal() as session:
        task = session.get(AgentTask, defect_store.get(row.id).fix_task_id)
    assert task.kind == "fix"


def test_kind_is_declared_on_the_cli_and_editable(flask_client):
    result = runner.invoke(tasks_app, ["add", "a step", "--kind", "build",
                                       "--json"])
    assert result.exit_code == 0, result.output
    task_id = int(result.output.split('"id": ')[1].split(",")[0])
    assert task_store.get_task(task_id)["kind"] == "build"

    resp = flask_client.patch(f"/api/tasks/{task_id}", json={"kind": "spec"})
    assert resp.get_json()["task"]["kind"] == "spec"
    bad = flask_client.patch(f"/api/tasks/{task_id}", json={"kind": "nope"})
    assert bad.status_code == 400


def test_steps_key_is_stable():
    assert steps.external_id(7, "build") == "feature:7:build"


@pytest.mark.parametrize("kind", ["task", "spec", "build"])
def test_the_dashboard_create_declares_a_stage(flask_client, kind):
    feature = feature_store.create("staged")
    resp = flask_client.post("/api/tasks", json={
        "title": "a step", "feature_id": feature.id, "kind": kind})

    assert resp.status_code == 201, resp.get_json()
    assert resp.get_json()["task"]["kind"] == kind


@pytest.mark.parametrize("kind", ["fix", "nope", None])
def test_the_dashboard_create_refuses_fix_and_unknown_stages(flask_client,
                                                              kind):
    resp = flask_client.post("/api/tasks", json={"title": "a step",
                                                 "kind": kind})

    assert resp.status_code == 400
    assert resp.get_json()["field"] == "kind"


def test_machine_capture_cannot_declare_a_stage():
    from lib.tasks import capture
    with pytest.raises(capture.CaptureRejected) as exc:
        capture.parse_capture({"title": "t", "source": "cli", "kind": "spec"})
    assert exc.value.field == "kind"


def test_a_step_title_cites_its_feature_and_reads_the_rename(flask_client):
    """The title stamps a reference, not the name: a rename reads through
    where titles are drawn as words, and a bodyless step's run is handed the
    feature's current title in its references block."""
    from lib.tasks.run_prompt import _prompt  # noqa: PLC0415

    feature = feature_store.create("old name")
    task_id = _post_step(flask_client, feature.id,
                         "build").get_json()["task"]["id"]

    feature_store.rename(feature.id, "new name")

    assert feature_store.task_titles([task_id]) == {
        task_id: f"Build · F{feature.id} new name"}
    with SessionLocal() as session:
        task = session.get(AgentTask, task_id).model_dump()
    assert f"- F{feature.id} 「new name」 — feature" in _prompt(task, run_id=1)


def test_an_announce_card_names_a_step_in_words(flask_client, monkeypatch):
    """A card title is plain text, so the step's stamped reference is read as
    words there rather than cut mid-link at 80 characters."""
    from lib.agent_messages import events as message_events  # noqa: PLC0415
    from lib.tasks import run_announce  # noqa: PLC0415

    sent = []
    monkeypatch.setattr(message_events, "emit",
                        lambda kind, **kw: sent.append(kw["title"]))
    feature = feature_store.create("x" * 60)
    task = _post_step(flask_client, feature.id, "spec").get_json()["task"]
    feature_store.rename(feature.id, "renamed")

    run_announce._announce_stuck(task, "no slot")

    assert sent == [f"Task ready: Spec · F{feature.id} renamed"]
