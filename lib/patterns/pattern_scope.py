"""Resolve a pattern's effective scope from `pattern_deployments`.

A pattern's rules should fire only where the pattern is actually deployed:
  - scope='global' deployment → fires on any file
  - scope='project' deployment(s) → fires only on files inside those repos
  - no deployments → does not fire

The source of truth is the `pattern_deployments` table; nothing else
(no frontmatter, no extra column on `pattern_docs`) carries this scope.

The hook handler resolves each rule's `guide` (pattern slug) through
`pattern_allowed_for_file` to decide whether to run the rule. The web
API uses `describe` to surface a per-rule scope badge.
"""

from __future__ import annotations

import os
from typing import Optional

from sqlmodel import select

from lib.orm import SessionLocal
from lib.orm.models import PatternDeployment, Repo


_CACHE: dict[str, dict] = {}


def reset_cache() -> None:
    _CACHE.clear()


def _load(slug: str) -> dict:
    cached = _CACHE.get(slug)
    if cached is not None:
        return cached

    with SessionLocal() as session:
        stmt = (
            select(PatternDeployment.scope, PatternDeployment.project_id, Repo.path)
            .outerjoin(Repo, PatternDeployment.project_id == Repo.id)
            .where(PatternDeployment.pattern_slug == slug)
        )
        rows = session.exec(stmt).all()

    is_global = False
    project_ids: list[int] = []
    project_paths: list[str] = []
    for scope, project_id, repo_path in rows:
        if scope == "global":
            is_global = True
        elif scope == "project" and project_id is not None:
            project_ids.append(project_id)
            if repo_path:
                project_paths.append(os.path.realpath(repo_path))

    entry = {
        "global": is_global,
        "project_ids": project_ids,
        "project_paths": project_paths,
    }
    _CACHE[slug] = entry
    return entry


def _is_inside(file_path: str, repo_path: str) -> bool:
    file_real = os.path.realpath(file_path)
    repo_real = repo_path.rstrip(os.sep) + os.sep
    return file_real == repo_real.rstrip(os.sep) or file_real.startswith(repo_real)


def pattern_allowed_for_file(slug: Optional[str], file_path: str) -> bool:
    """Return True if the pattern's rules should fire on this file.

    `slug=None` (a rule without a linked guide) is treated as allowed —
    rules without skill attribution keep their pre-refactor behavior of
    firing everywhere.
    """
    if not slug:
        return True
    entry = _load(slug)
    if entry["global"]:
        return True
    if not entry["project_paths"]:
        return False
    return any(_is_inside(file_path, p) for p in entry["project_paths"])


def pattern_allowed_for_repo(slug: Optional[str], repo_name: Optional[str]) -> bool:
    """Return True if the pattern is deployed globally OR to `repo_name`.

    `repo_name=None` means "no specific repo context" — only global
    skills pass. `slug=None` is treated as allowed (see above).
    """
    if not slug:
        return True
    entry = _load(slug)
    if entry["global"]:
        return True
    if repo_name is None or not entry["project_ids"]:
        return False
    with SessionLocal() as session:
        stmt = select(Repo.id).where(Repo.name == repo_name)
        row = session.exec(stmt).first()
    if row is None:
        return False
    repo_id = row[0] if isinstance(row, tuple) else row
    return repo_id in entry["project_ids"]


def describe(slug: Optional[str]) -> dict:
    """Return a JSON-friendly scope descriptor for an API payload.

    Shape: `{global: bool, project_ids: [int]}`. `slug=None` returns
    `{global: True, project_ids: []}` — same semantics as
    `pattern_allowed_for_file` (no skill → no scope restriction).
    """
    if not slug:
        return {"global": True, "project_ids": []}
    entry = _load(slug)
    return {"global": entry["global"], "project_ids": list(entry["project_ids"])}
