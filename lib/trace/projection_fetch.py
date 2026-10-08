"""Span reads for projection: a session's rows, the delegated-window filter, and
the scoped rows of a subagent or session group."""

from __future__ import annotations

import json

from lib.trace.projection_agents import (
    AGENT_ROOT_KEY,
    _SUBAGENT_MARKER_NAMES,
    _agent_id_of,
    _attach_agent_rollups,
    _markers_by_agent,
    _normalize_agent_ids,
)


def _fetch_spans(conn, trace_id: str) -> list[dict]:
    rows = conn.execute("""
        SELECT id, trace_id, span_id, parent_id, name, kind,
               start_time, end_time, duration_ms, attributes,
               status_code, status_message,
               output_tokens, input_tokens, image_tokens, cost_usd,
               tool_use_id, turn_uuid, source_prompt_id, source
        FROM session_spans
        WHERE trace_id = ?
        ORDER BY start_time ASC, id ASC
    """, (trace_id,)).fetchall()
    return [
        {**dict(r), 'attributes': json.loads(r['attributes'])}
        for r in rows
    ]


_SPAN_COLUMNS = """
    id, trace_id, span_id, parent_id, name, kind,
    start_time, end_time, duration_ms, attributes,
    status_code, status_message,
    output_tokens, input_tokens, image_tokens, cost_usd,
    tool_use_id, turn_uuid, source_prompt_id, source
"""


def drop_delegated_spans(spans: list[dict]) -> list[dict]:
    """A run's own spans plus the markers of the subagents it launched — never
    those subagents' work.

    Every span an agent emits is stored in the trace that launched it, so a
    parent that serves them renders each agent's whole conversation inline —
    84% of one measured delegating session's rows, 96% of a workflow run's.
    That conversation is served whole under `sa-<agent_id>`, and the markers
    left here carry the rollup (`_attach_agent_rollups`) a single card needs
    to stand for it.

    Only an agent whose marker is HERE collapses. 128 of the 3,533 agents in
    the local store emitted 11,956 spans and no marker at all, and there is
    nothing in that case to stand for the work or to link to the child — the
    same rule `alias.aliased_run_ids` applies to an SDK run's second id, where
    only a child that really has a session row lets its counterpart be hidden.

    A no-op on a subagent trace, without a case for it: `_fetch_subagent_spans`
    clears the tag on the rows it re-keys, so the agent's own read keeps
    everything it owns.
    """
    collapsed = _markers_by_agent(spans, None)
    if not collapsed:
        return spans
    served, withheld = [], []
    for s in spans:
        keep = (s['name'] in _SUBAGENT_MARKER_NAMES
                or _agent_id_of(s) not in collapsed)
        (served if keep else withheld).append(s)
    _carry_withheld_launches(served, withheld)
    return served


def _carry_withheld_launches(served: list[dict], withheld: list[dict]) -> None:
    """Move a withheld `tool.Agent`'s brief onto the marker that stands for the
    agent it launched.

    An agent launched BY another agent has its `tool.Agent` among the launcher's
    delegated work, so the rule above withholds it while keeping the
    `subagent.start` it produced — and the card rendered off that marker had no
    description and no task prompt, while the first (top-level) agent's card,
    whose launch is the parent's own span, kept both. `launch_tool_use_id` is
    the marker's own record of the call that made it, so the join is by
    identity rather than by which launch happened nearest in time.

    Every marker naming the launch is stamped, including a resumed agent's
    later runs: the brief is what the agent was asked, not a quantity to sum,
    and a run with its own recorded ask (`agent_segment_prompt`) outranks it at
    render time anyway."""
    launches = _launch_attrs_by_tool_use_id(withheld)
    if not launches:
        return
    for s in served:
        if s['name'] != 'subagent.start':
            continue
        attrs = s['attributes']
        launch = launches.get(attrs.get('launch_tool_use_id'))
        if launch:
            _stamp_launch_brief(attrs, launch)


_CARRIED_LAUNCH_KEYS = (('prompt', 'agent_launch_prompt'),
                        ('description', 'agent_launch_description'))


def _launch_attrs_by_tool_use_id(spans: list[dict]) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for s in spans:
        if s['name'] != 'tool.Agent':
            continue
        attrs = s.get('attributes') or {}
        tool_use_id = s.get('tool_use_id') or attrs.get('tool_use_id')
        if tool_use_id:
            out.setdefault(tool_use_id, attrs)
    return out


