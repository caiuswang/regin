"""Per-span endpoints: content, rewind, workflow runs and ancestors."""

from __future__ import annotations

import json

from flask import jsonify
from lib.orm import SessionLocal
from lib.orm.models import SessionSpan
from lib.trace.pending_spans import AGENT_ID_SQL
from web.blueprints.trace import trace_bp
from web.blueprints.trace.sessions.common import (
    _alias_group_ids, _map_row_to_span, _scoped_span_query, _stored_group_ids,
    _subagent_scope,
)
from web.blueprints.trace.sessions.map_view import _structural_map_spans


def _absorbed_prompt_attrs(trace_id: str, span_id: str, raw: dict) -> dict:
    """`span_id`'s prompt attributes as the serve-time merge renders them.

    A slash-command prompt stores only the bare `/command` echo on its resolved
    anchor; the text the user actually submitted lives on the PENDING
    `promptlive-` placeholder, and `_absorb_slash_command_expansions` moves it
    onto the anchor at read time. This endpoint reads ONE raw row, so without
    replaying that absorb it answers with the 22-character echo — and the
    client's content cache overlays what it returns ON TOP of the `/map`
    projection (`useSpanContentCache.allSpans`), replacing the full prompt the
    card was already rendering with the command name alone.

    Prompt rows alone are enough to replay it: the absorb pairs a prompt with a
    prompt, so the window is one small indexed read rather than the session.
    """
    from lib.trace.merge import _absorb_slash_command_expansions
    from sqlmodel import select as _select

    scope = _subagent_scope(trace_id)
    canonical = trace_id if scope is not None else _alias_group_ids(trace_id)[0]
    with SessionLocal() as session:
        rows = session.execute(
            _scoped_span_query(
                _select(
                    SessionSpan.id, SessionSpan.trace_id, SessionSpan.span_id,
                    SessionSpan.name, SessionSpan.status_code,
                    SessionSpan.attributes,
                ),
                trace_id, scope)
            .where(SessionSpan.name == 'prompt')
            .order_by(SessionSpan.id.asc())
        ).mappings().all()
    prompts = [_map_row_to_span(r, canonical) for r in rows]
    for s in _absorb_slash_command_expansions(prompts):
        if s.get('span_id') == span_id:
            return s.get('attributes') or raw
    # Unreachable for an anchor; a placeholder the absorb consumed keeps its
    # own text, which is the same text it just handed to the anchor.
    return raw


@trace_bp.route('/api/sessions/<trace_id>/spans/<span_id>/content')
def api_span_content(trace_id, span_id):
    """Return the content (attributes) for a single span.

    A subagent trace addresses rows stored under its parent, so the served id
    is not a `trace_id` to match on; every body the child's cards ask for would
    404 on the id the same response's spans were re-keyed to.
    """
    from sqlmodel import select as _select
    scope = _subagent_scope(trace_id)
    stmt = _select(SessionSpan.attributes, SessionSpan.name)
    if scope is not None:
        stmt = _scoped_span_query(stmt, trace_id, scope)
    else:
        stmt = stmt.where(SessionSpan.trace_id == trace_id)
    with SessionLocal() as session:
        row = session.execute(
            stmt.where(SessionSpan.span_id == span_id)
        ).first()
    if row is None:
        return jsonify({'error': 'span not found'}), 404
    attributes = json.loads(row[0] or '{}')
    if row[1] == 'prompt':
        attributes = _absorbed_prompt_attrs(trace_id, span_id, attributes)
    return jsonify({
        'trace_id': trace_id,
        'span_id': span_id,
        'attributes': attributes,
    })


