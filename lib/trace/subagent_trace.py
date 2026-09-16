"""When one stored trace is really several sessions.

A Claude Code subagent has its own prompt, its own tools and its own ending,
but none of that is stored apart: every span it emits is written into the
PARENT's trace, tagged `session_spans.agent_id`. There is no row an operator
can point a URL at, so a subagent is only ever visible as a subtree of the run
that launched it.

This module gives it an address — `sa-<agent_id>` — resolved on the way IN, in
the same spirit as `lib/trace/alias.py`: reconcile at serve time, leave the
append-only rows alone. Nothing here writes, nothing moves, and the parent
trace keeps rendering exactly as it does today; the split exists only for the
duration of a read.

The direction is the mirror of `alias.py`'s. There, two stored trace ids
resolve to one logical session; here, one stored trace resolves to a parent
plus N logical children.
"""

from __future__ import annotations

from typing import NamedTuple

# Namespaces a subagent's logical trace id. A stored `trace_id` never carries
# it, so an id without the prefix takes the ordinary path untouched and every
# hot reader can skip the agent lookup with a string compare.
SUBAGENT_TRACE_PREFIX = 'sa-'


def subagent_trace_id(agent_id: str) -> str:
    """The logical trace id a subagent is addressable as."""
    return f'{SUBAGENT_TRACE_PREFIX}{agent_id}'


def is_subagent_trace_id(trace_id) -> bool:
    """True for an id naming a subagent's logical trace. Safe on None and on
    non-strings: callers hold ids straight off a request path or a DB row."""
    return isinstance(trace_id, str) and trace_id.startswith(SUBAGENT_TRACE_PREFIX)


def resolve_subagent_trace(conn, trace_id) -> tuple[str, str] | None:
    """`(parent_trace_id, agent_id)` for an `sa-…` id, else None.

    None is the "this is an ordinary trace" answer as well as the "no such
    agent" one: both mean the caller should read `trace_id` as stored.

    `ORDER BY` because nothing constrains an agent to a single trace; an
    unordered `LIMIT 1` could resolve the same id to a different parent
    between calls, and a trace that changes shape when you refresh is worse
    than one that is consistently wrong.
    """
    if not is_subagent_trace_id(trace_id):
        return None
    agent_id = trace_id[len(SUBAGENT_TRACE_PREFIX):]
    if not agent_id:
        return None
    try:
        row = conn.execute("""
            SELECT trace_id FROM session_spans
            WHERE agent_id = ?
            ORDER BY id
            LIMIT 1
        """, (agent_id,)).fetchone()
    except Exception:  # noqa: BLE001 — a DB predating the promoted column has
        # no such field, and a trace read must degrade to "not a subagent"
        # rather than 500.
        return None
    if row is None:
        return None
    return row["trace_id"], agent_id


class AgentScope(NamedTuple):
    """Where one subagent's rows are STORED, as a clause over `session_spans`.

    `trace_ids` is the PARENT's alias group and `agent_ids` the tags the two
    writers know the agent by, so `clause` is the single predicate every
    reader — the windowed fetch, the anchor pager, the count, the summary —
    has to substitute for its `trace_id IN (…)` to answer for the child.
    """
    agent_id: str
    trace_ids: list[str]
    agent_ids: list[str]
    clause: str
    params: list


def agent_span_scope(conn, trace_id) -> AgentScope | None:
    """The scope of the subagent `trace_id` names, or None for an ordinary id.

    Accepts a lone id or a group and reads the first, so a caller holding
    either shape can ask without unwrapping.
    """
    from lib.trace.alias import sql_in, trace_group
    from lib.trace.projection import _launch_tool_use_id

    ids = [trace_id] if isinstance(trace_id, str) else list(trace_id or [])
    resolved = resolve_subagent_trace(conn, ids[0]) if ids else None
    if resolved is None:
        return None
    parent, agent_id = resolved
    group = trace_group(conn, parent)
    launch = _launch_tool_use_id(conn, group, agent_id)
    agent_ids = [agent_id, launch] if launch else [agent_id]
    trace_ph, trace_params = sql_in(group)
    agent_ph, agent_params = sql_in(agent_ids)
    return AgentScope(
        agent_id, group, agent_ids,
        f"trace_id IN {trace_ph} AND agent_id IN {agent_ph}",
        [*trace_params, *agent_params])


