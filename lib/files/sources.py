"""A file is addressed by a source, never a bare path.

A path source is a **root** plus a **path inside it**, so the confinement
check takes only those two and a future directory listing can reuse it. A
span source names captured content and has no root on disk at all.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Mapping, Union

from lib.files.errors import FileAccessError

WORKTREE = "WORKTREE"

# `:` is excluded so `<rev>:./<path>` stays unambiguous for git, and a leading
# `-` is excluded so a rev can never be read as an option.
_REV_RE = re.compile(r"^[A-Za-z0-9._/~^@{}][A-Za-z0-9._/~^@{}-]{0,199}$")


@dataclass(frozen=True)
class ProjectRoot:
    project_id: int
    rev: str = WORKTREE


@dataclass(frozen=True)
class SessionRoot:
    trace_id: str
    rev: str = WORKTREE


Root = Union[ProjectRoot, SessionRoot]


@dataclass(frozen=True)
class PathSource:
    root: Root
    path: str


@dataclass(frozen=True)
class SpanSource:
    trace_id: str
    span_id: str


FileSource = Union[PathSource, SpanSource]


def _rev(args: Mapping[str, str]) -> str:
    rev = (args.get("rev") or WORKTREE).strip()
    if rev != WORKTREE and not _REV_RE.match(rev):
        raise FileAccessError("bad_source")
    return rev


def _project_source(args: Mapping[str, str]) -> PathSource:
    try:
        project_id = int(args["project"])
    except (KeyError, TypeError, ValueError):
        raise FileAccessError("bad_source") from None
    path = args.get("path") or ""
    if not path:
        raise FileAccessError("bad_source")
    return PathSource(ProjectRoot(project_id, _rev(args)), path)


def _session_source(args: Mapping[str, str]) -> FileSource:
    trace_id = args.get("session") or ""
    span_id = args.get("span") or ""
    path = args.get("path") or ""
    if bool(span_id) == bool(path):
        raise FileAccessError("bad_source")
    if span_id:
        return SpanSource(trace_id, span_id)
    return PathSource(SessionRoot(trace_id, _rev(args)), path)


def parse_source(args: Mapping[str, str]) -> FileSource:
    """`project=&path=[&rev=]` | `session=&path=[&rev=]` | `session=&span=`."""
    if any("\x00" in (args.get(k) or "") for k in ("path", "session", "span")):
        raise FileAccessError("bad_source")
    has_project = bool(args.get("project"))
    has_session = bool(args.get("session"))
    if has_project == has_session:
        raise FileAccessError("bad_source")
    if has_project:
        return _project_source(args)
    return _session_source(args)


def source_to_dict(source: FileSource) -> dict:
    if isinstance(source, SpanSource):
        return {"kind": "span", "trace_id": source.trace_id,
                "span_id": source.span_id}
    root = source.root
    if isinstance(root, ProjectRoot):
        return {"kind": "project", "project_id": root.project_id,
                "path": source.path, "rev": root.rev}
    return {"kind": "session", "trace_id": root.trace_id,
            "path": source.path, "rev": root.rev}
