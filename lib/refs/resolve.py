"""References to their live items (`GET /api/refs/resolve`), and the one
`RefItem` shape every reference reader returns.

A chip shows the live title and status; the label stored in the body is only
a snapshot for raw-text readers, so nothing here is cached — a renamed feature
reads renamed on the next render.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from typing import Iterable, Optional

from sqlalchemy import bindparam, func
from sqlmodel import select, text

from lib.activity_log import get_activity_logger
from lib.features import index as feature_index
from lib.memory.engine import MemorySessionLocal
from lib.memory.models import Memory
from lib.orm import SessionLocal
from lib.orm.models import Session as SessionModel, SessionRepo, SessionSpan
from lib.orm.models.defects import Defect
from lib.orm.models.features import Feature
from lib.orm.models.sync import Repo
from lib.orm.models.tasks import AgentTask
from lib.refs.grammar import (
    RefError, decode_file_key, format_label, parse_ref_target, plain_title,
)

log = get_activity_logger("refs")

MAX_REFS = 100
#: A session titled by nothing falls back to its first prompt, which can run
#: to pages; the chip and the menu row show one line of it.
PROMPT_TITLE_MAX = 200

_ROUTES = {"feature": "/features/{}", "task": "/tasks/{}",
           "defect": "/defects/{}", "session": "/trace/sessions/{}",
           "memory": "/memory?id={}"}


@dataclass(frozen=True)
class Project:
    id: int
    name: str
    path: str


NO_PROJECT: tuple = (None, None)


@dataclass(frozen=True)
class ContextFeature:
    id: int
    project_id: Optional[int]


def _row_id(raw, field: str) -> Optional[int]:
    value = str(raw).strip() if raw is not None else ""
    if not value:
        return None
    try:
        row_id = int(value)
    except ValueError:
        raise RefError(f"{field} must be an integer: {value}", field) from None
    # Past 64 bits SQLite cannot even bind the id; no row can carry it.
    if row_id.bit_length() > 63:
        raise RefError(f"unknown {field[:-3]}: {row_id}", field)
    return row_id


def find_project(project_id: Optional[int]) -> Optional[Project]:
    if project_id is None:
        return None
    with SessionLocal() as session:
        repo = session.get(Repo, project_id)
        return Project(id=repo.id, name=repo.name, path=repo.path) \
            if repo is not None else None


def require_project(raw) -> Optional[Project]:
    """The project a request names, or None when it names none. An id that is
    not a registered repo is refused, not ignored: a scope silently dropped
    answers a different question than the one asked."""
    project_id = _row_id(raw, "project_id")
    if project_id is None:
        return None
    project = find_project(project_id)
    if project is None:
        raise RefError(f"unknown project: {project_id}", "project_id")
    return project


def require_feature(raw) -> Optional[ContextFeature]:
    """The feature a composer is writing inside, refused when unknown for
    the same reason an unknown project is."""
    feature_id = _row_id(raw, "feature_id")
    if feature_id is None:
        return None
    with SessionLocal() as session:
        row = session.get(Feature, feature_id)
        if row is None:
            raise RefError(f"unknown feature: {feature_id}", "feature_id")
        return ContextFeature(id=row.id, project_id=row.project_id)


def project_names(ids: Iterable[Optional[int]]) -> dict[int, str]:
    wanted = {pid for pid in ids if pid is not None}
    if not wanted:
        return {}
    with SessionLocal() as session:
        rows = session.exec(select(Repo.id, Repo.name)
                            .where(Repo.id.in_(wanted))).all()
    return {pid: name for pid, name in rows}


def named(project_id: Optional[int], names: dict[int, str]) -> tuple:
    """`(id, name)`, or both None when unfiled or since dropped — the repo
    table carries no foreign key, so a dangling id is a reachable state."""
    name = names.get(project_id) if project_id is not None else None
    return (project_id, name) if name else NO_PROJECT


def feature_titles(ids: Iterable[Optional[int]]) -> dict[int, str]:
    wanted = {fid for fid in ids if fid is not None}
    if not wanted:
        return {}
    with SessionLocal() as session:
        rows = session.exec(select(Feature.id, Feature.title)
                            .where(Feature.id.in_(wanted))).all()
    return {fid: plain_title(title) for fid, title in rows}


def ref_item(kind: str, key: str, title: str, *, status=None,
             project: tuple = NO_PROJECT, feature_id=None,
             feature_title: Optional[str] = None) -> dict:
    # A path is its own title; anything else is read as one line of text.
    title = (title or "") if kind == "file" else plain_title(title)
    route = _ROUTES.get(kind)
    return {"ref": f"{kind}:{key}", "kind": kind, "key": key,
            "label": format_label(kind, key, title), "title": title,
            "status": status, "project_id": project[0],
            "project": project[1], "feature_id": feature_id,
            # "S14 — verify" names nothing until it says whose step it is.
            "feature_title": feature_title,
            "url": route.format(key) if route else None}


def restricted_session(key: str) -> dict:
    """A session as a non-admin may see it: its id and nothing it said.

    Built without a lookup, so a non-admin cannot use resolve to learn which
    trace ids exist either.
    """
    item = ref_item("session", key, "")
    item["url"] = None
    item["restricted"] = True
    return item


def git_paths(repo_path: str, *args: str) -> Optional[list[str]]:
    """The paths a `-z` git listing prints, or None when the repo cannot be
    read. A path that is not UTF-8 has no file key and is skipped."""
    try:
        done = subprocess.run(["git", "-C", repo_path, *args],
                              capture_output=True, check=False)
    except OSError:
        return None
    if done.returncode != 0:
        return None
    paths = []
    for raw in done.stdout.split(b"\0"):
        try:
            paths.append(raw.decode("utf-8"))
        except UnicodeDecodeError:
            continue
    return [path for path in paths if path]


# ── rows → items ──────────────────────────────────────────────

def feature_rows(where: str, binds: dict, *, order: str = "",
                 limit: Optional[int] = None) -> list:
    """`(id, title, stage, project_id, matched)` off the Features tab's own
    statement, so a chip's stage is the stage the tab shows."""
    sql = (f"{feature_index._CTES} SELECT id, title, stage, project_id,"
           f" COUNT(*) OVER () AS matched FROM rows{where}{order}")
    stmt = text(sql + (" LIMIT :limit" if limit is not None else ""))
    if limit is not None:
        binds = {**binds, "limit": limit}
    if "ids" in binds:
        stmt = stmt.bindparams(bindparam("ids", expanding=True))
    with SessionLocal() as session:
        return session.exec(stmt.bindparams(**binds)).all()


