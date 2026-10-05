"""Read a confined source: worktree bytes, committed bytes at a git rev, or
the content a Read span captured. Every path goes through R1 first."""

from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

from sqlmodel import select

from lib.activity_log import get_activity_logger
from lib.files.classify import TEXT_KINDS, Classification, classify
from lib.files.confine import Confined, confine
from lib.files.errors import FileAccessError
from lib.files.sources import (
    WORKTREE, FileSource, PathSource, SpanSource, source_to_dict,
)
from lib.orm import SessionLocal
from lib.orm.models import SessionSpan

log = get_activity_logger("files")


@dataclass(frozen=True)
class Loaded:
    """One readable file. Exactly one of `path` (worktree, streamed from disk)
    and `data` (a git blob or captured span content) is set."""
    name: str
    size: int
    abs_path: Path | None
    path: Path | None = None
    data: bytes | None = None
    first_line: int = 1
    confined: Confined | None = None

    def read(self, n: int | None) -> bytes:
        if self.data is not None:
            return self.data if n is None else self.data[:n]
        try:
            with open(self.path, "rb") as fh:
                return fh.read() if n is None else fh.read(n)
        except OSError:
            raise FileAccessError("unreadable") from None


def _git(base: Path, *args: str) -> subprocess.CompletedProcess | None:
    """Only plumbing that runs no repo-configured command: a GET must not
    execute `core.fsmonitor`, a diff driver or a clean filter that the
    repo's own `.git/config` names."""
    try:
        return subprocess.run(
            ["git", "-C", str(base), "-c", "core.fsmonitor=false",
             "--no-optional-locks", *args],
            capture_output=True, check=False)
    except OSError:
        return None


def git_blob(confined: Confined, rev: str) -> bytes:
    """`<rev>:./<rel>` resolves inside git's object store, so it never
    follows a symlink on disk the way a worktree read would."""
    res = _git(confined.base, "cat-file", "blob", f"{rev}:./{confined.rel}")
    if res is None or res.returncode != 0:
        raise FileAccessError("missing")
    return res.stdout


def _stdout(res: subprocess.CompletedProcess | None) -> str | None:
    if res is None or res.returncode != 0:
        return None
    return res.stdout.decode().strip() or None


def git_state(confined: Confined | None) -> tuple[bool, bool]:
    """(in_head, diffable): the path is in HEAD, and its worktree bytes hash
    differently. Compared by object id rather than `git diff`, which would
    run the repo's clean filters."""
    if confined is None:
        return False, False
    head = _stdout(_git(confined.base, "rev-parse", "--verify", "-q",
                        f"HEAD:./{confined.rel}"))
    if head is None:
        return False, False
    if not confined.abs_path.is_file():
        return True, False
    work = _stdout(_git(confined.base, "hash-object", "--no-filters", "--",
                        str(confined.abs_path)))
    return True, work is not None and work != head


def _load_path(source: PathSource) -> Loaded:
    confined = confine(source.root, source.path)
    name = confined.abs_path.name
    if source.root.rev != WORKTREE:
        data = git_blob(confined, source.root.rev)
        return Loaded(name, len(data), confined.abs_path, data=data,
                      confined=confined)
    try:
        if not confined.abs_path.is_file():
            raise FileAccessError("missing")
        size = confined.abs_path.stat().st_size
    except OSError:
        raise FileAccessError("unreadable") from None
    return Loaded(name, size, confined.abs_path, path=confined.abs_path,
                  confined=confined)


def _read_span_attrs(source: SpanSource) -> dict:
    with SessionLocal() as session:
        row = session.exec(
            select(SessionSpan.name, SessionSpan.attributes)
            .where(SessionSpan.trace_id == source.trace_id)
            .where(SessionSpan.span_id == source.span_id)
        ).first()
    if row is None or row[0] != "tool.Read":
        raise FileAccessError("missing")
    try:
        return json.loads(row[1] or "{}")
    except ValueError:
        raise FileAccessError("unreadable") from None


def _load_span(source: SpanSource) -> Loaded:
    attrs = _read_span_attrs(source)
    content = attrs.get("content")
    if not isinstance(content, str):
        raise FileAccessError("missing")
    file_path = attrs.get("file_path") if isinstance(attrs.get("file_path"), str) else None
    data = content.encode("utf-8")
    try:
        first_line = max(1, int(attrs.get("start_line") or 1))
    except (TypeError, ValueError):
        first_line = 1
    name = os.path.basename(file_path) if file_path else source.span_id
    return Loaded(name, len(data), Path(file_path) if file_path else None,
                  data=data, first_line=first_line)


def load(source: FileSource) -> Loaded:
    if isinstance(source, SpanSource):
        return _load_span(source)
    return _load_path(source)


def read_meta(source: FileSource) -> dict:
    loaded = load(source)
    kind: Classification = classify(loaded.name, loaded.size, loaded.read)
    in_head, diffable = git_state(loaded.confined)
    diffable = diffable and kind.preview in TEXT_KINDS
    log.read("file_meta", preview=kind.preview, size=loaded.size)
    return {
        "source": source_to_dict(source),
        "name": loaded.name,
        "abs_path": str(loaded.abs_path) if loaded.abs_path else None,
        "size": loaded.size,
        "mime": kind.mime,
        "preview": kind.preview,
        "language": kind.language,
        "first_line": loaded.first_line,
        "in_head": in_head,
        "diffable": diffable,
    }


def read_raw(source: FileSource) -> tuple[Loaded, str]:
    """The loaded file and the mime it is served as."""
    loaded = load(source)
    mime = classify(loaded.name, loaded.size, loaded.read).mime
    log.read("file_raw", size=loaded.size)
    return loaded, mime
