"""Track where each pattern has been deployed as a Claude Code skill.

`scope='global'` → deployed to `~/.claude/skills/`.
`scope='project'` → deployed to `<repo.path>/.claude/skills/`, with `project_id` set.

The DB row is a best-effort record: the authoritative state is the on-disk
skill directory. Helpers here only read/write the row — callers are
responsible for invoking `skill_deployer` to actually write/remove files.
"""

from __future__ import annotations

import os
from typing import Optional

from sqlmodel import select

from lib.orm import SessionLocal
from lib.orm.models import PatternDeployment, Repo, User
from lib.activity_log import get_activity_logger as _get_activity_logger


def _patterns_log():
    return _get_activity_logger("patterns")


def record_deployment(pattern_slug: str, scope: str,
                      project_id: Optional[int],
                      deployed_path: str,
                      user_id: Optional[int] = None) -> None:
    """Upsert a deployment row.

    SQLite's UNIQUE constraint treats NULL as distinct from NULL, so we
    can't rely on a single INSERT ... ON CONFLICT for the global
    (project_id IS NULL) case. Delete any matching row in the same
    transaction and insert fresh — the SQLModel Session batches both
    statements into one commit.
    """
    with SessionLocal() as session:
        stmt = select(PatternDeployment).where(
            PatternDeployment.pattern_slug == pattern_slug,
            PatternDeployment.scope == scope,
        )
        if project_id is None:
            stmt = stmt.where(PatternDeployment.project_id.is_(None))
        else:
            stmt = stmt.where(PatternDeployment.project_id == project_id)

        for existing in session.exec(stmt).all():
            session.delete(existing)

        session.add(PatternDeployment(
            pattern_slug=pattern_slug, scope=scope, project_id=project_id,
            deployed_path=deployed_path, deployed_by=user_id,
        ))
        session.commit()
    _patterns_log().write(
        "deployment_recorded",
        pattern_slug=pattern_slug, scope=scope, project_id=project_id,
        deployed_path=deployed_path, user_id=user_id,
    )


def list_deployments(pattern_slug: Optional[str] = None,
                     project_id: Optional[int] = None) -> list[dict]:
    """Return deployments joined with repo/user metadata.

    Filters by any combination of `pattern_slug` and `project_id`.
    Shape matches the legacy dict (project_name, project_path,
    deployed_by_username) so blueprints consuming this keep working
    unchanged.
    """
    with SessionLocal() as session:
        stmt = (
            select(
                PatternDeployment.id, PatternDeployment.pattern_slug,
                PatternDeployment.scope, PatternDeployment.project_id,
                PatternDeployment.deployed_path, PatternDeployment.deployed_at,
                PatternDeployment.deployed_by,
                Repo.name.label("project_name"),
                Repo.path.label("project_path"),
                User.username.label("deployed_by_username"),
            )
            .outerjoin(Repo, PatternDeployment.project_id == Repo.id)
            .outerjoin(User, PatternDeployment.deployed_by == User.id)
        )
        if pattern_slug is not None:
            stmt = stmt.where(PatternDeployment.pattern_slug == pattern_slug)
        if project_id is not None:
            stmt = stmt.where(PatternDeployment.project_id == project_id)
        stmt = stmt.order_by(
            PatternDeployment.scope, Repo.name, PatternDeployment.deployed_at.desc(),
        )

        rows = session.exec(stmt).all()
        # exec() on a select of columns yields tuples with ._asdict() shim.
        return [dict(r._mapping) for r in rows]


def untracked_project_deployments(pattern_slug: str) -> list[dict]:
    """Project skill dirs present on disk but missing a DB row.

    Scans each registered repo's active-provider project skills dir for a
    `<pattern_slug>` directory that no `pattern_deployments` row covers.
    Deployments that landed on disk outside the two web endpoints (manual
    copies, external skills, a pre-ledger deploy) show up here so the UI can
    offer a one-click backfill. Rows are shaped like `list_deployments`
    output plus `tracked=False`.
    """
    from lib.providers import get_active_provider

    subpath = get_active_provider().project_skills_subpath()
    tracked_ids = {
        d["project_id"]
        for d in list_deployments(pattern_slug=pattern_slug)
        if d["scope"] == "project"
    }
    with SessionLocal() as session:
        repos = session.exec(select(Repo)).all()
        repos = [(r.id, r.name, r.path) for r in repos]

    out: list[dict] = []
    for repo_id, repo_name, repo_path in repos:
        if repo_id in tracked_ids:
            continue
        deployed_path = os.path.join(repo_path, *subpath, pattern_slug)
        if os.path.isdir(deployed_path):
            out.append({
                "id": f"untracked:{repo_id}",
                "pattern_slug": pattern_slug,
                "scope": "project",
                "project_id": repo_id,
                "deployed_path": deployed_path,
                "deployed_at": None,
                "deployed_by": None,
                "project_name": repo_name,
                "project_path": repo_path,
                "deployed_by_username": None,
                "tracked": False,
            })
    return out


def remove_deployment(pattern_slug: str, scope: str,
                      project_id: Optional[int] = None) -> bool:
    """Delete a deployment row. Returns True if a row was removed."""
    with SessionLocal() as session:
        stmt = select(PatternDeployment).where(
            PatternDeployment.pattern_slug == pattern_slug,
            PatternDeployment.scope == scope,
        )
        if project_id is None:
            stmt = stmt.where(PatternDeployment.project_id.is_(None))
        else:
            stmt = stmt.where(PatternDeployment.project_id == project_id)

        rows = session.exec(stmt).all()
        for row in rows:
            session.delete(row)
        session.commit()
        removed_count = len(rows)
    if removed_count > 0:
        _patterns_log().write(
            "deployment_removed",
            pattern_slug=pattern_slug, scope=scope, project_id=project_id,
            removed_count=removed_count,
        )
    return removed_count > 0
