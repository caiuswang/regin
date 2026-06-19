"""Unit tests for cli.commands.topics."""

from __future__ import annotations

import click
import pytest
from typer.testing import CliRunner

from cli.app import app
from cli.commands import topics as topics_cmd
from lib.topics import TopicGraphError, ValidationResult


def test_cmd_topics_bootstrap_prints_paths(monkeypatch, capsys, tmp_path):
    monkeypatch.setattr(
        topics_cmd,
        "bootstrap",
        lambda repo, seeds=False, force=False: {"topic": tmp_path / "topic.json"},
    )

    topics_cmd.cmd_topics_bootstrap(repo=str(tmp_path), seeds=False, force=False)

    out = capsys.readouterr().out
    assert "Topic graph created" in out


def test_cmd_topics_scan_prints_counts(monkeypatch, capsys, tmp_path):
    monkeypatch.setattr(
        topics_cmd,
        "scan",
        lambda repo, staged=False: {
            "updated_topics": ["web"],
            "covered_ref_count": 3,
        },
    )

    topics_cmd.cmd_topics_scan(repo=str(tmp_path), staged=False)

    out = capsys.readouterr().out
    assert "Updated topics: 1" in out
    assert "Covered refs: 3" in out


def test_cmd_topics_check_exits_on_invalid(monkeypatch, capsys, tmp_path):
    monkeypatch.setattr(
        topics_cmd,
        "validate",
        lambda repo: ValidationResult(errors=["broken ref"], warnings=["duplicate ref"]),
    )

    with pytest.raises(click.exceptions.Exit) as exc:
        topics_cmd.cmd_topics_check(repo=str(tmp_path))

    assert exc.value.exit_code == 1
    out = capsys.readouterr().out
    assert "warning: duplicate ref" in out
    assert "error: broken ref" in out


def test_cmd_topics_install_hook_prints_path(monkeypatch, capsys, tmp_path):
    hooks_dir = tmp_path / ".git/hooks"
    monkeypatch.setattr(
        topics_cmd, "install_topic_hooks",
        lambda repo: {
            "pre-commit": hooks_dir / "pre-commit",
            "post-merge": hooks_dir / "post-merge",
            "post-checkout": hooks_dir / "post-checkout",
        },
    )

    topics_cmd.cmd_topics_install_hook(repo=str(tmp_path))

    out = capsys.readouterr().out
    assert "pre-commit" in out
    assert "post-merge" in out
    assert "post-checkout" in out


def test_cmd_topics_wiki_prints_written_files(monkeypatch, capsys, tmp_path):
    monkeypatch.setattr(
        topics_cmd,
        "generate_wiki",
        lambda repo: [tmp_path / ".regin/topics/wiki/index.md", tmp_path / ".regin/topics/wiki/web.md"],
    )

    topics_cmd.cmd_topics_wiki(repo=str(tmp_path))

    out = capsys.readouterr().out
    assert "Topic wiki files written: 2" in out
    assert "web.md" in out


def test_cmd_topics_wiki_exits_on_error(monkeypatch, tmp_path):
    def fail(repo):
        raise ValueError("invalid graph")

    monkeypatch.setattr(topics_cmd, "generate_wiki", fail)

    with pytest.raises(click.exceptions.Exit) as exc:
        topics_cmd.cmd_topics_wiki(repo=str(tmp_path))

    assert exc.value.exit_code == 1


def test_cmd_topics_route_prints_json(monkeypatch, capsys, tmp_path):
    monkeypatch.setattr(
        topics_cmd,
        "route_topic",
        lambda repo, query: {"status": "approved", "query": query, "topic": {"id": "web"}},
    )

    topics_cmd.cmd_topics_route("web", repo=str(tmp_path), wiki=False)

    out = capsys.readouterr().out
    assert '"status": "approved"' in out
    assert '"id": "web"' in out


def test_render_topic_wiki_is_content_first():
    """`--wiki` renders the wiki markdown content-first and omits the refs
    list — the JSON envelope buries wiki_pages below refs, so an agent that
    pipes through `head` never reaches it."""
    out = topics_cmd._render_topic_wiki({
        "query": "demo",
        "refs": [{"path": f"lib/m{i}.py"} for i in range(30)],
        "wiki_pages": [
            {"path": ".regin/topics/wiki/demo.md",
             "content": "# Demo\n\nbody", "truncated": False},
            {"path": ".regin/topics/wiki/index.md",
             "content": "## Index", "truncated": True},
        ],
    })
    assert out.startswith("<!-- .regin/topics/wiki/demo.md -->")
    assert "# Demo" in out and "## Index" in out
    assert "truncated" in out                 # marker for the truncated page
    assert "lib/m0.py" not in out             # refs are NOT in the wiki output


def test_render_topic_wiki_handles_no_pages():
    out = topics_cmd._render_topic_wiki({"query": "x", "wiki_pages": []})
    assert "no wiki pages" in out and "x" in out


def test_cmd_topics_bootstrap_exits_on_error(monkeypatch, tmp_path):
    def fail(*args, **kwargs):
        raise TopicGraphError("exists")

    monkeypatch.setattr(topics_cmd, "bootstrap", fail)

    with pytest.raises(click.exceptions.Exit) as exc:
        topics_cmd.cmd_topics_bootstrap(repo=str(tmp_path), seeds=False, force=False)

    assert exc.value.exit_code == 1


def test_topics_cli_manual_flow_uses_temp_git_repo(fake_git_repo):
    (fake_git_repo / "service").mkdir()
    (fake_git_repo / "service" / "api.py").write_text("import os\n")
    import subprocess
    subprocess.check_call(["git", "-C", str(fake_git_repo), "add", "."])
    subprocess.check_call(["git", "-C", str(fake_git_repo), "commit", "-q", "-m", "service"])

    runner = CliRunner()

    result = runner.invoke(app, ["topics", "bootstrap", "--repo", str(fake_git_repo), "--seeds"])
    assert result.exit_code == 0, result.output

    result = runner.invoke(app, ["topics", "check", "--repo", str(fake_git_repo)])
    assert result.exit_code == 0, result.output

    result = runner.invoke(app, ["topics", "scan", "--repo", str(fake_git_repo)])
    assert result.exit_code == 0, result.output

    result = runner.invoke(app, ["topics", "wiki", "--repo", str(fake_git_repo)])
    assert result.exit_code == 0, result.output
    index = fake_git_repo / ".regin/topics/wiki/index.md"
    assert index.exists()
    assert "overview" in index.read_text().lower()

    result = runner.invoke(app, ["topics", "route", "overview", "--repo", str(fake_git_repo)])
    assert result.exit_code == 0, result.output
    assert '"status": "approved"' in result.output