def _stamp_launch_brief(attrs: dict, launch: dict) -> None:
    for key, carried in _CARRIED_LAUNCH_KEYS:
        if launch.get(key) and not attrs.get(carried):
            attrs[carried] = launch[key]


def _launch_tool_use_id(conn, trace_ids, agent_id: str) -> str | None:
    """The id an SDK run tagged this agent's spans with, off its
    `subagent.start` marker.

    Scoped by `(trace_id, agent_id)` alone so the query runs on that index; a
    `name` predicate makes the planner prefer the name index and reads the JSON
    of every marker in the store instead (15x slower here). Nothing but a marker
    carries the attribute, so the narrower scope loses nothing.
    """
    from lib.trace.alias import sql_in

    placeholders, params = sql_in(trace_ids)
    row = conn.execute(f"""
        SELECT json_extract(attributes, '$.launch_tool_use_id') AS launch
        FROM session_spans
        WHERE trace_id IN {placeholders} AND agent_id = ?
          AND json_extract(attributes, '$.launch_tool_use_id') IS NOT NULL
        ORDER BY id
        LIMIT 1
    """, [*params, agent_id]).fetchone()
    return row['launch'] if row else None


def _fetch_subagent_spans(conn, trace_ids, window_start, window_end):
    """The rows of one subagent, re-keyed to its own `sa-…` trace id, or None
    when the request is for an ordinary trace.

    Stripping `agent_id` from both the column and the attributes is what makes
    the child readable: every downstream pass keys subagent handling off that
    field, so leaving it set would re-nest the whole trace under a
    `subagent.start` that is not in the window and suppress its launch prompt
    as agent-scoped. Cleared, the ordinary prompt/turn machinery runs over the
    child unchanged.

    Both halves of an SDK run are read: the runner writes the same subagent's
    spans into the aliased sibling trace under the launching call's id, so a
    child selected on the roster id alone would show the hook's half of its own
    work and silently drop the rest.

    The agent's OWN markers never reach the result. They are the parent's
    account of this agent — served here they render the agent as a subagent of
    itself. The earliest start marker survives as the child's opening turn
    (`_agent_root_span`) rather than as a card, which is what keeps a trace
    without a `prompt-sa-…` launch prompt single-rooted.

    The agents THIS one launched are read too (`delegated_agent_ids`), tag
    intact, so a nested subagent is a card here exactly as it is in a top-level
    session: its rollup off its own spans, and its work withheld by
    `drop_delegated_spans` for the trace that addresses it.
    """
    from lib.trace.subagent_trace import agent_span_scope, delegated_agent_ids

    ids = [trace_ids] if isinstance(trace_ids, str) else list(trace_ids)
    scope = agent_span_scope(conn, ids) if ids else None
    if scope is None:
        return None
    rows = _scoped_agent_rows(conn, scope, delegated_agent_ids(conn, scope),
                             window_start, window_end)
    out, own_markers = _split_own_rows(rows, scope, ids[0])
    root = _agent_root_span(own_markers, out)
    if root is not None:
        out.append(root)
        out.sort(key=lambda s: (s['start_time'] or '', s['id'] or 0))
    return _attach_agent_rollups(_normalize_agent_ids(out, conn, scope.trace_ids))


def _scoped_agent_rows(conn, scope, delegated: list[str],
                       window_start, window_end):
    """The stored rows of one agent and of the agents it launched, in the
    paginated reader's window."""
    from lib.trace.alias import sql_in

    trace_ph, trace_params = sql_in(scope.trace_ids)
    agent_ph, agent_params = sql_in([*scope.agent_ids, *delegated])
    clauses = [f"trace_id IN {trace_ph}", f"agent_id IN {agent_ph}"]
    params = [*trace_params, *agent_params]
    if window_start is not None:
        clauses.append("start_time >= ?")
        params.append(window_start)
    if window_end is not None:
        clauses.append("start_time < ?")
        params.append(window_end)
    return conn.execute(f"""
        SELECT {_SPAN_COLUMNS}, agent_id
        FROM session_spans
        WHERE {' AND '.join(clauses)}
        ORDER BY start_time ASC, id ASC
    """, params).fetchall()


