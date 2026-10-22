"""Workspaces — a root directory whose members are the registered repos under it.

Membership is never stored. `members()` derives it from `Repo.path`, so a repo
registered after the workspace was created joins it, and removing a workspace
touches nothing but its own row.
"""

from __future__ import annotations

import os

from sqlmodel import select

from lib.activity_log import get_activity_logger
from lib.orm import SessionLocal
from lib.orm.models import Repo, Workspace
from lib.sync.repo_discovery import RepoAddError, _normalize, add_repo


def _log():
    return get_activity_logger("sync")


class WorkspaceError(ValueError):
    """A create request was refused; `field` names the offending input."""

    def __init__(self, message: str, *, field: str, status: int = 400):
        super().__init__(message)
        self.field = field
        self.status = status


def is_worktree_root(path: str) -> bool:
    """`path` is the top of a git working tree (a `.git` dir, or the `.git`
    file a linked worktree or submodule carries).

    Deliberately not `git rev-parse`: that succeeds for any directory *inside*
    a repo, which would count every child of a git root as a repo of its own.
    """
    return os.path.exists(os.path.join(path, ".git"))


def _child_worktrees(root: str) -> list[str]:
    try:
        entries = sorted(os.scandir(root), key=lambda e: e.name)
    except OSError:
        return []
    return [
        _normalize(e.path) for e in entries
        if e.name != ".git" and e.is_dir() and is_worktree_root(e.path)
    ]


def _registered_paths(session) -> set[str]:
    return {_normalize(r.path) for r in session.exec(select(Repo)).all()}


def _under(path: str, root: str) -> bool:
    return path == root or path.startswith(os.path.join(root, ""))


def members(ws: Workspace, repos: list[Repo]) -> list[dict]:
    return [
        {"id": r.id, "name": r.name, "path": r.path}
        for r in repos if _under(_normalize(r.path), ws.root_path)
    ]


def _to_dict(ws: Workspace, repos: list[Repo]) -> dict:
    return {
        "id": ws.id, "name": ws.name, "root_path": ws.root_path,
        "created_at": ws.created_at, "members": members(ws, repos),
    }


def probe(path: str) -> dict:
    """Classify `path` for the add-project dialog: a repo, a folder of repos,
    an existing workspace root, or none of those."""
    norm = _normalize(path)
    if not os.path.isdir(norm):
        return {"path": norm, "exists": False, "is_git": False,
                "child_repos": [], "workspace_id": None}
    with SessionLocal() as session:
        registered = _registered_paths(session)
        ws = session.exec(
            select(Workspace).where(Workspace.root_path == norm)
        ).first()
    return {
        "path": norm,
        "exists": True,
        "is_git": is_worktree_root(norm),
        "child_repos": [
            {"name": os.path.basename(p), "path": p, "registered": p in registered}
            for p in _child_worktrees(norm)
        ],
        "workspace_id": ws.id if ws else None,
    }


def _check_conflicts(session, name: str, root: str) -> None:
    if session.exec(select(Workspace).where(Workspace.root_path == root)).first():
        raise WorkspaceError(f"a workspace is already rooted at {root}",
                             field="root_path", status=409)
    if session.exec(select(Workspace).where(Workspace.name == name)).first():
        raise WorkspaceError(f"a workspace named '{name}' already exists",
                             field="name", status=409)


def _register_trees(trees: list[str], registered: set[str]) -> tuple[list[dict], list[dict]]:
    added, skipped = [], []
    for tree in trees:
        if tree in registered:
            continue
        try:
            added.append(add_repo(tree))
        except RepoAddError as exc:
            skipped.append({"path": tree, "reason": str(exc)})
    return added, skipped


def _root_and_trees(path: str) -> tuple[str, list[str]]:
    raw = (path or "").strip()
    if not raw or not os.path.isabs(raw):
        raise WorkspaceError("path must be an absolute directory", field="path")
    root = _normalize(raw)
    if not os.path.isdir(root):
        raise WorkspaceError(f"path does not exist: {root}", field="path")
    trees = ([root] if is_worktree_root(root) else []) + _child_worktrees(root)
    if not trees:
        raise WorkspaceError(
            f"no git repository at or directly under {root}", field="path")
    return root, trees


def create(path: str, name: str | None = None) -> dict:
    """Create a workspace rooted at `path`, registering every git working tree
    at the root or directly beneath it that is not registered yet.

    Returns `{workspace, registered, skipped}`; `skipped` lists trees whose
    registration `add_repo` refused (e.g. a repo-name clash). Raises
    `WorkspaceError` when the path is not an absolute directory, holds no git
    tree that is or could be registered, or the name/root is already taken.
    """
    root, trees = _root_and_trees(path)
    ws_name = (name or "").strip() or os.path.basename(root) or root

    with SessionLocal() as session:
        _check_conflicts(session, ws_name, root)
        registered = _registered_paths(session)
    added, skipped = _register_trees(trees, registered)
    if not added and not registered.intersection(trees):
        raise WorkspaceError(
            "no git repository under the root could be registered: "
            + "; ".join(s["reason"] for s in skipped), field="path")

    with SessionLocal() as session:
        _check_conflicts(session, ws_name, root)
        ws = Workspace(name=ws_name, root_path=root)
        session.add(ws)
        session.commit()
        session.refresh(ws)
        out = _to_dict(ws, session.exec(select(Repo)).all())
    _log().write("workspace_created", workspace_id=out["id"], name=ws_name,
                 root_path=root, registered=len(added), skipped=len(skipped))
    return {"workspace": out, "registered": added, "skipped": skipped}


def list_workspaces() -> list[dict]:
    with SessionLocal() as session:
        repos = session.exec(select(Repo).order_by(Repo.name)).all()
        rows = session.exec(select(Workspace).order_by(Workspace.name)).all()
        return [_to_dict(ws, repos) for ws in rows]


def launch_choices() -> list[dict]:
    """Each workspace as a run's working directory. `is_git` is read off the
    disk now, because a non-git root cannot host a worktree."""
    return [
        {"id": ws["id"], "name": ws["name"], "root_path": ws["root_path"],
         "repos": len(ws["members"]), "is_git": is_worktree_root(ws["root_path"])}
        for ws in list_workspaces()
    ]


def _delete(where) -> dict | None:
    with SessionLocal() as session:
        ws = session.exec(select(Workspace).where(where)).first()
        if ws is None:
            return None
        gone = {"id": ws.id, "name": ws.name, "root_path": ws.root_path}
        session.delete(ws)
        session.commit()
    _log().write("workspace_removed", workspace_id=gone["id"], name=gone["name"])
    return gone


def remove(workspace_id: int) -> dict | None:
    """Delete the workspace row only; its repos stay registered."""
    return _delete(Workspace.id == workspace_id)


def remove_by_name(name: str) -> dict | None:
    return _delete(Workspace.name == name)
