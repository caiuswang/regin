"""R1 — the project command table is read, or refused by name."""

from __future__ import annotations

import io
import json

import pytest

from lib.tasks.commands import (
    COMMAND_KEYS,
    UNDETECTED_REASON,
    CommandsAbsent,
    CommandsRejected,
    commands_path,
    draft_project_commands,
    load_project_commands,
    print_draft,
)


def _project(tmp_path, commands: dict | None = None, **files):
    for name, body in files.items():
        target = tmp_path / name.replace("__", "/")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body)
    if commands is not None:
        regin = tmp_path / ".regin"
        regin.mkdir(exist_ok=True)
        (regin / "commands.json").write_text(json.dumps(commands))
    return str(tmp_path)


def test_commands_missing_key_is_refused(tmp_path):
    root = _project(tmp_path, commands={
        "test": "pytest -q", "e2e": "npx playwright test", "reasons": {}})
    with pytest.raises(CommandsRejected) as caught:
        load_project_commands(root)
    assert caught.value.key == "migrate"
    assert "migrate" in str(caught.value)


def test_commands_null_without_reason_is_refused(tmp_path):
    root = _project(tmp_path, commands={
        "test": "pytest -q",
        "migrate": None,
        "e2e": "npx playwright test",
        "reasons": {"e2e": "irrelevant"},
    })
    with pytest.raises(CommandsRejected) as caught:
        load_project_commands(root)
    assert caught.value.key == "migrate"
    assert "reason" in str(caught.value)


def test_commands_draft_is_not_written_to_disk(tmp_path):
    root = _project(tmp_path, **{"pyproject.toml": "[project]\nname = 'x'\n"})
    with pytest.raises(CommandsAbsent) as caught:
        load_project_commands(root)
    out = io.StringIO()
    print_draft(caught.value.draft, caught.value.path, out)
    printed = out.getvalue()
    assert "pytest" in printed
    assert not (tmp_path / ".regin" / "commands.json").exists()
    assert not (tmp_path / ".regin").exists()


def test_a_complete_table_loads(tmp_path):
    root = _project(tmp_path, commands={
        "test": "pytest -q",
        "migrate": "alembic upgrade head",
        "e2e": "npx playwright test",
        "reasons": {},
    })
    commands = load_project_commands(root)
    assert commands.test == "pytest -q"
    assert commands.migrate == "alembic upgrade head"
    assert commands.e2e == "npx playwright test"


def test_a_null_with_a_reason_loads(tmp_path):
    root = _project(tmp_path, commands={
        "test": "pytest -q",
        "migrate": None,
        "e2e": None,
        "reasons": {"migrate": "no schema", "e2e": "no browser surface"},
    })
    commands = load_project_commands(root)
    assert commands.migrate is None
    assert commands.reasons["migrate"] == "no schema"


def test_an_undetectable_key_is_null_with_a_reason(tmp_path):
    root = _project(tmp_path, **{"pyproject.toml": "[project]\nname = 'x'\n"})
    draft = draft_project_commands(root)
    assert draft.test and "pytest" in draft.test
    assert draft.migrate is None
    assert draft.reasons["migrate"] == UNDETECTED_REASON
    assert draft.e2e is None


def test_every_key_appears_in_the_draft(tmp_path):
    root = _project(tmp_path)
    obj = json.loads(json.dumps(draft_project_commands(root).to_json_obj()))
    assert all(key in obj for key in COMMAND_KEYS)


def test_malformed_json_is_refused(tmp_path):
    (tmp_path / ".regin").mkdir()
    (tmp_path / ".regin" / "commands.json").write_text("{not json")
    with pytest.raises(CommandsRejected):
        load_project_commands(str(tmp_path))


def test_commands_path_is_under_dot_regin(tmp_path):
    assert commands_path(str(tmp_path)).endswith("/.regin/commands.json")


def test_the_draft_path_has_its_own_exit_code(tmp_path):
    from typer.testing import CliRunner

    from cli.commands.tasks import tasks_app

    (tmp_path / "pyproject.toml").write_text("[project]\nname = 'x'\n")
    result = CliRunner().invoke(tasks_app, ["commands", "--repo", str(tmp_path)])
    assert result.exit_code == 3
    assert "pytest" in result.stdout
    assert not (tmp_path / ".regin").exists()


def test_a_refusal_exits_one(tmp_path):
    from typer.testing import CliRunner

    from cli.commands.tasks import tasks_app

    _project(tmp_path, commands={"test": "pytest -q", "e2e": None, "reasons": {}})
    result = CliRunner().invoke(tasks_app, ["commands", "--repo", str(tmp_path)])
    assert result.exit_code == 1


def test_a_loaded_table_exits_zero(tmp_path):
    from typer.testing import CliRunner

    from cli.commands.tasks import tasks_app

    _project(tmp_path, commands={
        "test": "pytest -q", "migrate": None, "e2e": None,
        "reasons": {"migrate": "no schema", "e2e": "no browser"}})
    result = CliRunner().invoke(tasks_app, ["commands", "--repo", str(tmp_path)])
    assert result.exit_code == 0
