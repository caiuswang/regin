"""Track where each pattern has been deployed as an agent skill.

`scope='global'` → deployed to the active provider's global skills dir
(e.g. `~/.claude/skills/` or `~/.kimi-code/skills/`).
`scope='project'` → deployed to `<repo.path>/<provider-project-subpath>/`,
with `project_id` set.

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


_DEFAULT_PROVIDER = "claude"


def _active_provider_id() -> str:
    """Return the active provider id without creating a circular import."""
    from lib.providers import active_provider_id
    return active_provider_id()


def _patterns_log():
    return _get_activity_logger("patterns")


def _provider_segment_map() -> dict[str, str]:
    """Map each provider's project-skills directory segment (`.claude`,
    `.codex`, `.kimi-code`, `.agent`) to its provider id — the discriminator
    that identifies which provider owns a deployment from its path."""
    from lib.providers import list_provider_ids, build_provider
    seg_map: dict[str, str] = {}
    for pid in list_provider_ids():
        seg = build_provider(pid).project_skills_subpath()[0]
        seg_map.setdefault(seg, pid)
    return seg_map


def _infer_provider_from_path(deployed_path: str, seg_map: dict[str, str]) -> str:
    """Infer the owning provider from a deployed path's directory segment,
    falling back to claude (the only provider before multi-provider support)."""
    parts = (deployed_path or "").replace("\\", "/").split("/")
    for seg, pid in seg_map.items():
        if seg in parts:
            return pid
    return _DEFAULT_PROVIDER


def backfill_null_providers() -> int:
    """Assign a concrete provider to legacy rows (`provider IS NULL`).

    Rows written before the provider column all predate multi-provider
    support; their on-disk location unambiguously identifies the owning
    provider. Without this, a per-provider scan can't see them and re-reports
    the same on-disk skill dir as "untracked", and provider-scoped removes/
    re-deploys miss the NULL row. Returns the number of rows updated.
    """
    seg_map = _provider_segment_map()
    updated = 0
    with SessionLocal() as session:
        rows = session.exec(
            select(PatternDeployment).where(PatternDeployment.provider.is_(None))
        ).all()
        for row in rows:
            row.provider = _infer_provider_from_path(row.deployed_path, seg_map)
            session.add(row)
            updated += 1
        if updated:
            session.commit()
    if updated:
        _patterns_log().write("provider_backfilled", rows=updated)
    return updated


def record_deployment(pattern_slug: str, scope: str,
                      project_id: Optional[int],
                      deployed_path: str,
                      user_id: Optional[int] = None,
                      provider: Optional[str] = None) -> None:
    """Upsert a deployment row.

    SQLite's UNIQUE constraint treats NULL as distinct from NULL, so we
    can't rely on a single INSERT ... ON CONFLICT for the global
    (project_id IS NULL) case. Delete any matching row in the same
    transaction and insert fresh — the SQLModel Session batches both
    statements into one commit.
    """
    provider = provider or _active_provider_id()
    with SessionLocal() as session:
        stmt = select(PatternDeployment).where(
            PatternDeployment.pattern_slug == pattern_slug,
            PatternDeployment.scope == scope,
            PatternDeployment.provider == provider,
        )
        if project_id is None:
            stmt = stmt.where(PatternDeployment.project_id.is_(None))
        else:
            stmt = stmt.where(PatternDeployment.project_id == project_id)

        for existing in session.exec(stmt).all():
            session.delete(existing)

        session.add(PatternDeployment(
            pattern_slug=pattern_slug, scope=scope, project_id=project_id,
            provider=provider, deployed_path=deployed_path, deployed_by=user_id,
        ))
        session.commit()
    _patterns_log().write(
        "deployment_recorded",
        pattern_slug=pattern_slug, scope=scope, project_id=project_id,
        provider=provider, deployed_path=deployed_path, user_id=user_id,
    )


def list_deployments(pattern_slug: Optional[str] = None,
                     project_id: Optional[int] = None,
                     provider: Optional[str] = None) -> list[dict]:
    """Return deployments joined with repo/user metadata.

    Filters by any combination of `pattern_slug`, `project_id`, and
    `provider`. Shape matches the legacy dict (project_name, project_path,
    deployed_by_username) so blueprints consuming this keep working
    unchanged; a ``provider`` key is also included for multi-provider
    surfaces.
    """
    with SessionLocal() as session:
        stmt = (
            select(
                PatternDeployment.id, PatternDeployment.pattern_slug,
                PatternDeployment.scope, PatternDeployment.project_id,
                PatternDeployment.provider, PatternDeployment.deployed_path,
                PatternDeployment.deployed_at, PatternDeployment.deployed_by,
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
        if provider is not None:
            stmt = stmt.where(PatternDeployment.provider == provider)
        stmt = stmt.order_by(
            PatternDeployment.scope, Repo.name, PatternDeployment.deployed_at.desc(),
        )

        rows = session.exec(stmt).all()
        # exec() on a select of columns yields tuples with ._asdict() shim.
        return [dict(r._mapping) for r in rows]


def untracked_project_deployments(pattern_slug: str,
                                   provider: Optional[str] = None) -> list[dict]:
    """Project skill dirs present on disk but missing a DB row.

    Scans each registered repo's project skills dir for a
    `<pattern_slug>` directory that no `pattern_deployments` row covers.
    When `provider` is given only that provider's subpath is scanned;
    otherwise all enabled providers are scanned. Deployments that landed on
    disk outside the two web endpoints (manual copies, external skills, a
    pre-ledger deploy) show up here so the UI can offer a one-click backfill.
    Rows are shaped like `list_deployments` output plus `tracked=False`.
    """
    from lib.providers import build_provider, enabled_provider_ids

    if provider:
        provider_ids = [provider]
    else:
        provider_ids = enabled_provider_ids()

    # Bucket already-tracked project deployments by their effective provider in
    # a single query. A legacy row with provider=NULL predates multi-provider
    # support and belongs to the active provider — without this defense it is
    # invisible to the per-provider scan and the same on-disk skill dir gets
    # re-reported as "untracked".
    active = _active_provider_id()
    tracked_by_provider: dict[str, set] = {}
    for d in list_deployments(pattern_slug=pattern_slug):
        if d["scope"] != "project":
            continue
        eff = d.get("provider") or active
        tracked_by_provider.setdefault(eff, set()).add(d["project_id"])

    with SessionLocal() as session:
        repos = session.exec(select(Repo)).all()
        repos = [(r.id, r.name, r.path) for r in repos]

    out: list[dict] = []
    for pid in provider_ids:
        subpath = build_provider(pid).project_skills_subpath()
        tracked_ids = tracked_by_provider.get(pid, set())
        for repo_id, repo_name, repo_path in repos:
            if repo_id in tracked_ids:
                continue
            deployed_path = os.path.join(repo_path, *subpath, pattern_slug)
            if os.path.isdir(deployed_path):
                out.append({
                    "id": f"untracked:{pid}:{repo_id}",
                    "pattern_slug": pattern_slug,
                    "scope": "project",
                    "project_id": repo_id,
                    "provider": pid,
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
                      project_id: Optional[int] = None,
                      provider: Optional[str] = None) -> bool:
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
        if provider is not None:
            stmt = stmt.where(PatternDeployment.provider == provider)

        rows = session.exec(stmt).all()
        for row in rows:
            session.delete(row)
        session.commit()
        removed_count = len(rows)
    if removed_count > 0:
        _patterns_log().write(
            "deployment_removed",
            pattern_slug=pattern_slug, scope=scope, project_id=project_id,
            provider=provider, removed_count=removed_count,
        )
    return removed_count > 0
