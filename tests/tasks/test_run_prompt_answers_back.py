"""A run is told how to answer what it was asked, not only how to settle.

The gap this closes: starting a task only ever moved a status. The prompt
carried the unresolved comments and the feature id, but named no act for
either — so a run read a question it had no way to answer, and finished
knowing the feature's spec was wrong with no instruction to say so. Both are
now instructions with a real command behind them, and both live in an editable
prompt surface rather than an f-string.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

from cli.commands.tasks import tasks_app
from lib.comments import store as comments
from lib.orm.models.comments import RESOLUTION_OPEN, RESOLUTION_RESOLVED
from lib.tasks import runs


@pytest.fixture
def launcher(monkeypatch):
    state = SimpleNamespace(calls=[])

    def _fake(prompt, **kwargs):
        state.calls.append({"prompt": prompt, **kwargs})
        return SimpleNamespace(trace_id=f"sdk-fake{len(state.calls)}",
                               add_done_callback=lambda callback: None)

    monkeypatch.setattr(runs.supervisor, "launch_run", _fake)
    return state


def _armed(client, title="a task", **body):
    resp = client.post("/api/tasks", json={"title": title, **body})
    assert resp.status_code == 201, resp.get_json()
    task_id = resp.get_json()["task"]["id"]
    assert client.post(f"/api/tasks/{task_id}/arm", json={}).status_code == 200
    return task_id


def _launch(client, launcher, task_id) -> str:
    resp = client.post(f"/api/tasks/{task_id}/run")
    assert resp.status_code == 201, resp.get_json()
    return launcher.calls[-1]["prompt"]


# 1 ── the comment thread is addressable

def test_the_followup_block_names_the_thread_the_reply_command_takes(
        flask_client, launcher):
    """The reply command takes a thread id, so the block that delivers the
    comment has to be the thing that names it — an agent cannot guess a row id."""
    task_id = _armed(flask_client, "shelf", body="wire the thing")
    thread = comments.open_thread("task", str(task_id))
    comments.add_comment(thread["id"], body="also make it idempotent")

    prompt = _launch(flask_client, launcher, task_id)

    assert f"[thread {thread['id']} ·" in prompt
    assert "task comment" in prompt
    assert "--resolve" in prompt


def test_the_thread_id_is_on_every_line_not_just_the_first(flask_client,
                                                           launcher):
    """The oldest comments are dropped to fit, so an id carried only by a
    thread's first line could be dropped out from under its own continuation."""
    task_id = _armed(flask_client, "shelf", body="wire the thing")
    thread = comments.open_thread("task", str(task_id))
    comments.add_comment(thread["id"], body="first thing")
    comments.add_comment(thread["id"], body="second thing")

    prompt = _launch(flask_client, launcher, task_id)

    assert prompt.count(f"[thread {thread['id']} ·") == 2


# 2 ── the reply command exists and does both halves

def test_task_comment_posts_as_the_agent_and_leaves_the_thread_open(tmp_db):
    thread = comments.open_thread("task", "1")
    result = CliRunner().invoke(
        tasks_app, ["comment", str(thread["id"]), "--body", "done that"])

    assert result.exit_code == 0, result.output
    posted = comments.comments_for(thread["id"])
    assert [row["body"] for row in posted] == ["done that"]
    assert posted[0]["author_kind"] == "agent"
    assert (comments.threads_for("task", "1")[0]["resolution_state"]
            == RESOLUTION_OPEN)


def test_task_comment_resolve_settles_the_thread_in_the_same_act(tmp_db):
    thread = comments.open_thread("task", "1")
    result = CliRunner().invoke(
        tasks_app,
        ["comment", str(thread["id"]), "--body", "fixed", "--resolve"])

    assert result.exit_code == 0, result.output
    assert (comments.threads_for("task", "1")[0]["resolution_state"]
            == RESOLUTION_RESOLVED)


def test_task_comment_reopen_undoes_a_resolution(tmp_db):
    thread = comments.open_thread("task", "1")
    comments.resolve(thread["id"], RESOLUTION_RESOLVED)
    result = CliRunner().invoke(
        tasks_app,
        ["comment", str(thread["id"]), "--body", "not so fast", "--reopen"])

    assert result.exit_code == 0, result.output
    assert (comments.threads_for("task", "1")[0]["resolution_state"]
            == RESOLUTION_OPEN)


def test_task_comment_refuses_both_switches_at_once(tmp_db):
    thread = comments.open_thread("task", "1")
    result = CliRunner().invoke(
        tasks_app, ["comment", str(thread["id"]), "--body", "x",
                    "--resolve", "--reopen"])

    assert result.exit_code == 2
    assert comments.comments_for(thread["id"]) == []