def _split_own_rows(rows, scope, served_id: str) -> tuple[list[dict], list[dict]]:
    """`(spans to serve, the agent's own markers)`, each row re-keyed to the
    served id.

    A delegated agent's rows keep their tag, carried from the column the scope
    selected on so a row the writer tagged in one place only still reads as
    delegated work — the same way the parent reads this agent's."""
    from lib.trace.alias import ORIGIN_KEY

    canonical = scope.trace_ids[0]
    own_ids = set(scope.agent_ids)
    out: list[dict] = []
    own_markers: list[dict] = []
    for r in rows:
        row = dict(r)
        owner = row.pop('agent_id', None)
        attrs = json.loads(row['attributes'])
        span = {**row, 'attributes': attrs, 'trace_id': served_id,
                ORIGIN_KEY: 'hook' if r['trace_id'] == canonical else 'sdk'}
        if owner in own_ids:
            attrs.pop('agent_id', None)
            if r['name'] in _SUBAGENT_MARKER_NAMES:
                own_markers.append(span)
                continue
        elif owner:
            attrs['agent_id'] = owner
        out.append(span)
    return out, own_markers


def _agent_root_span(own_markers: list[dict], out: list[dict]) -> dict | None:
    """The turn a child trace opens with, derived from its own earliest
    `subagent.start`, or None when it has an anchor already or no marker to
    derive one from.

    Same row, same id, same times — only re-read as what it means HERE: from
    the parent's side the marker announces an agent, from the child's it is the
    task the run was started on. Keeping the stored id (rather than
    synthesizing one) is what lets its subtree and its body still be fetched.

    Its text follows `subagent_trace._agent_facts`' precedence for the child's
    title, one rung longer: whatever of the launch prompt the marker kept,
    falling back to the description. An agent whose marker carries none of them
    still gets the anchor — a turn with no text beats a flat pile of rows.
    """
    if not own_markers or any(s['name'] == 'prompt' for s in out):
        return None
    starts = [m for m in own_markers if m['name'] == 'subagent.start']
    if not starts:
        return None
    marker = starts[0]
    attrs = marker['attributes']
    text = (attrs.get('prompt') or attrs.get('prompt_preview')
            or attrs.get('description'))
    root_attrs = {**attrs, AGENT_ROOT_KEY: True}
    if text:
        root_attrs['text'] = text
    return {**marker, 'name': 'prompt', 'attributes': root_attrs}


def _fetch_spans_for_group(conn, trace_ids, *, window_start=None,
                           window_end=None, names=None) -> list[dict]:
    """Span rows for a whole alias group, re-keyed to the canonical id.

    An SDK run's two traces are one session (see `lib/trace/alias.py`). The
    projection below groups by `trace_id`, so leaving the run's rows under
    their own id would render one session as two unrelated conversations.

    Each row is tagged with which writer produced it, because the reconcile in
    `merge.py` has to know which of a duplicate pair to keep — and the `source`
    column can't tell it (the runner's rows persist under the default
    `'hook'`). A single-id group tags everything `'hook'`, so the cross-source
    rules downstream are a no-op and unaliased sessions read exactly as before.

    `names` keeps only spans of those names — for a reader that merges one
    span kind and need not load the whole session to do it.

    `window_start`/`window_end` bound the paginated reader's window. Both
    readers share this one explicit column list: a column added to the store
    but not to the SELECT is invisible to the projection, and the two lists
    had already drifted (`tool_use_id` was missing from the windowed one).
    """
    from lib.trace.alias import ORIGIN_KEY, sql_in

    if not trace_ids:
        return []
    scoped = _fetch_subagent_spans(conn, trace_ids, window_start, window_end)
    if scoped is not None:
        return scoped
    placeholders, params = sql_in(trace_ids)
    clauses = [f"trace_id IN {placeholders}"]
    if window_start is not None:
        clauses.append("start_time >= ?")
        params.append(window_start)
    if window_end is not None:
        clauses.append("start_time < ?")
        params.append(window_end)
    if names:
        clauses.append(f"name IN ({','.join('?' * len(names))})")
        params.extend(names)
    rows = conn.execute(f"""
        SELECT {_SPAN_COLUMNS}
        FROM session_spans
        WHERE {' AND '.join(clauses)}
        ORDER BY start_time ASC, id ASC
    """, params).fetchall()
    canonical = params[0]
    return _attach_agent_rollups(_normalize_agent_ids([
        {**dict(r), 'attributes': json.loads(r['attributes']),
         'trace_id': canonical,
         ORIGIN_KEY: 'hook' if r['trace_id'] == canonical else 'sdk'}
        for r in rows
    ], conn, trace_ids))
