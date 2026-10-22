"""`ws:<id>` as a Pipeline scope: the set of repo ids a workspace contains.

A project scope is one repo id; a workspace scope is the tuple of its member
repo ids, derived from paths at read time like every other membership read.
Readers take `project_id` as `int | tuple[int, ...]` and narrow with
`project_clause` / `project_sql`, so one predicate serves every surface.
"""

from __future__ import annotations

from typing import Optional, Union

from sqlmodel import select

from lib.orm import SessionLocal
from lib.orm.models import Repo, Workspace
from lib.sync.repo_discovery import _normalize

WS_PREFIX = "ws:"


class Members(tuple):
    """A workspace's member repo ids, still knowing the `ws:<id>` it was
    read from — so an echoed scope names the workspace, not its repos."""

    scope: str

    def __new__(cls, ids, scope: str):
        out = super().__new__(cls, ids)
        out.scope = scope
        return out


ProjectId = Union[int, tuple[int, ...]]


def is_ws(raw: str | None) -> bool:
    return (raw or "").strip().startswith(WS_PREFIX)


def member_ids(raw: str) -> Optional[tuple[int, ...]]:
    """The member repo ids of the workspace `raw` names, or None when no such
    workspace exists — the caller then reads the scope as `all`, so a stale
    link or a deleted workspace widens instead of stranding the page."""
    from lib.workspaces import _under  # noqa: PLC0415

    try:
        ws_id = int(raw.strip()[len(WS_PREFIX):])
    except ValueError:
        return None
    with SessionLocal() as session:
        ws = session.get(Workspace, ws_id)
        if ws is None:
            return None
        repos = session.exec(select(Repo)).all()
        return Members(sorted(r.id for r in repos
                              if _under(_normalize(r.path), ws.root_path)),
                       f"{WS_PREFIX}{ws_id}")


def project_clause(column, project_id: ProjectId):
    if isinstance(project_id, tuple):
        return column.in_(project_id)
    return column == project_id


def project_sql(column: str, project_id: ProjectId,
                binds: dict, name: str = "project_id") -> str:
    """The same predicate for a text-SQL reader; adds its binds to `binds`."""
    if not isinstance(project_id, tuple):
        binds[name] = project_id
        return f"{column} = :{name}"
    if not project_id:
        return "0 = 1"
    names = [f"{name}_{i}" for i in range(len(project_id))]
    binds.update(zip(names, project_id))
    return f"{column} IN ({', '.join(':' + n for n in names)})"


def echo(project_id: Optional[ProjectId]):
    """The scope as the client spelled it: `ws:<id>` for a workspace."""
    return getattr(project_id, "scope", project_id)
