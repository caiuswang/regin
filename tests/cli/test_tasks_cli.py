"""`regin task add/list/show` — the local capture path, no server involved."""

from __future__ import annotations

import json

from typer.testing import CliRunner

from cli.app import app

runner = CliRunner()


def test_task_group_is_wired_into_the_app():
    result = runner.invoke(app, ["task", "--help"])
    assert "No such command" not in result.output
    assert result.exit_code == 0


def test_add_list_show_round_trip(tmp_db):
    added = runner.invoke(app, ["task", "add", "write the spec",
                                "--body", "the long version",
                                "--tag", "docs", "--json"])
    assert added.exit_code == 0, added.output
    task = json.loads(added.stdout)
    assert task["status"] == "idea"
    assert task["source"] == "cli"
    assert task["tags"] == ["docs"]

    listed = runner.invoke(app, ["task", "list", "--json"])
    assert listed.exit_code == 0
    assert [t["id"] for t in json.loads(listed.stdout)] == [task["id"]]

    shown = runner.invoke(app, ["task", "show", str(task["id"]), "--json"])
    assert shown.exit_code == 0
    assert json.loads(shown.stdout)["title"] == "write the spec"


def test_list_on_an_empty_backlog(tmp_db):
    result = runner.invoke(app, ["task", "list"])
    assert result.exit_code == 0
    assert "no tasks" in result.output


def test_show_unknown_task_exits_nonzero(tmp_db):
    result = runner.invoke(app, ["task", "show", "999"])
    assert result.exit_code == 1
    assert "no task #999" in result.output


def test_add_renders_the_id_without_json(tmp_db):
    result = runner.invoke(app, ["task", "add", "an idea"])
    assert result.exit_code == 0
    assert "#1  an idea" in result.output
