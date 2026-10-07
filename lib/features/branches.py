"""Which branches a feature lives on — read from git on every request.

The binding is explicit, GitHub-style, never parsed out of a branch name: the
integration branch is `features.branch` when an operator set one, else the
`f{id}/integration` convention when that ref exists; the run branches are the
`params_json.worktree.branch` each run of the feature's tasks was armed with.
Everything about the branches themselves — whether they exist, which worktree
holds them, whether they are merged — is asked of git at read time and cached
nowhere, so the answer cannot drift from the repo. regin only reads here; it
never creates, deletes or merges a branch.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Optional

from sqlmodel import select

from lib.agent_sdk import worktree
from lib.features import store as feature_store
from lib.orm import SessionLocal
from lib.orm.models.features import Feature
from lib.orm.models.sync import Repo
from lib.orm.models.tasks import AgentTask, AgentTaskRun
from lib.tasks import feature_slices

_GIT_TIMEOUT = 30


class _GitUnreadable(RuntimeError):
    """The feature's repo cannot be read — shown as `error`, never a 500."""


def _git(root: Path, *args: str) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(["git", *args], cwd=str(root),
                              capture_output=True, text=True,
                              timeout=_GIT_TIMEOUT, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise _GitUnreadable(f"git {args[0]} failed: {exc}") from exc


def _branch_exists(root: Path, branch: str) -> bool:
    return bool(branch) and _git(
        root, "show-ref", "--verify", "--quiet",
        f"refs/heads/{branch}").returncode == 0


def _merged(root: Path, branch: str, into: str) -> Optional[bool]:
    """Whether `branch` is an ancestor of `into`; None when either is absent,
    because "not merged" and "nothing to compare against" are different
    answers."""
    if not (_branch_exists(root, branch) and _branch_exists(root, into)):
        return None
    return _git(root, "merge-base", "--is-ancestor",
                f"refs/heads/{branch}", f"refs/heads/{into}").returncode == 0


def _worktree_paths(root: Path) -> dict[str, str]:
    """branch → the worktree path git has it checked out in."""
    proc = _git(root, "worktree", "list", "--porcelain")
    if proc.returncode:
        raise _GitUnreadable(proc.stderr.strip() or "git worktree list failed")
    found: dict[str, str] = {}
    current: Optional[str] = None
    for line in proc.stdout.splitlines():
        if line.startswith("worktree "):
            current = line[len("worktree "):]
        elif line.startswith("branch refs/heads/") and current:
            found[line[len("branch refs/heads/"):]] = current
    return found


def _repo(feature: Feature) -> Path:
    try:
        return worktree.repo_root(feature_slices._repo_root(feature))
    except (worktree.WorktreeRefused, OSError,
            subprocess.TimeoutExpired) as exc:
        raise _GitUnreadable(str(exc)) from exc


def _default_branch(feature: Feature, root: Path) -> str:
    if feature.project_id is not None:
        with SessionLocal() as session:
            repo = session.get(Repo, feature.project_id)
        if repo is not None and repo.default_branch:
            return repo.default_branch
    try:
        return worktree.default_base(root)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise _GitUnreadable(str(exc)) from exc


def _integration(feature: Feature, root: Path) -> dict:
    if feature.branch:
        name, source = feature.branch, "set"
    elif _branch_exists(root, feature_slices.default_tail_base(feature.id)):
        name, source = feature_slices.default_tail_base(feature.id), "convention"
    else:
        return _no_integration()
    return {"name": name, "source": source,
            "exists": _branch_exists(root, name),
            "merged_into_default": _merged(
                root, name, _default_branch(feature, root))}


def _no_integration(name: str = "", source: Optional[str] = None) -> dict:
    return {"name": name, "source": source, "exists": False,
            "merged_into_default": None}


def _last_run_per_branch(feature_id: int) -> list[tuple[str, str, AgentTaskRun]]:
    """(branch, base, last run) for each distinct worktree branch any run of
    the feature's tasks was armed with, most recent first."""
    with SessionLocal() as session:
        runs = session.exec(
            select(AgentTaskRun)
            .join(AgentTask, AgentTask.id == AgentTaskRun.task_id)
            .where(AgentTask.feature_id == feature_id,
                   AgentTaskRun.params_json.is_not(None))
            .order_by(AgentTaskRun.id.desc())).all()
    seen: dict[str, tuple[str, str, AgentTaskRun]] = {}
    for run in runs:
        branch, base = _worktree_spec(run.params_json)
        if branch and branch not in seen:
            seen[branch] = (branch, base, run)
    return list(seen.values())


def _worktree_spec(params_json: str) -> tuple[str, str]:
    """(branch, base) from a run's params; ('', '') for any shape the armers
    never write, so one hand-edited row cannot take the whole read down."""
    try:
        spec = json.loads(params_json).get("worktree")
    except (ValueError, AttributeError):
        return "", ""
    if not isinstance(spec, dict):
        return "", ""
    branch, base = spec.get("branch"), spec.get("base") or ""
    if not isinstance(branch, str) or not isinstance(base, str):
        return "", ""
    return branch.strip(), base.strip()


def _runs(feature_id: int, root: Path) -> list[dict]:
    paths = _worktree_paths(root)
    return [{"branch": branch, "base": base, "task_id": run.task_id,
             "last_run_id": run.id, "worktree_path": paths.get(branch),
             "exists": _branch_exists(root, branch),
             "merged_into_base": _merged(root, branch, base)}
            for branch, base, run in _last_run_per_branch(feature_id)]


def for_feature(feature_id: int) -> dict:
    """The `FeatureBranches` view; raises `FeatureError` for an unknown id.

    A repo that cannot be read still answers: the integration name an
    operator set is a fact about the row, not about git, so it survives with
    `exists=false` beside the `error`.
    """
    feature = feature_store.require(feature_id)
    try:
        root = _repo(feature)
        return {"integration": _integration(feature, root),
                "runs": _runs(feature_id, root)}
    except _GitUnreadable as exc:
        name, source = (feature.branch, "set") if feature.branch else ("", None)
        return {"integration": _no_integration(name, source), "runs": [],
                "error": str(exc)}
