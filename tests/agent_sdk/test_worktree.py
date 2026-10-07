"""Worktree creation for a launched session (`lib/agent_sdk/worktree.py`).

These run against real `git` on a throwaway repo: the module is almost entirely
a reading of git's own answers — which branches exist, which paths git already
counts as worktrees — and a stub of that would pin the reading rather than the
behaviour.

`_sync_project_config` is stubbed by default. It shells out to a bash+rsync
script whose job is copying config that a temp repo does not have, so leaving
it live would buy nothing but two subprocesses per test; one test below covers
it directly.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from lib.agent_sdk import worktree

# Captured before the autouse stub replaces it, for the one test that wants the
# real thing back.
_REAL_SYNC = worktree._sync_project_config


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=str(repo), check=True,
                   capture_output=True, text=True)


@pytest.fixture
def repo(tmp_path):
    """A repo with one commit on `main` and nothing else."""
    root = tmp_path / "proj"
    root.mkdir()
    subprocess.run(["git", "-c", "init.defaultBranch=main", "init", str(root)],
                   check=True, capture_output=True, text=True)
    _git(root, "config", "user.email", "t@example.com")
    _git(root, "config", "user.name", "T")
    (root / "README.md").write_text("hi\n")
    _git(root, "add", "README.md")
    _git(root, "commit", "-m", "init")
    return root


@pytest.fixture(autouse=True)
def no_claude_sync(monkeypatch):
    monkeypatch.setattr(worktree, "_sync_project_config", lambda path: None)


def test_creates_the_tree_under_dot_claude_on_a_new_branch(repo):
    made = worktree.ensure_worktree(repo, "feat/new-ui")

    assert made["created"] is True
    assert made["branch"] == "feat/new-ui"
    assert made["repo_root"] == str(repo)
    assert Path(made["path"]) == repo / ".claude" / "worktrees" / "feat-new-ui"
    assert (Path(made["path"]) / "README.md").is_file()
    listed = subprocess.run(["git", "worktree", "list"], cwd=str(repo),
                            capture_output=True, text=True, check=True).stdout
    assert made["path"] in listed


def test_a_second_ask_for_the_same_branch_reuses_the_tree(repo):
    first = worktree.ensure_worktree(repo, "feat/new-ui")
    second = worktree.ensure_worktree(repo, "feat/new-ui")

    assert second["created"] is False
    assert second["path"] == first["path"]


def test_a_slug_held_by_a_different_branch_is_refused(repo):
    """`feat/new-ui` and `feat-new-ui` slugify the same. Running the second in
    the first's tree would put the operator on a branch they did not name."""
    worktree.ensure_worktree(repo, "feat/new-ui")

    with pytest.raises(worktree.WorktreeRefused, match="already the worktree"):
        worktree.ensure_worktree(repo, "feat-new-ui")


def test_an_existing_branch_is_checked_out_not_recreated(repo):
    _git(repo, "branch", "already-there")

    made = worktree.ensure_worktree(repo, "already-there")

    assert made["created"] is True
    head = subprocess.run(["git", "rev-parse", "--abbrev-ref", "HEAD"],
                          cwd=made["path"], capture_output=True, text=True,
                          check=True).stdout.strip()
    assert head == "already-there"


def test_the_base_ref_is_what_the_new_branch_forks_from(repo):
    _git(repo, "checkout", "-q", "-b", "other")
    (repo / "only-on-other.txt").write_text("x\n")
    _git(repo, "add", "only-on-other.txt")
    _git(repo, "commit", "-m", "other")
    _git(repo, "checkout", "-q", "main")

    made = worktree.ensure_worktree(repo, "forked", base="other")

    assert (Path(made["path"]) / "only-on-other.txt").is_file()


def test_an_unknown_base_ref_is_refused_and_leaves_no_directory(repo):
    with pytest.raises(worktree.WorktreeRefused, match="unknown base ref"):
        worktree.ensure_worktree(repo, "feat/x", base="no-such-branch")

    assert not (repo / ".claude" / "worktrees" / "feat-x").exists()