def feature_items(rows) -> list[dict]:
    names = project_names(row[3] for row in rows)
    return [ref_item("feature", str(fid), title, status=stage,
                     project=named(pid, names))
            for fid, title, stage, pid, *_ in rows]


def task_items(rows) -> list[dict]:
    names = project_names(row.project_id for row in rows)
    parents = feature_titles(row.feature_id for row in rows)
    return [ref_item("task", str(row.id), row.title, status=row.status,
                     project=named(row.project_id, names),
                     feature_id=row.feature_id,
                     feature_title=parents.get(row.feature_id))
            for row in rows]


def defect_items(rows) -> list[dict]:
    """`rows` are `(Defect, project_id)` — a defect's project is its
    feature's."""
    names = project_names(pid for _, pid in rows)
    parents = feature_titles(row.feature_id for row, _ in rows)
    return [ref_item("defect", str(row.id), row.symptom, status=row.status,
                     project=named(pid, names), feature_id=row.feature_id,
                     feature_title=parents.get(row.feature_id))
            for row, pid in rows]


def _first_prompts(session, trace_ids: list[str]) -> dict[str, str]:
    if not trace_ids:
        return {}
    ranked = (select(SessionSpan.trace_id,
                     func.json_extract(SessionSpan.attributes, "$.text")
                     .label("prompt"),
                     func.row_number().over(
                         partition_by=SessionSpan.trace_id,
                         order_by=(SessionSpan.start_time, SessionSpan.id))
                     .label("n"))
              .where(SessionSpan.name == "prompt",
                     SessionSpan.trace_id.in_(trace_ids))
              .subquery())
    rows = session.exec(select(ranked.c.trace_id, ranked.c.prompt)
                        .where(ranked.c.n == 1)).all()
    return {tid: " ".join(str(prompt or "").split())[:PROMPT_TITLE_MAX]
            for tid, prompt in rows}


def _session_projects(session, trace_ids: list[str]) -> dict[str, int]:
    """A session's project: the repo it started in, else the lowest-id repo
    it wrote to."""
    if not trace_ids:
        return {}
    rows = session.exec(select(SessionRepo.trace_id, SessionRepo.repo_id)
                        .where(SessionRepo.trace_id.in_(trace_ids))
                        .order_by(SessionRepo.trace_id,
                                  SessionRepo.is_primary.desc(),
                                  SessionRepo.repo_id)).all()
    out: dict[str, int] = {}
    for trace_id, repo_id in rows:
        out.setdefault(trace_id, repo_id)
    return out


def session_items(session, rows) -> list[dict]:
    """`rows` are `(trace_id, title)`, read inside `session`."""
    untitled = [tid for tid, title in rows if not (title or "").strip()]
    prompts = _first_prompts(session, untitled)
    projects = _session_projects(session, [tid for tid, _ in rows])
    names = project_names(projects.values())
    return [ref_item("session", tid,
                     (title or "").strip() or prompts.get(tid, ""),
                     project=named(projects.get(tid), names))
            for tid, title in rows]


REPO_SCOPE = "repo:"


def scope_repo(scope: Optional[str]) -> Optional[str]:
    """The repo name a memory scope names; None for `global`."""
    scope = scope or ""
    return scope[len(REPO_SCOPE):] if scope.startswith(REPO_SCOPE) else None


