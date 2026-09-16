"""Full-text search across every indexed surface, in one fan-out call.

Four document kinds reduce to one record shape (`docs.SearchDoc`), and each
is served by whichever backend can actually *address* its results:

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
    KIND_FILE, KIND_ORDER, KIND_PATTERN, KIND_SESSION, KIND_SPAN,
    MIN_QUERY_CHARS, Scored, body_window,
)
from lib.fulltext.file_docs import search_file_docs, search_pattern_docs
from lib.fulltext.session_docs import search_sessions, touched_files
from lib.fulltext.span_docs import search_session_spans

log = get_activity_logger("fulltext")

DEFAULT_LIMIT = 6
SPAN_LIMIT = 14

# The two questions the palette can ask. They are not the same query narrowed:
# they decide what `trace_ids` MEANS to each kind, which is why the caller
# declares the scope instead of the fan-out inferring it from whether an id
# happened to be supplied.
SCOPE_SESSION = "session"
SCOPE_GLOBAL = "global"


def _group(kind: str, scored: list[Scored], limit: int, query: str) -> dict:
    items = []
    for hit in scored[:limit]:
        payload = hit.doc.as_json()
        payload["body"] = body_window(payload["body"], query)
        items.append(payload)
    return {"kind": kind, "total": len(scored), "items": items}


def _span_group(conn, trace_ids, query, limit) -> dict:
    scored = search_session_spans(conn, trace_ids, query, limit * 4)
    return _group(KIND_SPAN, scored, limit, query)


def _file_group(conn, trace_ids, query, limit) -> dict:
    candidates = touched_files(conn, query, limit, trace_ids)
    return _group(KIND_FILE, search_file_docs(candidates, query, limit * 2),
                  limit, query)


def search(conn, query: str, kinds: list[str] | None = None,
           trace_ids: list[str] | None = None,
           limit: int = DEFAULT_LIMIT,
           scope: str = SCOPE_SESSION) -> dict:
    """Run `query` across `kinds`, returning the wire shape the palette reads.

    Each group is independent: one kind raising leaves the others usable, so
    a failing corpus degrades to a retry affordance on its own header rather
    than failing search as a whole.

    `trace_ids` is the open session's alias group, and `scope` says how each
    kind should read it: under `session` it is the corpus `span` and `file` are
    drawn from, under `global` those two look past it. `session` — the kind
    that answers "which OTHER sessions match" — always treats it as the set to
    exclude, because a session is never one of its own others.
    """
    query = (query or "").strip()
    if len(query) < MIN_QUERY_CHARS:
        return {"q": query, "groups": [], "truncated": False}

    wanted = set(kinds) if kinds else set(KIND_ORDER)
    trace_ids = trace_ids or []
    corpus_ids = trace_ids if scope == SCOPE_SESSION else []
    builders = {
        KIND_SPAN: lambda: _span_group(conn, corpus_ids, query, SPAN_LIMIT),
        KIND_PATTERN: lambda: _group(
            KIND_PATTERN, search_pattern_docs(query, limit * 2), limit, query),
        KIND_SESSION: lambda: _group(
            KIND_SESSION,
            search_sessions(conn, query, limit * 2, trace_ids),
            limit, query),
        KIND_FILE: lambda: _file_group(conn, corpus_ids, query, limit),
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
