"""R1 — who may read what, decided before any byte is read.

A path is checked by `realpath` against its root, so a symlink that leaves the
root is refused like a `..`. A session source also admits a path that a
file-touching span of *that* trace names: "the agent in this session touched
it" is an identifier-based grant, where a cwd prefix alone is not evidence
enough — agents legitimately work in `~/.claude/`, sibling worktrees and /tmp.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import text

from lib.activity_log import get_activity_logger
from lib.files.errors import FileAccessError
from lib.files.sources import (
    FileSource, PathSource, ProjectRoot, Root, SessionRoot,
)
from lib.orm import SessionLocal
from lib.orm.models.sync import Repo

log = get_activity_logger("files")

FILE_TOUCHING_SPANS = (
    "tool.Read", "tool.Edit", "tool.Write", "tool.MultiEdit",
    "tool.NotebookEdit",
)

_SPAN_PATHS_SQL = text(
    "SELECT DISTINCT COALESCE(json_extract(attributes, '$.file_path'),"
    " json_extract(attributes, '$.notebook_path')) FROM session_spans"
    " WHERE trace_id = :trace_id AND name IN ("
    + ",".join(f"'{n}'" for n in FILE_TOUCHING_SPANS) + ")"
)


@dataclass(frozen=True)
class Confined:
    """A path admitted by R1. `base` is a real directory and `rel` the path
    under it, which is how git is asked about the file (`<rev>:./<rel>`)."""
    abs_path: Path
    base: Path
    rel: str


def authorize(source: FileSource, role: str | None) -> None:
    """Project sources need editor or above; the session surface is
    admin-only, so session and span sources need admin."""
    if isinstance(source, PathSource) and isinstance(source.root, ProjectRoot):
        allowed = role in ("editor", "admin")
    else:
        allowed = role == "admin"
    if not allowed:
        raise FileAccessError("forbidden")


def _inside(path: Path, base: Path) -> bool:
    return path == base or base in path.parents


def _repo_base(project_id: int) -> Path:
    with SessionLocal() as session:
        repo = session.get(Repo, project_id)
    if repo is None:
        raise FileAccessError("missing")
    return Path(os.path.realpath(repo.path))


def _confine_project(root: ProjectRoot, path: str) -> Confined:
    if os.path.isabs(path):
        raise FileAccessError("outside_root")
    base = _repo_base(root.project_id)
    real = Path(os.path.realpath(base / path))
    if not _inside(real, base):
        raise FileAccessError("outside_root")
    return Confined(real, base, os.path.relpath(real, base))


def span_named_paths(trace_id: str) -> set[str]:
    """Every path a file-touching span of `trace_id` names, as realpaths."""
    with SessionLocal() as session:
        rows = session.execute(_SPAN_PATHS_SQL, {"trace_id": trace_id}).all()
    return {os.path.realpath(r[0]) for r in rows if isinstance(r[0], str) and r[0]}


def _session_base(trace_id: str) -> Path | None:
    from lib.agent_bridge.roots import session_cwd  # noqa: PLC0415

    cwd = session_cwd(trace_id)
    return Path(os.path.realpath(cwd)) if cwd is not None else None


def _confine_session(root: SessionRoot, path: str) -> Confined:
    if not os.path.isabs(path):
        raise FileAccessError("outside_root")
    real = Path(os.path.realpath(path))
    base = _session_base(root.trace_id)
    if base is not None and _inside(real, base):
        return Confined(real, base, os.path.relpath(real, base))
    if str(real) in span_named_paths(root.trace_id):
        return Confined(real, real.parent, real.name)
    log.read("file_outside_root", trace_id=root.trace_id)
    raise FileAccessError("outside_root")


def confine(root: Root, path: str) -> Confined:
    """The R1 path check. Takes only the root and the path inside it."""
    if isinstance(root, ProjectRoot):
        return _confine_project(root, path)
    return _confine_session(root, path)
