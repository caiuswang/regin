"""The chain tail: three fixed slices, every command read from the project's
command table, and written by the first pass rather than by transcription."""

from __future__ import annotations

import pathlib
import re

from lib.orm import SessionLocal
from lib.tasks.commands import ProjectCommands
from lib.tasks.slice_table import parse_slice_table
from lib.tasks.slicing import render_slice_table
from lib.tasks.tail import TAIL_SPECS, tail_slice_rows
from lib.tasks.transcribe import transcribe

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]

SKILL = REPO_ROOT / "regin-plugin/plugins/regin-agents/skills/to-slices/SKILL.md"
TRANSCRIBE_SRC = REPO_ROOT / "lib/tasks/transcribe.py"

HEADER = ("| # | Slice | Delivers | Spec refs | Tests | Depends on | Base |\n"
          "|---|---|---|---|---|---|---|\n")


def table(*rows: str) -> str:
    return "## Work slices\n\n" + HEADER + "".join(row + "\n" for row in rows)


TWO_LEAVES = table(
    "| S0 | contracts | the two contracts | R1 | test_a | — | `master` |",
    "| S1 | runner | the verify runner | R4 | test_b | S0 | f76/integration |",
    "| S2 | notice | the chain notice | R5 | test_c | S0 | f76/integration |",
)

# Deliberately nothing a detector would produce for this repo: if a command
# in the tail came from anywhere but the table, these would not show up.
ODD = ProjectCommands(
    test="just spec",
    migrate="./bin/schema up",
    e2e="deno task browser",
)


def _rows(body: str = TWO_LEAVES):
    return parse_slice_table(body)


def _assert_only_table_commands(rendered: str) -> None:
    """Each of the table's three values reached the tail, and none of the
    spellings a detector would have guessed for this repo did."""
    for command in (ODD.test, ODD.migrate, ODD.e2e):
        assert command in rendered, f"{command!r} never reached the tail"
    for stale in ("pytest", "alembic", "playwright", "npm test"):
        assert stale not in rendered, f"{stale!r} is hardcoded in the tail"


def test_tail_slices_read_every_command_from_the_project_table():
    tail = tail_slice_rows(_rows(), ODD, "f76/integration")

    assert [row.n for row in tail] == ["S3", "S4", "S5"]
    assert [row.title for row in tail] == [s.title for s in TAIL_SPECS]

    rendered = render_slice_table(tail, "f76/integration")
    _assert_only_table_commands(rendered)

    # Every command the tail runs is one of the table's values — no literal
    # from this project leaked into the template.
    verify = {row.n: row.verify_cmd for row in tail}
    assert verify["S4"] == ODD.migrate
    assert verify["S5"] == f"{ODD.test} && {ODD.e2e}"
    assert verify["S3"] is None          # integrate verifies with the default


def test_the_tail_chains_off_every_leaf_of_the_graph():
    tail = tail_slice_rows(_rows(), ODD, "f76/integration")

    assert tail[0].depends_on == ["S1", "S2"]
    assert tail[1].depends_on == ["S3"]
    assert tail[2].depends_on == ["S4"]
    assert {row.base for row in tail} == {"f76/integration"}


def test_a_null_command_renders_its_reason_instead_of_a_guess():
    commands = ProjectCommands(test="just spec", migrate=None, e2e=None,
                               reasons={"migrate": "本项目没有数据库",
                                        "e2e": "探测不出"})
    tail = tail_slice_rows(_rows(), commands, "f76/integration")

    migrate, verify = tail[1], tail[2]
    assert migrate.verify_cmd is None
    assert "本项目没有数据库" in migrate.delivers
    assert verify.verify_cmd == "just spec"
    assert "探测不出" in verify.delivers


def test_the_tail_renumbers_after_the_highest_existing_slice():
    body = table(
        "| S7 | late | a late slice | R1 | test_a | — | f76/integration |")
    assert [row.n for row in tail_slice_rows(_rows(body), ODD, "b")] == [
        "S8", "S9", "S10"]


def test_tail_is_written_by_the_first_skill_not_hardcoded_in_transcription(
        tmp_db, feature_row):
    """The tail is the first pass's output, so transcription must have no
    knowledge of it: a table without tail rows transcribes to exactly the
    rows it holds."""
    skill = SKILL.read_text()
    assert ".regin/commands.json" in skill
    assert "task tail --feature" in skill
    for spec in TAIL_SPECS:
        assert spec.title in skill, f"the skill never names {spec.title!r}"

    _assert_transcription_knows_no_tail()

    feature = feature_row("f76")
    transcribe(feature.id, TWO_LEAVES, branch_prefix="f76/")
    assert _slice_titles(feature.id) == ["S0 — contracts", "S1 — runner",
                                         "S2 — notice"]

    # And when the first pass HAS appended the tail, transcription lands it
    # like any other row — because it is one.
    tail = tail_slice_rows(_rows(), ODD, "f76/integration")
    with_tail = TWO_LEAVES + "".join(
        render_slice_table([row], "f76/integration").splitlines()[-1] + "\n"
        for row in tail)
    transcribe(feature.id, with_tail, branch_prefix="f76/")
    assert len(_slice_titles(feature.id)) == 6


def _assert_transcription_knows_no_tail() -> None:
    """Transcription neither imports the tail nor spells any of its rows.

    Matched on whole words, because `detail` contains the letters of `tail`
    and a substring scan would fail on a variable name.
    """
    source = TRANSCRIBE_SRC.read_text().lower()
    assert "lib.tasks.tail" not in source
    assert "commands.json" not in source
    words = set(re.findall(r"[a-z_]+", source))
    assert "tail" not in words
    # `verify` and `migrate` are words transcription legitimately uses (the
    # Verify column, the command table's key), so the literal checked here is
    # the one only a hardcoded tail would produce: a row's title.
    assert "migrate + doctor" not in source


def _slice_titles(feature_id: int) -> list[str]:
    from sqlmodel import select

    from lib.orm.models.tasks import AgentTask

    with SessionLocal() as session:
        return [t.title for t in session.exec(
            select(AgentTask).where(AgentTask.feature_id == feature_id)
            .order_by(AgentTask.id)).all()]


def test_the_cli_renders_the_tail_from_the_projects_own_table(
        tmp_db, feature_row, tmp_path):
    """End to end through `regin task tail`: the rows come out spelled with
    the commands the project's own table holds."""
    import json

    from typer.testing import CliRunner

    from cli.commands.tasks import tasks_app

    (tmp_path / ".regin").mkdir()
    (tmp_path / ".regin" / "commands.json").write_text(
        json.dumps(ODD.to_json_obj()))
    from lib.features import store as feature_store

    feature = feature_row("f76")
    feature_store.set_body(feature.id, TWO_LEAVES)

    result = CliRunner().invoke(
        tasks_app, ["tail", "--feature", str(feature.id),
                    "--repo", str(tmp_path), "--base", "f76/integration"])
    assert result.exit_code == 0, result.output
    _assert_only_table_commands(result.output)
    assert "| S3 |" in result.output and "| S5 |" in result.output