@pytest.mark.parametrize("branch", ["", "   ", "-x", "bad..name", "has space"])
def test_a_name_git_would_reject_is_refused_before_anything_is_created(repo,
                                                                      branch):
    with pytest.raises(worktree.WorktreeRefused):
        worktree.ensure_worktree(repo, branch)

    assert not (repo / ".claude" / "worktrees").exists()


def test_a_branch_that_slugifies_to_nothing_is_refused(repo):
    """`check-ref-format` accepts a name of pure punctuation; a directory
    named from it would be the empty string."""
    with pytest.raises(worktree.WorktreeRefused, match="no usable slug"):
        worktree.ensure_worktree(repo, "+++")


def test_launching_from_inside_a_worktree_branches_off_the_main_checkout(repo):
    """A worktree's `.git` is a file pointing at the main checkout, so the
    second tree must land beside the first — not nested inside it."""
    inner = worktree.ensure_worktree(repo, "first")

    second = worktree.ensure_worktree(inner["path"], "second")

    assert second["repo_root"] == str(repo)
    assert Path(second["path"]) == repo / ".claude" / "worktrees" / "second"


def test_a_directory_that_is_not_a_checkout_is_refused(tmp_path):
    plain = tmp_path / "plain"
    plain.mkdir()

    with pytest.raises(worktree.WorktreeRefused):
        worktree.ensure_worktree(plain, "feat/x")


def test_a_missing_directory_is_refused_by_name(tmp_path):
    with pytest.raises(worktree.WorktreeRefused, match="no such directory"):
        worktree.ensure_worktree(tmp_path / "gone", "feat/x")


def test_an_occupied_path_that_is_not_a_worktree_is_not_clobbered(repo):
    squatter = repo / ".claude" / "worktrees" / "feat-x"
    squatter.mkdir(parents=True)
    (squatter / "keep.txt").write_text("mine\n")

    with pytest.raises(worktree.WorktreeRefused, match="not a worktree"):
        worktree.ensure_worktree(repo, "feat/x")

    assert (squatter / "keep.txt").read_text() == "mine\n"


def test_options_report_the_repos_branches_and_default_base(repo):
    _git(repo, "branch", "side")

    opts = worktree.options(repo)

    assert opts["repo_root"] == str(repo)
    assert opts["worktrees_root"] == str(repo / ".claude" / "worktrees")
    assert sorted(opts["branches"]) == ["main", "side"]
    assert opts["default_base"] == "main"


def test_claude_config_is_synced_at_creation_not_left_to_a_later_hook(repo,
                                                                     monkeypatch):
    """The harness enumerates skills before a SessionStart hook could finish,
    so the copy has to happen while the tree is being made."""
    synced = []
    monkeypatch.setattr(worktree, "_sync_project_config", synced.append)

    made = worktree.ensure_worktree(repo, "feat/x")

    assert synced == [Path(made["path"])]


def test_a_repo_with_no_claude_config_still_creates_the_tree(repo, monkeypatch):
    """`_sync_project_config` is best-effort: the real script finds nothing to
    copy in this repo, and that must not fail the worktree."""
    monkeypatch.setattr(worktree, "_sync_project_config", _REAL_SYNC)

    assert worktree.ensure_worktree(repo, "feat/x")["created"] is True


def test_the_launch_route_creates_a_real_tree_and_runs_the_agent_in_it(
        flask_client, repo, monkeypatch):
    """The one end-to-end pass: everything else about the route is stubbed in
    `test_launch_route.py`, which cannot show that the cwd it hands the SDK is
    a directory git actually made."""
    from lib.agent_sdk import supervisor
    from lib.settings import settings

    monkeypatch.setattr(settings.agent_sdk, "enabled", True)
    seen = {}
    monkeypatch.setattr(supervisor, "launch",
                        lambda prompt, **kw: seen.update(kw) or "sdk-abc")

    body = flask_client.post("/api/agent-runs", json={
        "prompt": "hi", "cwd": str(repo),
        "worktree": {"branch": "feat/web-ui", "base": "main"},
    }).get_json()

    assert body["launched"] is True
    assert body["worktree"]["created"] is True
    expected = repo / ".claude" / "worktrees" / "feat-web-ui"
    assert seen["cwd"] == str(expected)
    assert (expected / "README.md").is_file()
