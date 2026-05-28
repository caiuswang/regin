"""Unit tests for lib.sync.git_ops.

Uses the `fake_git_repo` fixture from tests/conftest.py — a real
git-initialised workspace with one committed file.

Skips when `git` is not on PATH (the fixture itself handles that).
"""

from __future__ import annotations

import os
import pytest

from lib.sync.git_ops import (
    GitError, get_branches, get_changed_files, get_commit_count_between,
    get_current_commit, get_file_content, is_git_repo, list_all_files,
)


# ── is_git_repo ──────────────────────────────────────────────

def test_is_git_repo_true_for_initialised_repo(fake_git_repo):
    assert is_git_repo(str(fake_git_repo)) is True


def test_is_git_repo_false_for_plain_dir(tmp_path):
    assert is_git_repo(str(tmp_path)) is False


def test_is_git_repo_false_for_missing_dir(tmp_path):
    assert is_git_repo(str(tmp_path / "missing")) is False


# ── get_branches ─────────────────────────────────────────────

def test_get_branches_lists_main(fake_git_repo):
    branches = get_branches(str(fake_git_repo))
    assert "main" in branches


def test_get_branches_plain_dir_raises(tmp_path):
    with pytest.raises(GitError):
        get_branches(str(tmp_path))


# ── get_current_commit ───────────────────────────────────────

def test_get_current_commit_returns_sha(fake_git_repo):
    sha = get_current_commit(str(fake_git_repo), "main")
    assert len(sha) == 40
    assert all(c in "0123456789abcdef" for c in sha)


def test_get_current_commit_missing_branch_raises(fake_git_repo):
    with pytest.raises(GitError):
        get_current_commit(str(fake_git_repo), "does-not-exist")


# ── list_all_files ───────────────────────────────────────────

def test_list_all_files_returns_committed_files(fake_git_repo):
    files = list_all_files(str(fake_git_repo), "main")
    assert "README.md" in files


def test_list_all_files_untracked_excluded(fake_git_repo):
    (fake_git_repo / "untracked.txt").write_text("not committed")
    files = list_all_files(str(fake_git_repo), "main")
    assert "untracked.txt" not in files


# ── get_file_content ─────────────────────────────────────────

def test_get_file_content_reads_committed_file(fake_git_repo):
    content = get_file_content(str(fake_git_repo), "main", "README.md")
    assert content == "baseline"


def test_get_file_content_missing_file_raises(fake_git_repo):
    with pytest.raises(GitError):
        get_file_content(str(fake_git_repo), "main", "no-such-file.txt")


# ── get_changed_files / get_commit_count_between ─────────────

def test_changed_files_between_two_commits(fake_git_repo):
    """Create a second commit so we have something to diff against."""
    from subprocess import check_call, DEVNULL
    first = get_current_commit(str(fake_git_repo), "main")
    (fake_git_repo / "new.txt").write_text("second commit body\n")
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
    check_call(["git", "-C", str(fake_git_repo), "add", "."], env=env,
               stdout=DEVNULL, stderr=DEVNULL)
    check_call(["git", "-C", str(fake_git_repo), "commit", "-q", "-m", "second"],
               env=env, stdout=DEVNULL, stderr=DEVNULL)
    second = get_current_commit(str(fake_git_repo), "main")

    changed = get_changed_files(str(fake_git_repo), first, second)
    assert "new.txt" in changed
    assert "README.md" not in changed  # README unchanged between the two

    assert get_commit_count_between(str(fake_git_repo), first, second) == 1