def delegated_agent_ids(conn, scope: AgentScope) -> list[str]:
    """The agents THIS agent launched, as the tags their rows are stored under.

    Read off the `parent_agent_id` each child's `subagent.start` carries — the
    same edge `claude_subagents._resolve_parent_agents` writes and
    `projection._raw_parent_agent_edges` reads. A nested agent is delegated
    work like any other: its child's marker has to reach the parent's read to
    stand as a card there, and its own rows are tagged with ITS id, so the
    agent's own scope never selects them.

    Self-references are dropped, so an agent naming itself as its own parent
    cannot pull its own markers back in through this door.
    """
    from lib.trace.alias import sql_in

    trace_ph, trace_params = sql_in(scope.trace_ids)
    agent_ph, agent_params = sql_in(scope.agent_ids)
    rows = conn.execute(f"""
        SELECT DISTINCT agent_id,
               json_extract(attributes, '$.launch_tool_use_id') AS launch
        FROM session_spans
        WHERE trace_id IN {trace_ph} AND name = 'subagent.start'
          AND json_extract(attributes, '$.parent_agent_id') IN {agent_ph}
    """, [*trace_params, *agent_params]).fetchall()
    own = set(scope.agent_ids)
    ids = []
    for row in rows:
        for value in (row['agent_id'], row['launch']):
            if value and value not in own and value not in ids:
                ids.append(value)
    return ids


def span_scope(conn, trace_id) -> tuple[str, list]:
    """`(clause, params)` selecting the rows a served id addresses.

    An ordinary id yields the `trace_id IN (…)` every reader already wrote, so
    a call site can switch to this unconditionally and keep its behaviour.
    """
    scope = agent_span_scope(conn, trace_id)
    if scope is not None:
        return scope.clause, list(scope.params)
    from lib.trace.alias import sql_in
    placeholders, params = sql_in(trace_id)
    return f"trace_id IN {placeholders}", params


def stored_group(conn, trace_id) -> list[str]:
    """The stored trace ids behind a served id — the PARENT's alias group for a
    subagent, its own group otherwise.

    What the session-level joins (the `sessions` row, the workflow-label
    lookup, the read-side memo's invalidation key) have to key on: a child has
    no row and no rows of its own, so keying them on `sa-…` answers with
    silence and caches that silence under an id no writer ever touches.
    """
    scope = agent_span_scope(conn, trace_id)
    if scope is not None:
        return list(scope.trace_ids)
    from lib.trace.alias import trace_group
    return trace_group(conn, trace_id)


def _span_count(conn, scope: AgentScope) -> int:
    """The agent's stored rows, PENDING placeholders excluded — the denominator
    `_total_span_count` gives an ordinary session, asked of one agent."""
    row = conn.execute(
        f"SELECT COUNT(*) AS n FROM session_spans "
        f"WHERE {scope.clause} AND status_code != 'PENDING'",
        scope.params).fetchone()
    return row['n'] if row else 0


def subagent_span_count(conn, trace_id) -> int | None:
    """How many spans the subagent `trace_id` names owns, or None for an
    ordinary id."""
    scope = agent_span_scope(conn, trace_id)
    return _span_count(conn, scope) if scope is not None else None


_MARKER_NAMES_SQL = "('subagent.start','subagent.stop')"


