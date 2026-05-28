"""Explicit repo registry.

Each entry in `settings.repo_paths` is the absolute path of a git
working tree. The /repos web UI and `regin add-repo` / `regin
remove-repo` CLI commands mutate this list and reconcile the
`repos` / `branches` tables.
"""

from __future__ import annotations

import os
import tempfile
from datetime import datetime, timezone

from sqlmodel import delete, select

from lib.settings import _get
from lib.sync.git_ops import get_branches, is_git_repo
from lib.orm import SessionLocal
from lib.orm.models import Branch, Repo, SessionRepo
from lib.activity_log import get_activity_logger as _get_activity_logger


def _sync_log():
    return _get_activity_logger("sync")


# Conventional default branch names, tried in order when a repo has multiple.
_DEFAULT_BRANCH_CANDIDATES = ("main", "master", "trunk")


# ── path helpers ─────────────────────────────────────────────

def _normalize(path: str) -> str:
    """Expand `~`, resolve symlinks, and absolutize.

    Repo paths are stored normalized so the add/remove lookup
    always matches what's in `settings.repo_paths`.
    """
    return os.path.realpath(os.path.expanduser(str(path)))


def detect_default_branch(repo_path: str) -> str:
    """Detect the best default branch for a repo."""
    branches = get_branches(repo_path)
    for candidate in _DEFAULT_BRANCH_CANDIDATES:
        if candidate in branches:
            return candidate
    return branches[0] if branches else "main"


# ── settings I/O ─────────────────────────────────────────────

def _load_repo_paths() -> list[str]:
    """Read `repo_paths` from settings, normalized."""
    raw = _get("repo_paths", [])
    if not isinstance(raw, list):
        raw = [raw]
    out: list[str] = []
    seen: set[str] = set()
    for entry in raw:
        if not isinstance(entry, str) or not entry.strip():
            continue
        norm = _normalize(entry)
        if norm in seen:
            continue
        seen.add(norm)
        out.append(norm)
    return out


def _save_repo_paths(paths: list[str]) -> None:
    """Persist `repo_paths` to the local settings file."""
    from lib.settings import save_settings
    save_settings({"repo_paths": list(paths)}, scope="local")


# ── public API ───────────────────────────────────────────────

def scan_repos() -> list:
    """Resolve `settings.repo_paths` into structured repo dicts.

    Each path must point at a git working tree; non-existent or
    non-git entries are silently dropped (the registration step
    would reject them anyway).

    Returns list of dicts: {name, path, default_branch}.
    """
    out: list[dict] = []
    seen: set[str] = set()
    for path in _load_repo_paths():
        if not os.path.isdir(path) or not is_git_repo(path):
            continue
        if path in seen:
            continue
        seen.add(path)
        out.append({
            "name": os.path.basename(path.rstrip(os.sep)),
            "path": path,
            "default_branch": detect_default_branch(path),
        })
    return out


def register_repos(repos: list) -> dict:
    """Reconcile the `repos` / `branches` tables with `repos`.

    Returns {added, updated, skipped, removed}. Repos not in the input
    set are deleted along with their branch rows.
    """
    stats = {"added": 0, "updated": 0, "skipped": 0, "removed": 0}
    scanned_names = {repo["name"] for repo in repos}

    with SessionLocal() as session:
        for repo in repos:
            existing = session.exec(
                select(Repo).where(Repo.name == repo["name"])
            ).first()

            if existing is not None:
                if (existing.path != repo["path"]
                        or existing.default_branch != repo["default_branch"]):
                    existing.path = repo["path"]
                    existing.default_branch = repo["default_branch"]
                    existing.updated_at = datetime.now(timezone.utc).strftime(
                        "%Y-%m-%d %H:%M:%S"
                    )
                    session.add(existing)
                    stats["updated"] += 1
                else:
                    stats["skipped"] += 1
                target_repo = existing
            else:
                target_repo = Repo(
                    name=repo["name"], path=repo["path"],
                    default_branch=repo["default_branch"],
                )
                session.add(target_repo)
                session.flush()
                stats["added"] += 1

            branch_exists = session.exec(
                select(Branch).where(
                    Branch.repo_id == target_repo.id,
                    Branch.name == repo["default_branch"],
                )
            ).first()
            if branch_exists is None:
                session.add(Branch(
                    repo_id=target_repo.id,
                    name=repo["default_branch"],
                ))

        for db_repo in session.exec(select(Repo)).all():
            if db_repo.name not in scanned_names:
                for b in session.exec(
                    select(Branch).where(Branch.repo_id == db_repo.id)
                ).all():
                    session.delete(b)
                session.delete(db_repo)
                stats["removed"] += 1

        session.commit()
    _sync_log().write(
        'repos_registered',
        added=stats['added'], updated=stats['updated'],
        skipped=stats['skipped'], removed=stats['removed'],
        scanned_count=len(repos),
    )
    return stats


# ── add / remove ─────────────────────────────────────────────

class RepoAddError(ValueError):
    """Raised when add_repo() rejects a path."""


