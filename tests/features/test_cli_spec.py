"""`regin feature spec` — the spec written onto the row, not into a file.

Without a write path from a session, an agent that has just argued a design
out has nowhere to put it but `docs/`, and the file it writes is the copy
nothing re-reads. `--file` is the load-bearing half: a real spec is thousands
of characters with newlines, which argv is the wrong pipe for.
"""

from __future__ import annotations

from typer.testing import CliRunner

from cli.app import app
from lib.features import store
from lib.tasks.integrations.base import DESCRIPTION_MAX

runner = CliRunner()

SPEC = "## Intent\nthe row carries this, no file does\n"


def _spec_of(feature_id: int) -> str | None:
    return store.require(feature_id).body


def test_reads_the_stored_spec_back():
    feature = store.create("a specced feature", body=SPEC)
    result = runner.invoke(app, ["feature", "spec", str(feature.id)])
    assert result.exit_code == 0, result.output
    assert SPEC.strip() in result.output


def test_reading_an_empty_spec_says_so_rather_than_printing_nothing():
    feature = store.create("a feature nobody specced")
    result = runner.invoke(app, ["feature", "spec", str(feature.id)])
    assert result.exit_code == 0, result.output
    assert result.output.strip()


def test_body_replaces_the_spec():
    feature = store.create("a feature", body="the first draft")
    result = runner.invoke(app, ["feature", "spec", str(feature.id),
                                 "--spec", SPEC])
    assert result.exit_code == 0, result.output
    assert _spec_of(feature.id) == SPEC


def test_file_replaces_the_spec_from_disk(tmp_path):
    feature = store.create("a feature")
    path = tmp_path / "spec.md"
    path.write_text(SPEC, encoding="utf-8")
    result = runner.invoke(app, ["feature", "spec", str(feature.id),
                                 "--file", str(path)])
    assert result.exit_code == 0, result.output
    assert _spec_of(feature.id) == SPEC


def test_file_dash_reads_stdin():
    feature = store.create("a feature")
    result = runner.invoke(app, ["feature", "spec", str(feature.id),
                                 "--file", "-"], input=SPEC)
    assert result.exit_code == 0, result.output
    assert _spec_of(feature.id) == SPEC


def test_clear_empties_the_spec():
    feature = store.create("a feature", body=SPEC)
    result = runner.invoke(app, ["feature", "spec", str(feature.id),
                                 "--clear"])
    assert result.exit_code == 0, result.output
    assert not _spec_of(feature.id)


def test_two_write_sources_at_once_is_refused():
    feature = store.create("a feature", body=SPEC)
    result = runner.invoke(app, ["feature", "spec", str(feature.id),
                                 "--spec", "one", "--clear"])
    assert result.exit_code == 2
    assert _spec_of(feature.id) == SPEC


def test_an_over_long_spec_is_refused_and_leaves_the_row_alone():
    feature = store.create("a feature", body=SPEC)
    result = runner.invoke(app, ["feature", "spec", str(feature.id),
                                 "--spec", "x" * (DESCRIPTION_MAX + 1)])
    assert result.exit_code == 2
    assert str(DESCRIPTION_MAX) in result.output
    assert _spec_of(feature.id) == SPEC


def test_an_unknown_feature_is_refused():
    result = runner.invoke(app, ["feature", "spec", "9999", "--spec", "x"])
    assert result.exit_code == 2


def test_a_missing_file_is_refused_without_touching_the_row(tmp_path):
    feature = store.create("a feature", body=SPEC)
    result = runner.invoke(app, ["feature", "spec", str(feature.id),
                                 "--file", str(tmp_path / "nope.md")])
    assert result.exit_code == 2
    assert _spec_of(feature.id) == SPEC