@trace_bp.route('/api/sessions/<trace_id>/spans/<span_id>/rewind')
def api_span_rewind(trace_id, span_id):
    """Lazy before/after content for a `/rewind` marker's rolled-back files.

    The map carries only `rolled_back_count`; the file list + `@vN` refs live
    in the marker's full attributes, and the actual file copies live in
    `~/.claude/file-history/<session>/`. This route joins them on demand so a
    code-rollback diff never bloats the map payload. `session_id == trace_id`
    (regin keys sessions by the Claude Code session id, which also names the
    file-history dir)."""
    from sqlmodel import select as _select
    from lib.trace.file_history import diff_versions

    with SessionLocal() as session:
        attrs_json = session.exec(
            _select(SessionSpan.attributes)
            .where(SessionSpan.trace_id == trace_id)
            .where(SessionSpan.span_id == span_id)
        ).first()
    if attrs_json is None:
        return jsonify({'error': 'span not found'}), 404
    attrs = json.loads(attrs_json or '{}')
    if attrs.get('kind') != 'rewind':
        return jsonify({'error': 'not a rewind marker'}), 400
    files = [
        diff_versions(trace_id, entry)
        for entry in (attrs.get('rolled_back_files') or [])
    ]
    return jsonify({
        'trace_id': trace_id,
        'span_id': span_id,
        'files': files,
    })


def _workflow_run_summary(session, run_id: str) -> dict:
    """Read-time rollup for a captured workflow run (its trace_id == run_id).

    Returns the counts the parent session's collapsed summary node shows:
    agent/phase totals, the run's status, and summed agent output tokens.
    Computed from spans on every call — nothing is denormalized, so this
    stays clear of the schema.sql/Alembic drift trap.
    """
    from sqlmodel import select as _select
    from sqlalchemy import func as _func

    def _count(name: str) -> int:
        return session.exec(
            _select(_func.count())
            .select_from(SessionSpan)
            .where(SessionSpan.trace_id == run_id)
            .where(SessionSpan.name == name)
        ).one() or 0

    tokens = session.exec(
        _select(_func.sum(
            _func.json_extract(SessionSpan.attributes, '$.tokens')))
        .where(SessionSpan.trace_id == run_id)
        .where(SessionSpan.name == 'subagent.start')
    ).one()
    status = session.exec(
        _select(_func.json_extract(SessionSpan.attributes, '$.workflow_status'))
        .where(SessionSpan.trace_id == run_id)
        .where(SessionSpan.name == 'session.start')
    ).first()
    return {
        'agent_count': _count('subagent.start'),
        'phase_count': _count('workflow.phase'),
        'tokens': int(tokens) if tokens is not None else None,
        'status': status or None,
    }


@trace_bp.route('/api/sessions/<trace_id>/workflow-runs')
def api_session_workflow_runs(trace_id):
    """Dynamic-workflow runs launched from this session, in call order.

    Each ``tool.Workflow`` call this session made carries a
    ``workflow_run_id`` (+ ``workflow_name``), stamped at ingest by matching
    the persisted script to the captured run. Surfaced so the session header
    can offer a ``workflows N`` pivot chip (mirroring the ``plans`` / ``tasks``
    chips) that jumps straight to each run's captured trace. Empty for
    sessions that launched no workflows (and for a run's own session, which
    has no ``tool.Workflow`` spans).
    """
    from sqlmodel import select as _select

    with SessionLocal() as session:
        rows = session.exec(
            _select(SessionSpan.attributes)
            .where(SessionSpan.trace_id == trace_id)
            .where(SessionSpan.name == 'tool.Workflow')
            .order_by(SessionSpan.start_time)
        ).all()

        runs: list[dict] = []
        seen: set[str] = set()
        for attrs_json in rows:
            attrs = json.loads(attrs_json or '{}')
            run_id = attrs.get('workflow_run_id')
            if not run_id or run_id in seen:
                continue
            seen.add(run_id)
            # Counts are computed on read from the run's captured spans (its
            # own trace_id == run_id) — no denormalized column, so no
            # schema.sql/Alembic drift. Lets the parent session render a rich
            # collapsed summary node (agents · phases · status · tokens)
            # without opening the run.
            runs.append({
                'run_id': run_id,
                'name': attrs.get('workflow_name'),
                **_workflow_run_summary(session, run_id),
            })
    return jsonify({'trace_id': trace_id, 'items': runs})