def test_task_comment_on_a_missing_thread_is_an_error_not_a_silent_no_op(
        tmp_db):
    result = CliRunner().invoke(
        tasks_app, ["comment", "9999", "--body", "hello"])

    assert result.exit_code == 1


def test_a_resolved_reply_stops_the_thread_reaching_the_next_run(
        flask_client, launcher):
    """The round trip the instruction promises: reply, resolve, and the thread
    is gone from the next prompt."""
    task_id = _armed(flask_client, "shelf", body="wire the thing")
    thread = comments.open_thread("task", str(task_id))
    comments.add_comment(thread["id"], body="also make it idempotent")
    assert "also make it idempotent" in _launch(flask_client, launcher, task_id)

    CliRunner().invoke(tasks_app, ["comment", str(thread["id"]),
                                   "--body", "did it", "--resolve"])

    runs.complete_run(_latest_run(task_id), status="done", task_status="done")
    assert flask_client.post(f"/api/tasks/{task_id}/arm",
                             json={}).status_code == 200
    assert "<followup>" not in _launch(flask_client, launcher, task_id)


def _latest_run(task_id: int) -> int:
    return runs.latest_attempt(task_id)["id"]


# 3 ── the spec is something a run can rewrite

def test_a_run_under_a_feature_is_told_how_to_rewrite_its_spec(flask_client,
                                                               launcher):
    from lib.features import store as feature_store

    feature = feature_store.create("the feature")
    task_id = _armed(flask_client, "shelf", body="wire the thing")
    assert flask_client.patch(f"/api/tasks/{task_id}",
                              json={"feature_id": feature.id}).status_code == 200

    prompt = _launch(flask_client, launcher, task_id)

    assert "feature spec" in prompt
    assert f"'{feature.id}'" in prompt or f" {feature.id} " in prompt
    assert "do not append a changelog" in prompt


def test_a_task_under_no_feature_is_told_nothing_about_a_spec(flask_client,
                                                              launcher):
    """The block is absent, not empty — a bodyless instruction is noise the
    agent has to resolve."""
    task_id = _armed(flask_client, "shelf", body="wire the thing")

    prompt = _launch(flask_client, launcher, task_id)

    assert "<feature>" not in prompt
    assert "feature spec" not in prompt


# 4 ── the operator can edit the prompt, but not out of its own control loop

def test_an_envelope_edited_to_drop_the_finish_block_still_carries_it(
        tmp_db, monkeypatch):
    """Every block is the operator's to remove — except the one that lets the
    task complete at all. A *missing* placeholder is not a render error, so
    `render_surface` never falls back; the block is re-appended instead."""
    from lib.prompt_templates import seed_builtin_skeletons, update_template
    from lib.prompts.surfaces.tasks import LAUNCH_SURFACE_ID

    seed_builtin_skeletons()
    update_template(LAUNCH_SURFACE_ID, {"body": "{{body}}{{feature}}"})
    monkeypatch.setattr(runs, "_feature_block", lambda task: "")
    task = {"id": 1, "title": "t", "body": "the idea", "feature_id": None}

    prompt = runs._prompt(task, 7, None)

    assert prompt.startswith("the idea")
    assert "task finish 7" in prompt
    assert prompt.count("<finish>") == 1


def test_task_comment_refuses_a_thread_that_is_not_on_a_task(tmp_db):
    """The id comes off the prompt, but a mistyped one must not let an agent
    post as `agent` into somebody else's conversation."""
    thread = comments.open_thread("feature", "1")
    result = CliRunner().invoke(
        tasks_app, ["comment", str(thread["id"]), "--body", "wrong place"])

    assert result.exit_code == 2
    assert comments.comments_for(thread["id"]) == []


def test_the_dropped_comments_note_is_an_editable_surface(flask_client,
                                                          launcher, tmp_db):
    from lib.prompt_templates import seed_builtin_skeletons, update_template
    from lib.prompts.surfaces.tasks import FOLLOWUP_OMITTED_SURFACE_ID

    seed_builtin_skeletons()
    update_template(FOLLOWUP_OMITTED_SURFACE_ID,
                    {"body": "LOST {{dropped}}"})
    task_id = _armed(flask_client, "shelf", body="wire the thing")
    for index in range(6):
        thread = comments.open_thread("task", str(task_id))
        comments.add_comment(thread["id"], body=f"note-{index} " + "x" * 2000)

    assert "LOST " in _launch(flask_client, launcher, task_id)


def test_the_unnamed_base_branch_phrase_is_an_editable_surface(tmp_db):
    from lib.prompt_templates import seed_builtin_skeletons, update_template
    from lib.prompts.surfaces.tasks import FINISH_BASE_UNNAMED_SURFACE_ID

    seed_builtin_skeletons()
    update_template(FINISH_BASE_UNNAMED_SURFACE_ID, {"body": "wherever"})

    assert "merge this run's work back into wherever" in runs._finish_block(7)
