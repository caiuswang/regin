"""Shared fixtures for the topic-graph tests."""

from __future__ import annotations

import subprocess

import pytest


@pytest.fixture
def branch_owned_ref(fake_git_repo) -> str:
    """A repo-relative path carried by the tip of an unmerged branch and absent
    from the working tree — the CAI-25/CAI-30 case that must read as a live
    anchor for work that simply isn't checked out, never as a dead ref.

    Returns the path; the repo is `fake_git_repo`.
    """
    path = "elsewhere.py"
    here = subprocess.run(
        ["git", "-C", str(fake_git_repo), "rev-parse", "--abbrev-ref", "HEAD"],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    subprocess.check_call(
        ["git", "-C", str(fake_git_repo), "checkout", "-q", "-b", "feat/elsewhere"])
    (fake_git_repo / path).write_text("x\n")
    subprocess.check_call(["git", "-C", str(fake_git_repo), "add", "-f", path])
    subprocess.check_call(
        ["git", "-C", str(fake_git_repo), "commit", "-q", "-m", f"add {path}"])
    subprocess.check_call(["git", "-C", str(fake_git_repo), "checkout", "-q", here])
    assert not (fake_git_repo / path).exists()
    return path
