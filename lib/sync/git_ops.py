"""Git subprocess wrapper for repository operations."""

import subprocess


class GitError(Exception):
    pass


def git(repo_path: str, *args) -> str:
    """Run a git command in the given repo directory."""
    result = subprocess.run(
        ['git', '-C', repo_path] + list(args),
        capture_output=True, text=True, timeout=30
    )
    if result.returncode != 0:
        raise GitError(f"git {' '.join(args)} failed: {result.stderr.strip()}")
    return result.stdout.strip()


def is_git_repo(path: str) -> bool:
    """Check if a directory is a git repository."""
    try:
        git(path, 'rev-parse', '--git-dir')
        return True
    except (GitError, FileNotFoundError):
        return False


def get_current_commit(repo_path: str, branch: str) -> str:
    """Get the current commit SHA for a branch."""
    return git(repo_path, 'rev-parse', branch)


def get_branches(repo_path: str) -> list:
    """List all local branches."""
    output = git(repo_path, 'branch', '--format=%(refname:short)')
    return [b for b in output.split('\n') if b] if output else []


def get_changed_files(repo_path: str, from_commit: str, to_commit: str) -> list:
    """Get files changed between two commits."""
    output = git(repo_path, 'diff', '--name-only', from_commit, to_commit)
    return [f for f in output.split('\n') if f] if output else []


def list_all_files(repo_path: str, branch: str) -> list:
    """List all files in a branch (for initial sync)."""
    output = git(repo_path, 'ls-tree', '-r', '--name-only', branch)
    return [f for f in output.split('\n') if f] if output else []


def get_file_content(repo_path: str, branch: str, filepath: str) -> str:
    """Read file content from a specific branch."""
    return git(repo_path, 'show', f'{branch}:{filepath}')


def get_commit_count_between(repo_path: str, from_commit: str, to_commit: str) -> int:
    """Count commits between two refs."""
    output = git(repo_path, 'rev-list', '--count', f'{from_commit}..{to_commit}')
    return int(output)
