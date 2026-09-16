"""`lib/tasks/store.py` — the sole writer of `agent_tasks`."""

from __future__ import annotations

import json

import pytest

from lib.tasks import runs, store


def test_capture_always_lands_as_an_idea(tmp_db):
    task, created = store.capture_task(title="idea", source="cli")
    assert created is True
    assert task["status"] == "idea"


def test_replay_touches_updated_at_and_returns_the_same_id(tmp_db):
    first, _ = store.capture_task(title="idea", source="tg", external_id="1")
    second, created = store.capture_task(title="different title", source="tg",
                                         external_id="1")
    assert created is False
    assert second["id"] == first["id"]
    # A replay resolves to the stored row; it does not overwrite it.
    assert second["title"] == "idea"
    assert len(store.list_tasks()) == 1


def test_same_external_id_from_a_different_source_is_a_new_task(tmp_db):
    store.capture_task(title="a", source="tg", external_id="1")
    _, created = store.capture_task(title="b", source="linear", external_id="1")
    assert created is True
    assert len(store.list_tasks()) == 2


def test_list_is_empty_on_a_fresh_db(tmp_db):
    assert store.list_tasks() == []


def test_list_filters_by_status_and_orders_newest_first(tmp_db):
    first, _ = store.capture_task(title="one", source="cli")
    second, _ = store.capture_task(title="two", source="cli")

    assert [t["id"] for t in store.list_tasks()] == [second["id"], first["id"]]
    assert len(store.list_tasks(status="idea")) == 2
    assert store.list_tasks(status="archived") == []


def test_tags_and_links_round_trip(tmp_db):
    task, _ = store.capture_task(
        title="idea", source="cli", tags=["a", "b"],
        links=[{"label": "spec", "href": "https://example.test"}])
    stored = store.get_task(task["id"])
    assert stored["tags"] == ["a", "b"]
    assert stored["links"] == [{"label": "spec", "href": "https://example.test"}]


def test_get_unknown_task_is_none(tmp_db):
    assert store.get_task(999) is None


def test_a_stranded_running_task_is_archivable(tmp_db, monkeypatch):
    task, _ = store.capture_task(title="stranded", source="cli")
    store.set_status(task["id"], "running")
    monkeypatch.setattr(runs, "live_run", lambda task_id: None)

    assert store.update_task(task["id"],
                             {"status": "archived"})["status"] == "archived"


def test_a_task_with_a_live_run_is_not_archivable(tmp_db, monkeypatch):
    task, _ = store.capture_task(title="going", source="cli")
    store.set_status(task["id"], "running")
    monkeypatch.setattr(runs, "live_run", lambda task_id: {"id": 1})

    with pytest.raises(store.TransitionRejected) as caught:
        store.update_task(task["id"], {"status": "archived"})
    assert "live run" in str(caught.value)
    assert store.get_task(task["id"])["status"] == "running"


def test_bulk_archive_skips_only_the_live_row(tmp_db, monkeypatch):
    stranded, _ = store.capture_task(title="stranded", source="cli")
    going, _ = store.capture_task(title="going", source="cli")
    for task in (stranded, going):
        store.set_status(task["id"], "running")
    monkeypatch.setattr(runs, "live_run",
                        lambda task_id: {"id": 1} if task_id == going["id"]
                        else None)

    result = store.bulk_update([stranded["id"], going["id"]],
                               {"status": "archived"})

    assert result["changed"] == [stranded["id"]]
    assert [r["id"] for r in result["rejected"]] == [going["id"]]


# ── the CLI can file a task too ──────────────────────────────

def test_cli_add_resolves_a_project_by_repo_name(tmp_db):
    from typer.testing import CliRunner

    from cli.commands.tasks import tasks_app
    from lib.orm import SessionLocal
    from lib.orm.models.sync import Repo

    with SessionLocal() as session:
        session.add(Repo(name="my-repo", path="/tmp/my-repo", is_active=1,
                         default_branch="main"))
        session.commit()

    result = CliRunner().invoke(tasks_app, [
        "add", "from the cli", "--project", "my-repo", "--priority", "high",
        "--json"])

    assert result.exit_code == 0, result.output
    task = json.loads(result.stdout)
    assert task["priority"] == "high"
    assert task["project_id"] is not None


def test_cli_add_names_the_registered_repos_when_the_project_is_unknown(tmp_db):
    from typer.testing import CliRunner

    from cli.commands.tasks import tasks_app
    from lib.orm import SessionLocal
    from lib.orm.models.sync import Repo

    with SessionLocal() as session:
        session.add(Repo(name="only-one", path="/tmp/only-one", is_active=1,
                         default_branch="main"))
        session.commit()

    result = CliRunner().invoke(tasks_app, ["add", "x", "--project", "typo"])

    # An operator types a name, not an id, so the refusal has to answer with
    # names — failing with a number they never typed is not an answer.
    assert result.exit_code == 1
    assert "only-one" in result.output
