"""Tests for `regin pattern ...` commands."""

from __future__ import annotations

import pytest
import click

from cli.commands import patterns as patterns_cmd


def test_cmd_pattern_import_prints_result(monkeypatch, capsys):
    class Result:
        slug = "topic-router"
        pattern_dir = "/tmp/patterns/topic-router"
        file_count = 2
        grit_rules: list[str] = []
        grit_languages: list[str] = []
        enabled_languages: list[str] = []

    from lib.patterns import pattern_importer
    monkeypatch.setattr(pattern_importer, "import_zip", lambda bundle, force=False, target_slug=None: Result())

    patterns_cmd.cmd_pattern_import("/tmp/topic-router.zip")

    out = capsys.readouterr().out
    assert "imported pattern topic-router" in out
    assert "regin skills push --id topic-router" in out
    # No grit rules on this bundle → no grit summary line.
    assert "grit rule(s)" not in out


def test_cmd_pattern_import_prints_grit_summary(monkeypatch, capsys):
    class Result:
        slug = "api-bean-contract"
        pattern_dir = "/tmp/patterns/api-bean-contract"
        file_count = 4
        grit_rules = ["api_bean_missing_base", "api_bean_uses_lombok"]
        grit_languages = ["java"]
        enabled_languages = ["java"]

    from lib.patterns import pattern_importer
    monkeypatch.setattr(pattern_importer, "import_zip", lambda bundle, force=False, target_slug=None: Result())

    patterns_cmd.cmd_pattern_import("/tmp/api-bean-contract.zip")

    out = capsys.readouterr().out
    assert "merged 2 grit rule(s) (java)" in out
    assert "enabled grit language(s): java" in out
    assert "rules activate after: regin skills push --id api-bean-contract" in out


def test_cmd_pattern_import_conflict_exits(monkeypatch, capsys):
    from lib.patterns import pattern_importer

    def fail(*args, **kwargs):
        raise pattern_importer.ImportConflictError("exists")

    monkeypatch.setattr(pattern_importer, "import_zip", fail)

    with pytest.raises(click.exceptions.Exit) as exc:
        patterns_cmd.cmd_pattern_import("/tmp/topic-router.zip")

    assert exc.value.exit_code == 2
    assert "import failed: exists" in capsys.readouterr().err
