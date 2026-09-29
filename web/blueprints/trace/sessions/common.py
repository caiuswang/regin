"""Trace-id alias groups, span counts and subagent scoping shared by the session endpoints."""

from __future__ import annotations

import json

from lib.orm import SessionLocal
from lib.orm.models import Session as SessionModel, SessionSpan
from lib.trace.alias import ORIGIN_KEY, trace_group


_VAGUE_END_REASONS = frozenset({None, '', 'other', 'unknown'})


def _group_ended_reason(group: list[str], stored):
    """Why the session ended, taking the concrete answer from either writer.

    The child's hook can only report the generic `other` for a run the SDK
    ended deliberately — only the runner knows it was an idle timeout. The span
    merge already prefers the concrete reason (`merge._fill_vague_attrs`), so
    without this the header and the session list contradict the `session.end`
    span in the very same response.

    Takes the caller's already-resolved group: a live session's reason is NULL,
    i.e. vague, so the early return below does not fire for it and re-resolving
    here would add a connection to every poll of every session.
    """
    if stored not in _VAGUE_END_REASONS or len(group) < 2:
        return stored
    from sqlmodel import select as _sel
    with SessionLocal() as session:
        for reason in session.exec(
                _sel(SessionModel.ended_reason)
                .where(SessionModel.trace_id.in_(group))).all():
            if reason not in _VAGUE_END_REASONS:
                return reason
    return stored


def _total_span_count(session, trace_id: str, loaded: int = 0) -> int:
    """Denominator for the "N of M loaded" indicator.

    Excludes PENDING placeholders (the append-only store keeps them).

    For an alias group the raw row count is the WRONG total: it sums both
    writers, so it double-counts every event they both recorded and would read
    "134 of 236" against a merged view. The merged count is what
    `sessions.span_count` holds for the canonical row, so use that — the same
    number the session list shows, which is also what makes the two agree.

    A subagent trace has no rows and no `sessions` row under its own id, so it
    is counted over the agent's scope instead — the raw count answers 0 there,
    and "N of 0 loaded" on a 569-span child is worse than no indicator at all.
    """
    from sqlalchemy import func as _fn
    from sqlmodel import select as _sel

    subagent_total = _subagent_span_count(trace_id)
    if subagent_total is not None:
        return subagent_total

    def raw_for(tid):
        return session.exec(
            _sel(_fn.count())
            .select_from(SessionSpan)
            .where(SessionSpan.trace_id == tid)
            .where(SessionSpan.status_code != 'PENDING')
        ).one()

    group = _alias_group_ids(trace_id)
    if len(group) < 2:
        return raw_for(trace_id)
    # Summing the group would double-count every event both writers recorded.
    # The exact answer is the merged count in `sessions.span_count`, but that
    # is only refreshed when the session ends — so for the whole life of a live
    # run it is one writer's stale figure and can fall BELOW what is on screen.
    # Merging per poll is far too slow, so take the best lower bounds we have:
    # the merge keeps every row of the larger writer plus the smaller writer's
    # unpaired ones, so it is never less than either raw count, and never less
    # than what the caller already loaded.
    stored = session.exec(
        _sel(SessionModel.span_count)
        .where(SessionModel.trace_id == group[0])
    ).first()
    return max(stored or 0, loaded, *(raw_for(t) for t in group))


def _expand_alias_groups(trace_ids: list[str]) -> list[str]:
    """Every trace id these sessions are actually made of, de-duplicated."""
    out: list[str] = []
    for trace_id in trace_ids:
        for member in _alias_group_ids(trace_id):
            if member not in out:
                out.append(member)
    return out


def _map_row_to_span(row, canonical: str) -> dict:
    """One `/map` row as a projection span dict.

    Re-keys to the canonical id and tags the writer, the same contract as
    `_fetch_spans_for_group` — without the tag `merge_spans` sees a single
    origin and reconciles nothing, leaving this view double-rendered where the
    other readers are not.
    """
    d = dict(row)
    try:
        d['attributes'] = json.loads(d['attributes']) if d.get('attributes') else {}
    except (TypeError, ValueError):
        d['attributes'] = {}
    d[ORIGIN_KEY] = 'hook' if d['trace_id'] == canonical else 'sdk'
    d['trace_id'] = canonical
    return d


def _alias_group_ids(trace_id: str) -> list[str]:
    """Every trace id whose spans belong to this session — itself for an
    ordinary one. Opens its own connection so the SQLModel-side readers can
    scope to the group without threading one through."""
    from lib.orm.engine import get_connection
    conn = get_connection()
    try:
        return trace_group(conn, trace_id)
    finally:
        conn.close()


def _stored_group_ids(trace_id: str) -> list[str]:
    """The trace ids this session's rows are actually STORED under — the
    parent's group for a subagent trace, which owns no row of its own.

    What every session-level read has to key on: the `sessions` row behind the
    header, the workflow labels, and the read-side memo, whose entries a
    subagent trace would otherwise file under an id no writer ever
    invalidates — freezing a live child on its first answer."""
    from lib.orm.engine import get_connection
    from lib.trace.subagent_trace import stored_group
    conn = get_connection()
    try:
        return stored_group(conn, trace_id)
    finally:
        conn.close()


def _subagent_scope(trace_id: str):
    """`subagent_trace.AgentScope` for an `sa-…` id, else None.

    The prefix test comes first so the per-span content reads — one request per
    body the trace view opens — don't each pay for a connection to be told the
    id was ordinary."""
    from lib.orm.engine import get_connection
    from lib.trace.subagent_trace import agent_span_scope, is_subagent_trace_id
    if not is_subagent_trace_id(trace_id):
        return None
    conn = get_connection()
    try:
        return agent_span_scope(conn, trace_id)
    finally:
        conn.close()


def _subagent_span_count(trace_id: str) -> int | None:
    """The agent's own span total for an `sa-…` id, else None."""
    from lib.orm.engine import get_connection
    from lib.trace.subagent_trace import subagent_span_count
    conn = get_connection()
    try:
        return subagent_span_count(conn, trace_id)
    finally:
        conn.close()


def _scoped_span_query(stmt, trace_id: str, scope):
    """Narrow a span query to the rows `trace_id` serves.

    A subagent trace's rows are stored under its parent's id, so the group
    predicate alone would answer with the whole parent conversation; the agent
    scope is what keeps one child's read to one child.
    """
    if scope is None:
        return stmt.where(SessionSpan.trace_id.in_(_alias_group_ids(trace_id)))
    return (stmt.where(SessionSpan.trace_id.in_(scope.trace_ids))
                .where(SessionSpan.agent_id.in_(scope.agent_ids)))
