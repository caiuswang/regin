"""The `session` kind: other sessions, as a digest of their matching turns.

Stays on SQL rather than ripgrep because a row has to be *addressable* — it
deep-links to `/trace/sessions/<trace_id>?span=<span_id>`, and both ids live
only in the span store. The raw transcripts a grep could scan carry neither,
and they still hold placeholder rows the serve-time merge retires.
"""

from __future__ import annotations

import json

from lib.fulltext.docs import (
    KIND_SESSION, LIKE_ESCAPE, SearchDoc, Scored, body_window, like_patterns,
    rank, score_doc,
)
from lib.fulltext.span_docs import row_to_doc

# Hard ceiling on the rows one cross-session query may inspect. The scan has
# no index to ride (the query is a substring of a JSON blob), so the bound is
# what keeps a rare term from walking the whole table.
MAX_SCANNED_ROWS = 600
# Matching spans quoted into one session's digest.
DIGEST_SPANS = 6


def _digest(docs: list[SearchDoc], query: str) -> str:
    """One session's matching spans as terminal-style lines — never the whole
    session, which is the difference between a preview and a download."""
    lines: list[str] = []
    for doc in docs[:DIGEST_SPANS]:
        header = f"{doc.crumb or doc.title}"
        lines.append(header)
        window = body_window(doc.body, query, lines=6)
        for row in window.split("\n"):
            if row.strip():
                lines.append(f"  {row}")
        lines.append("")
    return "\n".join(lines).strip()


def _matching_rows(conn, query: str, exclude_traces: list[str] | None):
    patterns = like_patterns(query)
    likes = " OR ".join([f"attributes LIKE ? ESCAPE '{LIKE_ESCAPE}'"] * len(patterns))
    params: list = list(patterns)
    where = f"({likes})"
    excluded = [t for t in (exclude_traces or []) if t]
    if excluded:
        placeholders = ",".join("?" * len(excluded))
        where += f" AND trace_id NOT IN ({placeholders})"
        params.extend(excluded)
    sql = (
        "SELECT trace_id, span_id, name, attributes, duration_ms, agent_id "
        f"FROM session_spans WHERE {where} ORDER BY id DESC LIMIT ?"
    )
    params.append(MAX_SCANNED_ROWS)
    return conn.execute(sql, params)


def _titles_for(conn, trace_ids: list[str]) -> dict[str, tuple[str, str, int]]:
    if not trace_ids:
        return {}
    placeholders = ",".join("?" * len(trace_ids))
    sql = (
        f"SELECT trace_id, title, last_seen, span_count FROM sessions "
        f"WHERE trace_id IN ({placeholders})"
    )
    return {
        row[0]: (row[1] or "", row[2] or "", row[3] or 0)
        for row in conn.execute(sql, trace_ids)
    }


def search_sessions(conn, query: str, limit: int | None = None,
                    exclude_traces: list[str] | None = None) -> list[Scored]:
    """Rank other sessions by how well their spans match `query`.

    `exclude_traces` is the open session's whole alias group, not one id: a
    resumed or SDK-launched run carries spans under two trace ids, and dropping
    only the canonical one left the session you are already in ranking as
    another session under its alias.
    """
    if not query:
        return []
    per_session: dict[str, list[SearchDoc]] = {}
    hits: dict[str, int] = {}
    first_span: dict[str, str] = {}
    for row in _matching_rows(conn, query, exclude_traces):
        trace_id = row[0]
        doc = row_to_doc(row[1:])
        scored = score_doc(doc, query)
        if not scored:
            continue
        per_session.setdefault(trace_id, []).append(doc)
        hits[trace_id] = hits.get(trace_id, 0) + max(scored.hits, 1)
        first_span.setdefault(trace_id, doc.id)

    meta = _titles_for(conn, list(per_session))
    scored_out: list[Scored] = []
    # Rows arrived newest-first, so enumeration order IS recency — the
    # tie-break the spec asks for on this kind.
    for order, (trace_id, docs) in enumerate(per_session.items()):
        title, last_seen, span_count = meta.get(trace_id, ("", "", 0))
        day = (last_seen or "").split("T", 1)[0]
        doc = SearchDoc(
            kind=KIND_SESSION,
            id=trace_id,
            title=title or trace_id[:12],
            sub=" · ".join(filter(None, [f"{len(docs)} matching spans", day])),
            tag=" · ".join(filter(None, [f"{span_count} spans", day])),
            crumb=f"session · {trace_id[:12]}",
            body=_digest(docs, query),
            mono=True,
            href=f"/trace/sessions/{trace_id}?span={first_span[trace_id]}",
        )
        hit = score_doc(doc, query, order)
        # A session whose digest holds no visible match is dropped rather than
        # shown with a 0-match pill: the SQL pre-filter matches the raw JSON,
        # which can hit an attribute the readable body never renders, and a row
        # the reader cannot see the reason for is worse than no row.
        if hit:
            scored_out.append(hit)
    return rank(scored_out)[:limit]


EDIT_SPAN_NAMES = "('tool.Edit','tool.Write','tool.MultiEdit','tool.NotebookEdit')"


def _touched_sql(query: str, trace_ids: list[str]) -> tuple[str, list]:
    """Candidate edit spans — either the session's, or the corpus's.

    Given `trace_ids`, the candidates are every file those traces touched:
    `trace_id` is indexed, so the group can be COMPLETE for the trace on
    screen, and the on-disk grep that follows does the actual matching.
    Without them there is no index to ride, so the diff-text pre-filter is the
    only affordable narrowing and the group is best-effort.

    They are alternatives, never a union. Unioning them let a file touched
    only in ANOTHER session rank inside a group the reader had scoped to this
    one — and its row deep-links to that other trace, so opening it navigated
    out of the very session the scope was pinning.
    """
    head = ("SELECT trace_id, span_id, attributes FROM session_spans "
            f"WHERE name IN {EDIT_SPAN_NAMES} AND ")
    tail = " ORDER BY id DESC LIMIT ?"
    if trace_ids:
        placeholders = ",".join("?" * len(trace_ids))
        return f"{head}trace_id IN ({placeholders}){tail}", list(trace_ids)
    patterns = like_patterns(query)
    likes = " OR ".join([f"attributes LIKE ? ESCAPE '{LIKE_ESCAPE}'"] * len(patterns))
    return f"{head}({likes}){tail}", list(patterns)


def touched_files(conn, query: str, limit: int | None = None,
                  trace_ids: list[str] | None = None) -> list[dict]:
    """Files an agent edited or wrote, most recent first — the candidate set
    the `file` kind then greps on disk."""
    sql, params = _touched_sql(query, trace_ids or [])
    seen: set[str] = set()
    out: list[dict] = []
    for trace_id, span_id, raw in conn.execute(sql, [*params, MAX_SCANNED_ROWS]):
        try:
            attrs = json.loads(raw) if raw else {}
        except (ValueError, TypeError):
            continue
        path = attrs.get("file_path")
        if not isinstance(path, str) or path in seen:
            continue
        seen.add(path)
        out.append({
            "path": path, "span_id": span_id, "trace_id": trace_id,
            "additions": attrs.get("additions"), "deletions": attrs.get("deletions"),
        })
        if limit is not None and len(out) >= limit * 4:
            break
    return out