def _delegated_child_trace(trace_id: str, span_id: str) -> str | None:
    """The trace that serves a span this session no longer does, or None when
    the span is not a subagent's.

    Resolved off the stored row rather than the rendered map, which is exactly
    where the span has stopped being: a `?span=` deep link and a search hit
    both name a row that outlived the view it was found in.

    The agent id is re-read through the marker that names the launching call,
    because an SDK run tags its half of an agent's spans with that call's id
    (`projection._normalize_agent_ids`) and the child is addressed by the
    roster id.
    """
    from lib.orm.engine import get_connection
    from lib.trace.alias import sql_in
    from lib.trace.pending_spans import AGENT_ID_SQL
    from lib.trace.subagent_trace import subagent_trace_id

    placeholders, params = sql_in(_stored_group_ids(trace_id))
    conn = get_connection()
    try:
        row = conn.execute(
            f"SELECT {AGENT_ID_SQL} AS agent_id FROM session_spans "
            f"WHERE trace_id IN {placeholders} AND span_id = ? LIMIT 1",
            [*params, span_id]).fetchone()
        agent_id = row['agent_id'] if row else None
        if not agent_id:
            return None
        named = conn.execute(
            f"SELECT {AGENT_ID_SQL} AS agent_id FROM session_spans "
            f"WHERE trace_id IN {placeholders} "
            "AND json_extract(attributes, '$.launch_tool_use_id') = ? LIMIT 1",
            [*params, agent_id]).fetchone()
    finally:
        conn.close()
    return subagent_trace_id(
        (named['agent_id'] if named else None) or agent_id)


@trace_bp.route('/api/sessions/<trace_id>/spans/<span_id>/ancestors')
def api_span_ancestors(trace_id, span_id):
    """Walk parent_id up to the projected root and return the chain.

    The DB-level `parent_id` is NOT authoritative for tree shape, and
    neither is a per-row mirror of any single graft rule. The store is
    append-only, so a live `promptlive-` placeholder and its promoted
    `prompt-<uuid>` anchor coexist; the serve-time `merge_spans` drops
    the placeholder, then grafts NULL-parent orphans (rule.check, tool
    attachments) under the surviving anchor and reparents subagent spans
    by `agent_id`. Resolving the chain off raw rows lands on the retired
    placeholder (or skips the subagent nesting) — a root the frontend no
    longer renders, so the deep-link loads the wrong subtree and the jump
    silently fails.

    So compute the chain from the SAME merged span set the `/map` and
    `/children` endpoints render (`_structural_map_spans`): walk its
    post-merge `parent_id` from the target up to the root. The returned
    root is guaranteed to exist in the rendered tree, and a `children?deep`
    fetch of it surfaces the target.

    Cycle-guarded against a self-referential parent_id.
    """
    by_id = {s['span_id']: s for s in _structural_map_spans(trace_id)}
    if span_id not in by_id:
        child = _delegated_child_trace(trace_id, span_id)
        if child is None:
            return jsonify({'error': 'span not found'}), 404
        # 200 with no chain rather than 404: there is nothing to reveal in
        # this trace, but the span is reachable and the client is told where.
        return jsonify({
            'trace_id': trace_id,
            'span_id': span_id,
            'child_trace_id': child,
            'root_span_id': None,
            'chain': [],
        })

    chain: list[str] = []
    current_id = span_id
    seen: set[str] = set()
    while current_id and current_id not in seen:
        seen.add(current_id)
        span = by_id.get(current_id)
        if not span:
            # Dangling parent (merge heals these, but stay defensive). Stop.
            break
        chain.append(current_id)
        current_id = span.get('parent_id')

    chain.reverse()  # root first
    return jsonify({
        'trace_id': trace_id,
        'span_id': span_id,
        'root_span_id': chain[0],
        'chain': chain,
    })