def _agent_facts(conn, scope: AgentScope):
    """One pass over the agent's rows for everything its header needs.

    The usage figures follow `projection._billed_total`: the `subagent.stop`
    markers hold the agent's real transcript totals, and the agent's own spans
    answer for the workflow agents that never get a stop marker. Reproduced in
    SQL rather than by fetching the child, so a header read stays two indexed
    aggregates instead of a whole projection.
    """
    return conn.execute(f"""
        SELECT MIN(start_time) AS started_at,
               MAX(end_time)   AS ended_at,
               SUM(name = 'subagent.stop') AS stops,
               SUM(CASE WHEN name = 'subagent.stop' THEN input_tokens END)
                   AS billed_input,
               SUM(CASE WHEN name = 'subagent.stop' THEN output_tokens END)
                   AS billed_output,
               SUM(CASE WHEN name NOT IN {_MARKER_NAMES_SQL}
                        THEN input_tokens END) AS span_input,
               SUM(CASE WHEN name NOT IN {_MARKER_NAMES_SQL}
                        THEN output_tokens END) AS span_output,
               MAX(CASE WHEN name IN {_MARKER_NAMES_SQL}
                        THEN json_extract(attributes, '$.description') END)
                   AS description,
               MAX(CASE WHEN name IN {_MARKER_NAMES_SQL}
                        THEN json_extract(attributes, '$.agent_type') END)
                   AS agent_type
        FROM session_spans
        WHERE {scope.clause}
    """, scope.params).fetchone()


def _parent_session_row(conn, scope: AgentScope):
    """The parent's `sessions` row, preferring the canonical member of its
    alias group but falling back to whichever half HAS one — the same
    precedence the ordinary summary applies."""
    from lib.trace.alias import sql_in
    placeholders, params = sql_in(scope.trace_ids)
    return conn.execute(
        f"SELECT trace_id, model, cwd, status FROM sessions "
        f"WHERE trace_id IN {placeholders} "
        "ORDER BY (trace_id = ?) DESC LIMIT 1",
        [*params, scope.trace_ids[0]]).fetchone()


def subagent_summary(conn, trace_id) -> dict | None:
    """The session header for a subagent trace, or None for an ordinary id.

    No `sessions` row exists for an `sa-…` id, so every field is derived: what
    the agent IS from its markers, what it did and when from its own spans,
    and the run it belongs to (model, cwd, and the status it inherits until it
    stops) from the parent's row. A field no source supplies is left out
    rather than guessed — a header that says nothing about the model is
    honest, one that names the wrong model is not.
    """
    scope = agent_span_scope(conn, trace_id)
    if scope is None:
        return None
    facts = _agent_facts(conn, scope)
    fields = {
        'title': facts['description'] or facts['agent_type'] or scope.agent_id,
        'started_at': facts['started_at'],
        'ended_at': facts['ended_at'],
        'input_tokens': _billed(facts, 'input'),
        'output_tokens': _billed(facts, 'output'),
        'span_count_total': _span_count(conn, scope),
        # The id this child is really keyed on — itself. `trace_group` never
        # widens an `sa-…` id, so a client that rewrites its URL onto the
        # canonical id stays on the child instead of jumping to the parent.
        'canonical_trace_id': trace_id,
        # New for subagent traces only, so a client can offer "back to the run
        # that launched this".
        'parent_trace_id': scope.trace_ids[0],
        **_parent_fields(_parent_session_row(conn, scope)),
    }
    if facts['stops']:
        fields['status'] = 'ended'
    return {k: v for k, v in fields.items() if v is not None}


def _parent_fields(parent) -> dict:
    """What the child inherits from the run that launched it: the model it
    spoke to, the directory it ran in, and — until its own stop marker says
    otherwise — whether that run is still going."""
    if parent is None:
        return {}
    return {'model': parent['model'], 'cwd': parent['cwd'],
            'status': parent['status']}


def _billed(facts, side: str):
    """The agent's usage on one side: its stop markers' totals, else its own
    spans'. None when neither carries the figure."""
    billed = facts[f'billed_{side}']
    return billed if billed is not None else facts[f'span_{side}']

