"""`GET /api/refs`: the `@` menu's one grouped search over the six kinds.

Each kind narrows with the predicate its own list endpoint applies to `q` —
features `lib/features/index.py`, tasks `lib/tasks/query.py`, defects
`lib/defects/store.py`, sessions `lib/trace/session_search.py`, memory
`lib/memory/store/reads.py`. Two of those are private to their module and
reached into on purpose: the menu and the list a chip opens must find the same
rows for the same words.

On top of that predicate, one ranking for every kind: exact id or key, then
title prefix, title substring, any other matched field, then recency — with
the selected project's rows first inside each tier. Abandoned features and
archived tasks are outside every list predicate, so only their exact id finds
them.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

from sqlalchemy import case, func
from sqlmodel import select

import lib.memory as memory
from lib.activity_log import get_activity_logger
from lib.defects import store as defect_store
from lib.defects.query import DefectQuery
from lib.features import index as feature_index
from lib.memory.engine import MemorySessionLocal
from lib.memory.models import Memory
from lib.orm import SessionLocal
from lib.orm.models import Session as SessionModel, SessionRepo
from lib.orm.models.defects import Defect
from lib.orm.models.features import Feature
from lib.orm.models.tasks import AgentTask
from lib.refs import resolve
from lib.refs.grammar import (
    KINDS, RefError, canonical_kind, encode_file_key, is_valid_key,
)
from lib.tasks import query as task_query
from lib.trace.alias import aliased_run_ids
from lib.trace.session_search import search_clause

log = get_activity_logger("refs")

GROUP_LIMIT = 5
KIND_LIMIT = 20
RECENT_LIMIT = 3
LIMIT_MAX = 50
FILE_HINT = "Pick a project to search its files"

RECENT_KINDS = ("feature", "task")
ID_KINDS = ("feature", "task", "defect")

_KIND_PREFIX = re.compile(r"([A-Za-z]+):(.*)", re.S)
_ID_SHORTCUT = re.compile(r"([FfTtDd])([1-9][0-9]{0,9})")
_SHORTCUT_KINDS = {"f": "feature", "t": "task", "d": "defect"}
# Upper-case only: `s` plus four hex letters is too often a word.
_SESSION_SHORTCUT = re.compile(r"S([0-9A-Fa-f][0-9A-Fa-f-]{3,127})")
_DIGITS = re.compile(r"[0-9]+")


@dataclass(frozen=True)
class Plan:
    """What one `q` asks for, decided before any row is read."""

    kinds: tuple[str, ...]
    text: str = ""
    #: The kind the caller named (`kind=` or a `task:` prefix), echoed back.
    kind: Optional[str] = None
    #: Ranks first in each kind whose key grammar accepts it.
    exact: str = ""
    session_prefix: str = ""
    recent: bool = False


def _named_kind(raw) -> Optional[str]:
    value = str(raw).strip() if raw is not None else ""
    if not value:
        return None
    kind = canonical_kind(value.lower())
    if kind is None:
        raise RefError(f"unknown kind: {value}", "kind")
    return kind


def _id_in(text: str, kind: str) -> str:
    """`310` or `T310` as task 310's id; '' when `text` names no id of
    `kind`."""
    if kind in ID_KINDS and is_valid_key(kind, text):
        return text
    match = _ID_SHORTCUT.fullmatch(text)
    if match and _SHORTCUT_KINDS[match.group(1).lower()] == kind:
        return match.group(2)
    return ""


def _shortcut(text: str, can_read_sessions: bool) -> Optional[Plan]:
    match = _ID_SHORTCUT.fullmatch(text)
    if match:
        return Plan(kinds=(_SHORTCUT_KINDS[match.group(1).lower()],),
                    text=text, exact=match.group(2))
    if _DIGITS.fullmatch(text):
        return Plan(kinds=ID_KINDS, text=text, exact=text)
    match = _SESSION_SHORTCUT.fullmatch(text)
    if match and can_read_sessions:
        return Plan(kinds=("session",), session_prefix=match.group(1))
    if "/" in text:
        return Plan(kinds=("file",), text=text)
    return None


def plan(q: str, kind=None, *, can_read_sessions: bool = False) -> Plan:
    """The query grammar of what follows `@`."""
    named = _named_kind(kind)
    text = (q or "").strip()[:task_query.Q_MAX]
    prefixed = _KIND_PREFIX.fullmatch(text)
    typed = canonical_kind(prefixed.group(1).lower()) if prefixed else None
    # A prefix naming another kind than `kind=` is text, not a second filter.
    if typed and named in (None, typed):
        named, text = typed, prefixed.group(2).strip()
    if named:
        return Plan(kinds=(named,), text=text, kind=named,
                    exact=_id_in(text, named) or text)
    if not text:
        return Plan(kinds=RECENT_KINDS, recent=True)
    return (_shortcut(text, can_read_sessions)
            or Plan(kinds=KINDS, text=text, exact=text))


def _size(chosen: Plan, raw) -> int:
    if chosen.recent:
        return RECENT_LIMIT
    default = KIND_LIMIT if len(chosen.kinds) == 1 else GROUP_LIMIT
    try:
        wanted = int(str(raw).strip())
    except (TypeError, ValueError):
        return default
    return min(max(wanted, 1), LIMIT_MAX)


def _group(kind: str, items: list, total: int, **extra) -> dict:
    return {"kind": kind, "total": int(total), "items": items, **extra}


def _exact_key(kind: str, chosen: Plan) -> str:
    return chosen.exact if is_valid_key(kind, chosen.exact) else ""


def _exact(kind: str, chosen: Plan, size: int, items: list,
           total: int) -> dict:
    """`items` with the exact-key row put first. The text query excluded
    that key, so the row is counted once. Sessions only reach here for a
    caller who may read them."""
    key = _exact_key(kind, chosen)
    head = resolve.items_for(kind, [key], can_read_sessions=True) \
        if key else []
    return _group(kind, (head + items)[:size], total + len(head))


def _patterns(text: str) -> tuple[str, str]:
    """LIKE patterns for a title prefix and a substring, wildcards escaped."""
    substring = task_query.like_pattern(text)
    return substring[1:], substring


def _tiered(column, text: str) -> list:
    """Title prefix, then title substring, then whatever else matched — the
    typed text taken literally even where a kind's own filter lets `%` and
    `_` act as wildcards. No tier at all for an empty query: a NULL title
    would sink a row that is as recent as any."""
    if not text:
        return []
    prefix, substring = _patterns(text)
    return [case((column.like(prefix, escape="\\"), 1),
                 (column.like(substring, escape="\\"), 2), else_=3)]


def _first(condition, project) -> list:
    return [case((condition, 0), else_=1)] if project is not None else []


# ── one search per kind ───────────────────────────────────────

def _features(chosen: Plan, project, size: int) -> dict:
    spec = {**feature_index.NO_FILTERS, "q": chosen.text}
    if chosen.recent and project is not None:
        spec["project_id"] = project.id
    where, binds = feature_index._where(spec)
    key = _exact_key("feature", chosen)
    if key:
        where += (" AND" if where else " WHERE") + " id <> :exact"
        binds["exact"] = int(key)
    order = []
    if chosen.text:
        binds["prefix"], binds["sub"] = _patterns(chosen.text)
        order.append("CASE WHEN title LIKE :prefix ESCAPE '\\' THEN 1"
                     " WHEN title LIKE :sub ESCAPE '\\' THEN 2 ELSE 3 END")
    if project is not None:
        binds["project"] = project.id
        order.append("CASE WHEN project_id = :project THEN 0 ELSE 1 END")
    order += ["activity DESC", "id DESC"]
    rows = resolve.feature_rows(where, binds,
                                order=" ORDER BY " + ", ".join(order),
                                limit=size)
    total = int(rows[0][4]) if rows else 0
    return _exact("feature", chosen, size, resolve.feature_items(rows), total)


def _tasks(chosen: Plan, project, size: int) -> dict:
    args = {"q": chosen.text}
    if chosen.recent and project is not None:
        args["project_id"] = str(project.id)
    filters = task_query.parse_filters(args)
    stmt = task_query.apply_filters(select(AgentTask), filters)
    count = task_query.apply_filters(select(func.count(AgentTask.id)),
                                     filters)
    key = _exact_key("task", chosen)
    if key:
        stmt = stmt.where(AgentTask.id != int(key))
        count = count.where(AgentTask.id != int(key))
    stmt = stmt.order_by(
        *_tiered(AgentTask.title, chosen.text),
        *_first(AgentTask.project_id == getattr(project, "id", None), project),
        AgentTask.updated_at.desc(), AgentTask.id.desc()).limit(size)
    with SessionLocal() as session:
        rows = session.exec(stmt).all()
        total = session.exec(count).one()
    return _exact("task", chosen, size, resolve.task_items(rows), total)


def _defects(chosen: Plan, project, size: int) -> dict:
    base = defect_store.listing_stmt(DefectQuery(q=chosen.text)).order_by(None)
    key = _exact_key("defect", chosen)
    if key:
        base = base.where(Defect.id != int(key))
    stmt = base.add_columns(Feature.project_id).order_by(
        *_tiered(Defect.symptom, chosen.text),
        *_first(Feature.project_id == getattr(project, "id", None), project),
        Defect.updated_at.desc(), Defect.id.desc()).limit(size)
    with SessionLocal() as session:
        rows = session.exec(stmt).all()
        total = session.exec(
            select(func.count()).select_from(base.subquery())).one()
    items = resolve.defect_items([(row[0], row[-1]) for row in rows])
    return _exact("defect", chosen, size, items, total)


def _session_where(session, chosen: Plan, key: str) -> list:
    if chosen.session_prefix:
        # A typed id prefix is a deep link: it reaches a run's own id even
        # where browsing hides it behind its merged conversation.
        where = [SessionModel.trace_id.ilike(f"{chosen.session_prefix}%")]
    else:
        where = [SessionModel.trace_id.notin_(aliased_run_ids(session))]
        if chosen.text:
            where.append(search_clause(chosen.text, "both"))
    if key:
        where.append(SessionModel.trace_id != key)
    return [SessionModel.is_test == 0, *where]


def _sessions(chosen: Plan, project, size: int) -> dict:
    key = "" if chosen.session_prefix else _exact_key("session", chosen)
    in_project = SessionModel.trace_id.in_(
        select(SessionRepo.trace_id).where(
            SessionRepo.repo_id == getattr(project, "id", None)))
    with SessionLocal() as session:
        # The count rides the page's own statement: the prompt predicate
        # scans every prompt span, and a second statement would scan twice.
        rows = session.exec(
            select(SessionModel.trace_id, SessionModel.title,
                   func.count().over())
            .where(*_session_where(session, chosen, key))
            .order_by(*_tiered(SessionModel.title, chosen.text),
                      *_first(in_project, project),
                      SessionModel.last_seen.desc(),
                      SessionModel.trace_id.desc())
            .limit(size)).all()
        items = resolve.session_items(session, [row[:2] for row in rows])
    total = int(rows[0][2]) if rows else 0
    return _exact("session", chosen, size, items, total)


def _leaves_repo(text: str) -> bool:
    return text.startswith(("/", "~")) or ".." in text.split("/")


def _subsequence(needle: str, haystack: str) -> bool:
    rest = iter(haystack)
    return all(char in rest for char in needle)


def _path_tier(folded: str, needle: str) -> Optional[int]:
    if folded == needle:
        return 0
    if folded.rsplit("/", 1)[-1].startswith(needle):
        return 1
    if needle in folded:
        return 2
    return 3 if _subsequence(needle, folded) else None


def _rank_paths(paths: list[str], text: str) -> list[str]:
    """Paths `text` is a subsequence of: the exact path, then basename
    prefix, then substring, then the rest — shorter first in each tier."""
    if not text:
        return sorted(paths)
    needle = text.casefold()
    scored = []
    for path in paths:
        tier = _path_tier(path.casefold(), needle)
        if tier is not None:
            scored.append((tier, len(path), path))
    return [path for _, _, path in sorted(scored)]


def _files(chosen: Plan, project, size: int) -> dict:
    """Tracked files of the selected project's repo, never a session tree:
    a build runs in a worktree, which holds only what git tracks. The query
    is matched against `git ls-files` and never touches the filesystem, so
    no query can name a path outside the repo."""
    if project is None:
        return _group("file", [], 0, hint=FILE_HINT)
    if _leaves_repo(chosen.text):
        return _group("file", [], 0)
    paths = resolve.git_paths(project.path, "ls-files", "-z") or []
    keyed = [(path, key) for path in _rank_paths(paths, chosen.text)
             if (key := encode_file_key(path))]
    items = [resolve.file_item(key, path, project)
             for path, key in keyed[:size]]
    return _group("file", items, len(keyed))


def _memories(chosen: Plan, project, size: int) -> dict:
    base = memory.get_store()._filtered_memory_stmt(
        tier=None, status="active", kind=None, scope=None,
        q=chosen.text or None, include_tests=False).order_by(None)
    key = _exact_key("memory", chosen)
    if key:
        base = base.where(Memory.id != key)
    in_project = Memory.scope == f"repo:{getattr(project, 'name', '')}"
    with MemorySessionLocal() as session:
        rows = session.exec(base.order_by(
            *_tiered(Memory.title, chosen.text), *_first(in_project, project),
            Memory.updated_at.desc(), Memory.id.desc()).limit(size)).all()
        total = session.exec(
            select(func.count()).select_from(base.subquery())).one()
    return _exact("memory", chosen, size, resolve.memory_items(rows), total)


_SEARCHES = {"feature": _features, "task": _tasks, "defect": _defects,
             "session": _sessions, "file": _files, "memory": _memories}


def search(q: str = "", *, kind=None, project_id=None, limit=None,
           can_read_sessions: bool = False) -> dict:
    """`RefSearch`: the groups in fixed kind order, each with its total.

    An empty group is left out, except the File group's hint when no
    project is selected. No Session group is built for a caller who cannot
    read sessions — not an empty one, none.
    """
    chosen = plan(q, kind, can_read_sessions=can_read_sessions)
    project = resolve.require_project(project_id)
    size = _size(chosen, limit)
    groups = []
    for name in KINDS:
        if name not in chosen.kinds:
            continue
        if name == "session" and not can_read_sessions:
            continue
        group = _SEARCHES[name](chosen, project, size)
        if group["items"] or group.get("hint"):
            groups.append(group)
    log.read("refs_searched", groups=len(groups), recent=chosen.recent,
             kind=chosen.kind or "")
    return {"query": (q or "").strip(), "kind": chosen.kind, "groups": groups}
