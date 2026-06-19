"""SessionStart `environment.git_status` span.

The harness (claude-code `src/context.ts getGitStatus`) injects a git-status
block into the cached system context — it never flows through a tool call or
the transcript, so regin reconstructs it at SessionStart. These tests pin the
reconstruction, the env-gating that keeps it faithful, and the non-git skip.
"""

from __future__ import annotations

import subprocess

from hook_manager.core import HookPayload
from hook_manager.handlers import session_lifecycle


def _payload(cwd, session_id="s-git", **raw_extra):
    raw = {"cwd": cwd, "session_id": session_id, "source": "startup",
           "agent_type": "claude", **raw_extra}
    return HookPayload(event="SessionStart", cwd=cwd,
                       session_id=session_id, raw=raw)


def _capture_spans(monkeypatch):
    posted = []

    def _fake_post_span(*, trace_id, name, attributes=None, **_kw):
        posted.append({"trace_id": trace_id, "name": name,
                       "attributes": attributes or {}})
        return True

    from lib import hook_plugin
    monkeypatch.setattr(hook_plugin, "post_span", _fake_post_span)
    return posted


def _git(repo, *args):
    subprocess.run(["git", "-C", str(repo), *args], check=True,
                   capture_output=True, text=True)


def _make_repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "Tester")
    (repo / "tracked.txt").write_text("committed\n")
    _git(repo, "add", "tracked.txt")
    _git(repo, "commit", "-q", "-m", "seed commit")
    # The file the agent would only know about via the injected status block.
    (repo / "untracked-guide.md").write_text("# guide\n")
    return repo


def _only(posted, name):
    return [s for s in posted if s["name"] == name]


def test_emits_git_status_span_with_untracked_file(tmp_path, monkeypatch):
    repo = _make_repo(tmp_path)
    posted = _capture_spans(monkeypatch)

    session_lifecycle.handle_start(_payload(str(repo)))

    spans = _only(posted, "environment.git_status")
    assert len(spans) == 1
    attrs = spans[0]["attributes"]
    # The untracked file is the whole point: it must surface in the block,
    # exactly as the harness's `git status --short` would have shown it.
    assert "untracked-guide.md" in attrs["block"]
    assert "?? untracked-guide.md" in attrs["block"]
    assert "seed commit" in attrs["block"]
    assert attrs["changed_count"] == 1
    assert attrs["truncated"] is False
    assert attrs["captured_at"] == "session_start_hook"
    assert attrs["branch"]  # non-empty (master/main)


def test_clean_repo_reports_clean(tmp_path, monkeypatch):
    repo = _make_repo(tmp_path)
    (repo / "untracked-guide.md").unlink()  # remove the only dirty entry
    posted = _capture_spans(monkeypatch)

    session_lifecycle.handle_start(_payload(str(repo)))

    attrs = _only(posted, "environment.git_status")[0]["attributes"]
    assert attrs["changed_count"] == 0
    assert "(clean)" in attrs["block"]


def test_skipped_when_git_instructions_disabled(tmp_path, monkeypatch):
    repo = _make_repo(tmp_path)
    monkeypatch.setenv("CLAUDE_CODE_DISABLE_GIT_INSTRUCTIONS", "1")
    posted = _capture_spans(monkeypatch)

    session_lifecycle.handle_start(_payload(str(repo)))

    # The agent never saw the block, so we must not fabricate a span for it.
    assert _only(posted, "environment.git_status") == []
    # session.start still fires regardless.
    assert _only(posted, "session.start")


def test_skipped_in_remote_runs(tmp_path, monkeypatch):
    repo = _make_repo(tmp_path)
    monkeypatch.setenv("CLAUDE_CODE_REMOTE", "true")
    posted = _capture_spans(monkeypatch)

    session_lifecycle.handle_start(_payload(str(repo)))

    assert _only(posted, "environment.git_status") == []


def test_non_git_cwd_emits_no_git_status(tmp_path, monkeypatch):
    plain = tmp_path / "plain"
    plain.mkdir()
    posted = _capture_spans(monkeypatch)

    session_lifecycle.handle_start(_payload(str(plain)))

    assert _only(posted, "environment.git_status") == []
    assert _only(posted, "session.start")  # lifecycle marker unaffected
