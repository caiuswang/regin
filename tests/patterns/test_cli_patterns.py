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


def test_cmd_pattern_import_dir_only_parses_selection(monkeypatch, capsys):
    """--only 'a, b ,,c' → the ['a','b','c'] subset (trimmed, blanks dropped)."""
    from lib.patterns import pattern_importer
    captured = {}

    def fake_batch(root_dir, *, on_conflict="skip", dry_run=False,
                   only=None, progress=None):
        captured["only"] = only
        return []

    monkeypatch.setattr(
        pattern_importer, "batch_import_skill_directory", fake_batch)

    patterns_cmd.cmd_pattern_import_dir(
        "/tmp/skills", on_conflict="skip", only="foo, bar ,,baz",
        dry_run=False,
    )
    assert captured["only"] == ["foo", "bar", "baz"]


def test_cmd_pattern_import_dir_no_only_imports_all(monkeypatch, capsys):
    """No --only ⇒ only=None ⇒ historical import-everything."""
    from lib.patterns import pattern_importer
    captured = {"only": "sentinel"}

    def fake_batch(root_dir, *, on_conflict="skip", dry_run=False,
                   only=None, progress=None):
        captured["only"] = only
        return []

    monkeypatch.setattr(
        pattern_importer, "batch_import_skill_directory", fake_batch)

    patterns_cmd.cmd_pattern_import_dir(
        "/tmp/skills", on_conflict="skip", only=None, dry_run=False)
    assert captured["only"] is None


def _entry(status, name, slug=None, file_count=None, error=None, grit_rules=()):
    from types import SimpleNamespace
    return SimpleNamespace(status=status, name=name, slug=slug, file_count=file_count,
                           error=error, grit_rules=list(grit_rules))


_IMPORT_DIR_ENTRIES = [
    (_entry("imported", "a", slug="a-slug", file_count=2, grit_rules=["r1", "r2"]),
     "  + a-slug  (2 file(s))  [+2 grit rule(s)]"),
    (_entry("overwritten", "b", file_count=1, error="ignored"), "  ↻ b  (1 file(s))"),
    (_entry("renamed", "c", slug="c-2", grit_rules=["r"]), "  ~ c-2  [+1 grit rule(s)]"),
    (_entry("skipped", "d", file_count=3, error="exists"), "  · d  — exists"),
    (_entry("failed", "e", error="boom", grit_rules=["r"]), "  ! e  — boom"),
    (_entry("planned", "f"), "  · f"),
    (_entry("weird", "g"), "  ? g"),
]


@pytest.mark.parametrize("entries,summary,exit_code", [
    (_IMPORT_DIR_ENTRIES,
     "summary: imported=1, overwritten=1, renamed=1, skipped=1, failed=1, planned=1, weird=1",
     1),
    (_IMPORT_DIR_ENTRIES[:1], "summary: imported=1", None),
    ([], "summary: (no changes)", None),
])
def test_cmd_pattern_import_dir_progress_and_summary(
        monkeypatch, capsys, entries, summary, exit_code):
    from lib.patterns import pattern_importer
    seen = {}

    def fake_batch(root_dir, *, on_conflict="skip", dry_run=False,
                   only=None, progress=None):
        seen.update(root_dir=root_dir, on_conflict=on_conflict, dry_run=dry_run)
        for entry, _ in entries:
            progress(entry)
        return [entry for entry, _ in entries]

    monkeypatch.setattr(
        pattern_importer, "batch_import_skill_directory", fake_batch)
    monkeypatch.setenv("HOME", "/home/u")
    try:
        patterns_cmd.cmd_pattern_import_dir(
            "~/skills", on_conflict=" Overwrite ", only=None, dry_run=True)
        code = None
    except click.exceptions.Exit as exc:
        code = exc.exit_code
    out = capsys.readouterr().out.splitlines()
    assert (code, out) == (exit_code, [line for _, line in entries] + ["", summary])
    assert seen == {"root_dir": "/home/u/skills", "on_conflict": "overwrite", "dry_run": True}


def test_cmd_pattern_import_dir_importer_error_exits_1(monkeypatch, capsys):
    from lib.patterns import pattern_importer

    def fake_batch(root_dir, **_kw):
        raise pattern_importer.ImportError_("no such dir")

    monkeypatch.setattr(
        pattern_importer, "batch_import_skill_directory", fake_batch)
    with pytest.raises(click.exceptions.Exit) as exc:
        patterns_cmd.cmd_pattern_import_dir(
            "/nope", on_conflict=None, only=None, dry_run=False)
    assert (exc.value.exit_code, capsys.readouterr().err) == (1, "no such dir\n")
