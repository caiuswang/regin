"""Full-text search across every indexed surface, in one fan-out call.

Every kind reduces to one record shape (`docs.SearchDoc`), and each is
served by whichever backend can actually *address* its results:

    feature  SQL LIKE over `features` title + spec          (global only)
    task     SQL LIKE over `agent_tasks` title + body        (global only)
    defect   SQL LIKE over `defects` symptom + missing check (global only)
    span     SQL over `session_spans`, scoped by the indexed `trace_id`
    pattern  ripgrep over the patterns / skills / wiki markdown trees
    session  SQL — a row must carry `trace_id` + `span_id` to deep-link
    file     ripgrep over the on-disk files an agent touched

The `span` group is the open session's own spans and is answered from the
merged span store rather than the client's model: the trace view loads
shallowly and paginated, so a client-only pass would search a fraction of a
long session and quietly report it as the whole.
"""

from __future__ import annotations

from lib.activity_log import get_activity_logger
from lib.fulltext.docs import (
    ENTITY_KINDS, KIND_FILE, KIND_ORDER, KIND_PATTERN, KIND_SESSION, KIND_SPAN,
    MIN_QUERY_CHARS, Scored, body_window,
)
from lib.fulltext.entity_docs import ENTITY_SEARCHES
from lib.fulltext.file_docs import search_file_docs, search_pattern_docs
from lib.fulltext.session_docs import search_sessions, touched_files
from lib.fulltext.span_docs import search_session_spans

log = get_activity_logger("fulltext")

DEFAULT_LIMIT = 6
SPAN_LIMIT = 14
# R1: a feature / task / defect group in the all-kinds list shows this many
# rows and a "Show all N" row; a Drill asks for the one kind and pages by its
# own `limit`.
ENTITY_FANOUT_CAP = 3

# The two questions the palette can ask. They are not the same query narrowed:
# they decide what `trace_ids` MEANS to each kind, which is why the caller
# declares the scope instead of the fan-out inferring it from whether an id
# happened to be supplied.
SCOPE_SESSION = "session"
SCOPE_GLOBAL = "global"


def _group(kind: str, scored: list[Scored], limit: int, query: str,
           offset: int = 0) -> dict:
    """One page of a kind. `scored` must be the kind's whole ranked set:
    `total` is read off it, and a total taken after a cut is the cut."""
    items = []
    for hit in scored[offset:offset + limit]:
        payload = hit.doc.as_json()
        payload["body"] = body_window(payload["body"], query)
        items.append(payload)
    return {"kind": kind, "total": len(scored), "offset": offset,
            "items": items}


def _span_group(conn, trace_ids, query, limit) -> dict:
    scored = search_session_spans(conn, trace_ids, query, limit * 4)
    return _group(KIND_SPAN, scored, limit, query)


def _file_group(conn, trace_ids, query, limit, offset) -> dict:
    candidates = touched_files(conn, query, trace_ids=trace_ids)
    return _group(KIND_FILE, search_file_docs(candidates, query),
                  limit, query, offset)


def _entity_builders(conn, query, wanted, limit, offset) -> dict:
    cap = limit if len(wanted) == 1 else min(limit, ENTITY_FANOUT_CAP)
    return {
        kind: (lambda run=run, kind=kind: _group(
            kind, run(conn, query), cap, query, offset))
        for kind, run in ENTITY_SEARCHES.items()
    }


def _wanted(kinds, scope) -> set[str]:
    """An explicit `kinds` is honoured as asked. Otherwise the session scope
    leaves the process entities out: they have no session to be scoped to,
    and "this session" never meant "every feature that mentions it"."""
    if kinds:
        return set(kinds)
    if scope == SCOPE_SESSION:
        return set(KIND_ORDER) - set(ENTITY_KINDS)
    return set(KIND_ORDER)


def search(conn, query: str, kinds: list[str] | None = None,
           trace_ids: list[str] | None = None,
           limit: int = DEFAULT_LIMIT,
           scope: str = SCOPE_SESSION,
           offset: int = 0) -> dict:
    """Run `query` across `kinds`, returning the wire shape the palette reads.

    Each group is independent: one kind raising leaves the others usable, so
    a failing corpus degrades to a retry affordance on its own header rather
    than failing search as a whole.

    `trace_ids` is the open session's alias group, and `scope` says how each
    kind should read it: under `session` it is the corpus `span` and `file` are
    drawn from, under `global` those two look past it. `session` — the kind
    that answers "which OTHER sessions match" — always treats it as the set to
    exclude, because a session is never one of its own others.

    `offset` pages every kind but `span`, whose in-session group is a fixed
    window rather than a list the reader drills into.
    """
    query = (query or "").strip()
    if len(query) < MIN_QUERY_CHARS:
        return {"q": query, "groups": [], "truncated": False}

    wanted = _wanted(kinds, scope)
    trace_ids = trace_ids or []
    corpus_ids = trace_ids if scope == SCOPE_SESSION else []
    builders = {
        KIND_SPAN: lambda: _span_group(conn, corpus_ids, query, SPAN_LIMIT),
        KIND_PATTERN: lambda: _group(
            KIND_PATTERN, search_pattern_docs(query), limit, query, offset),
        KIND_SESSION: lambda: _group(
            KIND_SESSION,
            search_sessions(conn, query, exclude_traces=trace_ids),
            limit, query, offset),
        KIND_FILE: lambda: _file_group(conn, corpus_ids, query, limit, offset),
        **_entity_builders(conn, query, wanted, limit, offset),
    }

    groups = []
    failed = []
    for kind in KIND_ORDER:
        if kind not in wanted:
            continue
        try:
            groups.append(builders[kind]())
        except Exception:
            log.error("search_group_failed", kind=kind, exc_info=True)
            failed.append(kind)
    return {"q": query, "groups": groups, "failed": failed, "truncated": False}