def add_repo(path: str) -> dict:
    """Register a single git repo by absolute path.

    Validates the path is a git working tree, normalizes it, appends
    to `settings.repo_paths`, and upserts a `Repo` + default-branch
    `Branch` row. Returns the registered descriptor (name, path,
    default_branch). Raises `RepoAddError` for invalid or duplicate
    paths.
    """
    norm = _normalize(path)
    if not os.path.isdir(norm):
        raise RepoAddError(f"path does not exist: {norm}")
    if not is_git_repo(norm):
        raise RepoAddError(f"not a git repository: {norm}")

    name = os.path.basename(norm.rstrip(os.sep))
    paths = _load_repo_paths()
    if norm in paths:
        raise RepoAddError(f"repo already registered: {name}")

    # Reject same-name collisions (different paths).
    with SessionLocal() as session:
        clash = session.exec(select(Repo).where(Repo.name == name)).first()
    if clash is not None and _normalize(clash.path) != norm:
        raise RepoAddError(
            f"another repo is already registered as '{name}' at {clash.path}"
        )

    default_branch = detect_default_branch(norm)
    paths.append(norm)
    _save_repo_paths(paths)
    register_repos([
        {"name": name, "path": norm, "default_branch": default_branch}
    ] + [
        # Preserve the other registered repos so the reconcile step
        # doesn't remove them.
        {
            "name": os.path.basename(p.rstrip(os.sep)),
            "path": p,
            "default_branch": detect_default_branch(p),
        }
        for p in paths if p != norm and is_git_repo(p)
    ])
    return {"name": name, "path": norm, "default_branch": default_branch}


def _is_orphan_path(path: str) -> bool:
    """A repo row is orphaned if its on-disk path is gone or lives under
    the OS temp dir (a leftover from a test or ad-hoc scratch run).

    `resolve_or_create_repo` in `lib.topics.snapshots` lazy-inserts a
    `Repo` row whenever proposals/snapshots touch an unregistered path,
    so a failed test or aborted manual run can leave a stale row behind
    pointing at a `/T/regin-*-*/repo` that no longer exists.
    """
    if not path:
        return True
    try:
        resolved = os.path.realpath(path)
    except OSError:
        return True
    if not os.path.isdir(resolved):
        return True
    tmp_root = os.path.realpath(tempfile.gettempdir())
    rel = os.path.relpath(resolved, tmp_root)
    return not rel.startswith("..") and rel != "."


def prune_orphan_repos(*, dry_run: bool = False) -> list[dict]:
    """Drop `Repo` rows whose path is missing or under `$TMPDIR`.

    Skips entries that are still listed in `settings.repo_paths` — if a
    user has explicitly registered a path under /tmp we trust them. FK
    dependents on `repos` (proposal_runs, graph_snapshots, topic_audits)
    cascade in schema; branches don't, so we delete those manually like
    `remove_repo` does.

    Returns one dict per pruned (or pruneable, in dry-run) row with
    {id, name, path, reason}.
    """
    registered = set(_load_repo_paths())
    candidates: list[dict] = []
    with SessionLocal() as session:
        for repo in session.exec(select(Repo)).all():
            if _normalize(repo.path) in registered:
                continue
            if not _is_orphan_path(repo.path):
                continue
            reason = "missing_path" if not os.path.isdir(repo.path) else "under_tmpdir"
            candidates.append({
                "id": repo.id, "name": repo.name,
                "path": repo.path, "reason": reason,
            })

        if dry_run or not candidates:
            return candidates

        ids = [c["id"] for c in candidates]
        for branch in session.exec(
            select(Branch).where(Branch.repo_id.in_(ids))
        ).all():
            session.delete(branch)
        session.exec(delete(SessionRepo).where(SessionRepo.repo_id.in_(ids)))
        for repo in session.exec(select(Repo).where(Repo.id.in_(ids))).all():
            session.delete(repo)
        session.commit()
    _sync_log().write(
        "orphan_repos_pruned",
        pruned_count=len(candidates),
        names=[c["name"] for c in candidates],
    )
    return candidates


def remove_repo(name: str) -> dict:
    """Unregister a repo by name.

    Drops it from `settings.repo_paths` and deletes its `Repo`/`Branch`
    rows. Returns `{removed: bool, name, path}`. The repo's source
    tree on disk is untouched.
    """
    paths = _load_repo_paths()
    target_path = None
    for p in paths:
        if os.path.basename(p.rstrip(os.sep)) == name:
            target_path = p
            break

    new_paths = [p for p in paths if p != target_path] if target_path else paths
    if new_paths != paths:
        _save_repo_paths(new_paths)

    removed = False
    repo_id = None
    with SessionLocal() as session:
        repo = session.exec(select(Repo).where(Repo.name == name)).first()
        if repo is not None:
            repo_id = repo.id
            for b in session.exec(
                select(Branch).where(Branch.repo_id == repo.id)
            ).all():
                session.delete(b)
            # session_repos has no FK cascade — drop its tags manually
            # so removing a repo doesn't orphan session→repo links.
            session.exec(delete(SessionRepo).where(SessionRepo.repo_id == repo.id))
            session.delete(repo)
            session.commit()
            removed = True

    if removed or target_path is not None:
        _sync_log().write(
            "repo_removed", name=name, path=target_path,
            repo_id=repo_id, db_row_removed=removed,
        )
    return {"removed": removed or target_path is not None,
            "name": name, "path": target_path}