def _repo_ids_by_name(names: set) -> dict[str, int]:
    if not names:
        return {}
    with SessionLocal() as session:
        return {name: rid for rid, name in session.exec(
            select(Repo.id, Repo.name).where(Repo.name.in_(names)))}


def _memory_title(row) -> str:
    return (row.title or "").strip() \
        or (row.body or "").strip().split("\n", 1)[0]


def memory_items(rows) -> list[dict]:
    """A memory's project is the repo its `repo:<name>` scope names."""
    ids = _repo_ids_by_name({scope_repo(row.scope) for row in rows} - {None})
    items = []
    for row in rows:
        name = scope_repo(row.scope)
        project = (ids[name], name) if name in ids else NO_PROJECT
        items.append(ref_item("memory", row.id, _memory_title(row),
                              project=project))
    return items


def file_item(key: str, path: str, project: Optional[Project],
              tracked: Optional[set] = None) -> dict:
    """`exists` is present only when the project's tree could be read: an
    unknown answer is not a `false` one. The url needs the project, because a
    file key alone names no file — which is why `file` has no `_ROUTES` row.
    The key is already percent-encoded per segment, so it is a valid query
    value as it stands."""
    item = ref_item("file", key, path,
                    project=(project.id, project.name) if project
                    else NO_PROJECT)
    if project is not None:
        item["url"] = f"/files?project={project.id}&path={key}"
    if tracked is not None:
        item["exists"] = path in tracked
    return item


# ── resolve ───────────────────────────────────────────────────

def _features(keys, **_) -> list[dict]:
    rows = feature_rows(" WHERE id IN :ids", {"ids": [int(k) for k in keys]})
    return feature_items(rows)


def _tasks(keys, **_) -> list[dict]:
    with SessionLocal() as session:
        rows = session.exec(select(AgentTask).where(
            AgentTask.id.in_([int(k) for k in keys]))).all()
    return task_items(rows)


def _defects(keys, **_) -> list[dict]:
    with SessionLocal() as session:
        rows = session.exec(
            select(Defect, Feature.project_id)
            .join(Feature, Feature.id == Defect.feature_id, isouter=True)
            .where(Defect.id.in_([int(k) for k in keys]))).all()
    return defect_items(rows)


def _sessions(keys, *, can_read_sessions, **_) -> list[dict]:
    if not can_read_sessions:
        return [restricted_session(key) for key in keys]
    with SessionLocal() as session:
        rows = session.exec(select(SessionModel.trace_id, SessionModel.title)
                            .where(SessionModel.trace_id.in_(keys))).all()
        return session_items(session, rows)


def _files(keys, *, project, **_) -> list[dict]:
    tracked = None
    if project is not None:
        listed = git_paths(project.path, "ls-tree", "-r", "-z",
                           "--name-only", "HEAD")
        tracked = set(listed) if listed is not None else None
    return [file_item(key, decode_file_key(key), project, tracked)
            for key in keys]


def _memories(keys, **_) -> list[dict]:
    with MemorySessionLocal() as session:
        rows = session.exec(select(Memory).where(Memory.id.in_(keys))).all()
    return memory_items(rows)


_RESOLVERS = {"feature": _features, "task": _tasks, "defect": _defects,
              "session": _sessions, "file": _files, "memory": _memories}


def items_for(kind: str, keys: list[str], *, project: Optional[Project] = None,
              can_read_sessions: bool) -> list[dict]:
    """The live items for already-valid keys of one kind; a missing one is
    simply absent."""
    return _RESOLVERS[kind](keys, project=project,
                           can_read_sessions=can_read_sessions)


def _targets(refs: list[str]) -> dict[str, list[str]]:
    if len(refs) > MAX_REFS:
        raise RefError(f"at most {MAX_REFS} refs per request, got {len(refs)}",
                       "ref")
    keys: dict[str, list[str]] = {}
    for ref in refs:
        target = parse_ref_target(ref)
        if target is None:
            raise RefError(f"not a reference: {ref}", "ref")
        kind, key = target
        if key not in keys.setdefault(kind, []):
            keys[kind].append(key)
    return keys


def resolve_refs(refs: Iterable[str], *, project_id=None,
                 can_read_sessions: bool) -> dict[str, Optional[dict]]:
    """`{ref: RefItem | None}` for every ref asked; None is not found.

    `project_id` scopes file references (which repo's tree a path is looked
    up in); a session comes back `restricted` unless `can_read_sessions`.
    """
    refs = list(refs)
    keys = _targets(refs)
    project = require_project(project_id)
    out: dict[str, Optional[dict]] = {ref: None for ref in refs}
    for kind, wanted in keys.items():
        for item in items_for(kind, wanted, project=project,
                              can_read_sessions=can_read_sessions):
            out[item["ref"]] = item
    log.read("refs_resolved", count=len(out),
             found=sum(1 for item in out.values() if item is not None))
    return out
