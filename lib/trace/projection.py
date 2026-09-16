"""Pure data transforms used to reshape raw session_spans into the tree
the Vue Trace view consumes.

These live in `lib/` because they are pure transforms over span dicts
with zero Flask/request dependency — both the web blueprints and the
lib-side ingest/queries layer need them, so they belong below the web
layer rather than above it. A handful of tests reach them as
`app_module._graft_orphans` etc.; `web/app.py` re-exports the names so
that surface stays intact.

Pairing is deliberate:
    _fetch_spans(conn, trace_id)       # read
    _graft_orphans(spans)              # pure
    _widen_envelopes(spans)            # pure
    _build_span_tree(spans)            # pure — builds the view model
    _persist_projection(conn, …)       # write — turns a projection into DB UPDATEs

The GET /api/sessions/<id> handler composes these as:
    raw = _fetch_spans(conn, id)
    projected = _widen_envelopes(_graft_orphans(raw))
    tree = _build_span_tree(projected)

POST /api/sessions/<id>/materialize adds a trailing _persist_projection
call to commit the shape back to disk.
"""

from __future__ import annotations

import json
import re
from datetime import datetime

from lib.trace.pending_spans import is_agent_scoped_prompt

# A `turn` span emitted on UserPromptSubmit fires in the same hook
# invocation as the new `prompt` span, so its timestamp lands a handful
# of milliseconds BEFORE the new prompt's timestamp. In older data (and
# in races where handler ordering slips), the turn sorts ahead of the
# new prompt in `_graft_orphans` and gets attached to the PREVIOUS
# prompt — widening that prompt's envelope to the next user input and
# inflating its duration by the entire user-idle gap. Any turn whose
# start_time is within this window of a LATER prompt's start_time is
# re-attributed to the later prompt. 1 s is comfortably larger than
# handler-chain jitter (ms) and comfortably smaller than any real
# per-tool response cadence, so it can't re-attach a legitimate turn.
_TURN_LOOKAHEAD_SECONDS = 1.0


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


def _launch_to_agent_map(rows: list[dict]) -> dict[str, str]:
    """`launch_tool_use_id -> agent_id` for every `subagent.start` marker in
    `rows` that carries the link.

    The two writers of an SDK run stamp `agent_id` in different namespaces: the
    hook writes the roster id, the runner writes the launching call's
    `parent_tool_use_id` (`lib/agent_events/from_sdk.py`). The marker is the
    only span carrying both (`lib/trace/claude_subagents.py`), so it is the
    only join between them.
    """
    out = {}
    for row in rows:
        if row['name'] != 'subagent.start':
            continue
        attrs = row['attributes']
        launch = attrs.get('launch_tool_use_id')
        agent_id = row.get('agent_id') or attrs.get('agent_id')
        if launch and agent_id:
            out[launch] = agent_id
    return out


def _stored_launch_map(conn, trace_ids, unmapped: set) -> dict[str, str]:
    """`launch_tool_use_id -> agent_id` off the STORED markers for the ids in
    `unmapped`, for a window that does not hold the marker itself.

    A paginated page or a `/subtrees` refresh is bounded to its roots' time
    window, and an agent launched in an earlier turn keeps emitting into the
    current one — the runner's rows for it carry the launching call's id, and
    with the marker outside the window nothing in the rows could translate
    it. Left as stored they read as the main agent's own work: a subagent's
    running Bash rendered as the parent's running command.
    """
    from lib.trace.alias import sql_in

    if conn is None or not trace_ids or not unmapped:
        return {}
    ids = [trace_ids] if isinstance(trace_ids, str) else list(trace_ids)
    tph, tparams = sql_in(ids)
    lph, lparams = sql_in(sorted(unmapped))
    try:
        found = conn.execute(f"""
            SELECT json_extract(attributes, '$.launch_tool_use_id') AS launch,
                   COALESCE(agent_id, json_extract(attributes, '$.agent_id')) AS aid
            FROM session_spans
            WHERE trace_id IN {tph} AND name = 'subagent.start'
              AND json_extract(attributes, '$.launch_tool_use_id') IN {lph}
        """, [*tparams, *lparams]).fetchall()
    except Exception:  # noqa: BLE001 — a schema predating the column reads
        # as "no marker", the same answer an unlinked agent gets.
        return {}
    return {r['launch']: r['aid'] for r in found if r['launch'] and r['aid']}


def _untranslated_agent_ids(rows: list[dict], launches: dict) -> set:
    """Agent ids in `rows` that neither a window marker names nor a window
    launch translates — the ones worth asking the store about."""
    roster_ids = set(launches.values()) | {
        _agent_id_of(r) for r in rows if r['name'] in _SUBAGENT_MARKER_NAMES}
    return {aid for aid in (_agent_id_of(r) for r in rows)
            if aid and aid not in launches and aid not in roster_ids}


def _normalize_agent_ids(rows: list[dict], conn=None, trace_ids=None) -> list[dict]:
    """Rewrite a runner-namespaced `agent_id` to the roster id its marker names,
    in the promoted column and the attributes alike so the two never disagree.

    A row already carrying a roster id, and one whose launching call no marker
    names, are left exactly as stored: the link only exists where the subagent's
    on-disk meta file did, and reading a span as the main agent's is the same
    answer as before. Markers outside the window are consulted through `conn`
    (`_stored_launch_map`) before giving up on an id.
    """
    launches = _launch_to_agent_map(rows)
    unmapped = _untranslated_agent_ids(rows, launches)
    launches = {**_stored_launch_map(conn, trace_ids, unmapped), **launches}
    if not launches:
        return rows
    for row in rows:
        attrs = row['attributes']
        agent_id = launches.get(row.get('agent_id') or attrs.get('agent_id'))
        if agent_id is None:
            continue
        attrs['agent_id'] = agent_id
        if 'agent_id' in row:
            row['agent_id'] = agent_id
    return rows


_SUBAGENT_MARKER_NAMES = frozenset({'subagent.start', 'subagent.stop'})


def _agent_id_of(span: dict) -> str | None:
    """Owning agent from the promoted column, falling back to the attribute —
    the same column-then-attribute shape as `_turn_uuid_of`."""
    return span.get('agent_id') or (span.get('attributes') or {}).get('agent_id')


def _sum_present(spans: list[dict], key: str):
    """Sum of one numeric column over the rows that carry it, or None when no
    row does. A summed 0 is an answer (a workflow agent really costs nothing);
    having no contributing row at all is not."""
    values = [s[key] for s in spans if s.get(key) is not None]
    return sum(values) if values else None


def _parsed_times(spans: list[dict], key: str) -> list[datetime]:
    out: list[datetime] = []
    for s in spans:
        try:
            out.append(datetime.fromisoformat(s.get(key)))
        except (TypeError, ValueError):
            continue
    return out


def _agent_extent_ms(spans: list[dict]) -> int | None:
    """First start to last end across an agent's own spans, in ms, or None when
    either bound is absent. An agent whose spans never recorded an end reports
    no extent rather than a zero one — the card can then say nothing instead of
    claiming the run was instant."""
    starts = _parsed_times(spans, 'start_time')
    ends = _parsed_times(spans, 'end_time')
    if not starts or not ends:
        return None
    ms = int((max(ends) - min(starts)).total_seconds() * 1000)
    return ms if ms >= 0 else None


def _marker_span_ms(opened: list[dict], closed: list[dict]) -> int | None:
    """First open marker to last close marker, in ms — how long the run was
    ALIVE, which is what the roster reports and what the agent's own view
    shows. The extent of its spans is a different figure (it stops at the last
    row recorded) and the two disagreed on screen: 2m20s on the parent's card
    against 2m32s in the agent's pane, for one run. Markers are point events
    whose stored `duration_ms` is 0, so the width has to come from the gap
    between them; with no close marker there is no width and the span extent
    answers instead."""
    opens = _parsed_times(opened, 'start_time')
    closes = _parsed_times(closed, 'start_time')
    if not opens or not closes:
        return None
    ms = int((max(closes) - min(opens)).total_seconds() * 1000)
    return ms if ms >= 0 else None


def _billed_total(stops: list[dict], spans: list[dict], key: str):
    """One usage figure for an agent, from its `subagent.stop` marker if that
    marker carries it, else summed off its spans.

    The marker is where the agent's REAL usage lands: `claude_subagents.
    _stamp_stop_marker` totals the subagent's own transcript onto it, and the
    billing rollup sums that same row. An agent's spans hold only the fragments
    the hooks happened to promote — for one measured agent, 2 output tokens
    against the marker's 12,926 and $3.51. The span sum is still the answer for
    the workflow agents, which never get a stop marker at all and whose spans
    do carry their output tokens.
    """
    billed = _sum_present(stops, key)
    return billed if billed is not None else _sum_present(spans, key)


def _agent_rollup(spans: list[dict], stops: list[dict],
                  starts: list[dict] | None = None) -> dict:
    """Totals for one agent as seen IN THIS WINDOW. A key no source supplied is
    None here and dropped by `_stamp_agent_rollup`.

    Duration is the agent's LIFETIME — its first start marker to its last stop
    marker (`_marker_span_ms`), the same width the roster and the agent's own
    pane report. The extent of its spans is the fallback for an agent with no
    stop marker at all, which is every workflow agent.

    The two COUNTS exclude PENDING rows because this runs before `merge_spans`
    and the append-only store keeps a placeholder alongside its resolved twin:
    they are one call, the resolved row is the one that survives, and counting
    both put 4,165 spans on one measured session's cards against the 2,708 it
    really has. The usage figures are unaffected — they come off the stop
    marker's transcript totals, not off this population."""
    resolved = [s for s in spans if s.get('status_code') != 'PENDING']
    fields = {
        'agent_input_tokens': _billed_total(stops, spans, 'input_tokens'),
        'agent_output_tokens': _billed_total(stops, spans, 'output_tokens'),
        'agent_cost_usd': _billed_total(stops, spans, 'cost_usd'),
    }
    if spans:
        fields.update({
            'agent_span_count': len(resolved),
            'agent_tool_count': sum(
                1 for s in resolved if (s.get('name') or '').startswith('tool.')),
            'agent_duration_ms': (_marker_span_ms(starts or [], stops)
                                  or _agent_extent_ms(spans)),
        })
    return fields


def _stamp_child_trace_id(marker: dict, agent_id: str) -> None:
    """The one key every marker carries: an address, not a quantity, so it is
    safe to repeat."""
    from lib.trace.subagent_trace import subagent_trace_id

    attrs = marker['attributes']
    attrs['agent_child_trace_id'] = (
        attrs.get('child_trace_id') or subagent_trace_id(agent_id))


def _stamp_agent_rollup(marker: dict, agent_id: str, spans: list[dict],
                        stops: list[dict], starts: list[dict] | None = None) -> None:
    fields = _agent_rollup(spans, stops, starts)
    marker['attributes'].update(
        {k: v for k, v in fields.items() if v is not None})
    _stamp_child_trace_id(marker, agent_id)


def _segment_spans(starts: list[dict], spans: list[dict]) -> dict[str, list[dict]]:
    """start span_id → the spans in ITS segment, by the same ingest-order rule
    `_reparent_subagents` nests on."""
    out: dict[str, list[dict]] = {st['span_id']: [] for st in starts}
    for s in spans:
        owner = _owning_start_id(starts, s)
        if owner in out:
            out[owner].append(s)
    return out


def _segment_asked(segment: list[dict]) -> dict:
    """What THIS run was asked. Each user turn of a resumed agent's transcript
    becomes its own agent-scoped `prompt` span (`subagent_lifecycle.
    _emit_subagent_prompt`), and the one in this segment is the run's prompt —
    the launch's prompt only ever describes the first."""
    asks = [s for s in segment if s.get('name') == 'prompt']
    if not asks:
        return {}
    text = (asks[0].get('attributes') or {}).get('text')
    return {'agent_segment_prompt': text} if text else {}


# The marker's copy of an agent's answer is a READING copy: the message is also
# served whole under the agent's own trace, and every marker's copy rides the
# same window payload as the `assistant_response` row it duplicates.
_AGENT_RESULT_MAX_BYTES = 8 * 1024


def _ingest_id(span: dict) -> int:
    return span.get('id') or 0


_TRUNC_MARKER = '\n\n…'
_FENCE_RE = re.compile(r'^ {0,3}(?:```|~~~)')


def _markdown_safe(head: str) -> str:
    """`head` retreated to the last boundary markdown survives being cut at.

    The budget is counted in bytes, so its edge falls wherever it falls — in
    the middle of an inline-code span, or between the two fences of a code
    block. Either leaves a construct open, and an open construct swallows
    whatever the card renders after it. Drop the line the cut landed in, then
    the block an unpaired fence opened; a lone over-long line has no boundary
    left but the space between two words.
    """
    lines = head.split('\n')
    partial = lines.pop()
    fences = [i for i, line in enumerate(lines) if _FENCE_RE.match(line)]
    if len(fences) % 2:
        del lines[fences[-1]:]
    while lines and not lines[-1].strip():
        lines.pop()
    if lines:
        return '\n'.join(lines)
    return partial[:partial.rfind(' ')] if ' ' in partial else partial


def _capped(text: str) -> str:
    raw = text.encode('utf-8')
    if len(raw) <= _AGENT_RESULT_MAX_BYTES:
        return text
    budget = _AGENT_RESULT_MAX_BYTES - len(_TRUNC_MARKER.encode('utf-8'))
    return _markdown_safe(raw[:budget].decode('utf-8', 'ignore')) + _TRUNC_MARKER


def _stop_result(stops: list[dict]) -> str:
    for stop in stops:
        result = (stop.get('attributes') or {}).get('result_preview')
        if result:
            return result
    return ''


def _final_message(spans: list[dict]) -> str:
    """The agent's last assistant turn — the message the stop marker's
    `result_preview` is a cut of.

    That preview is built by flattening every run of whitespace and capping at
    200 characters (`subagent_lifecycle._result_preview`), so an agent whose
    answer is a markdown report reaches the card as one unreadable line of
    source ("## Verdict … **1. "). The turn itself is in the store beside it,
    newlines intact, and is the only copy that can be rendered.
    """
    ordered = sorted(spans, key=lambda s: (s.get('start_time') or '',
                                           _ingest_id(s)))
    for span in reversed(ordered):
        # PENDING is a placeholder beside its resolved twin (`_one_row_per_span`
        # keeps the first of each), and a placeholder with no twin holds a
        # partial turn — the same population `_segment_shape` refuses.
        if (span.get('name') != 'assistant_response'
                or span.get('status_code') == 'PENDING'):
            continue
        text = (span.get('attributes') or {}).get('text')
        if isinstance(text, str) and text.strip():
            return _capped(text.strip())
    return ''


def _answer(stops: list[dict], spans: list[dict], key: str) -> dict:
    """The answer a CLOSED working period reported, under `key`.

    Gated on a stop marker either way: mid-run the newest assistant turn is a
    step ("Now the composable —"), not an answer, and a card that showed it
    would keep relabelling a running agent's result.
    """
    if not _stop_result(stops):
        return {}
    return {key: _final_message(spans) or _stop_result(stops)}


def _carry_answer(start: dict, stops: list[dict], spans: list[dict],
                  key: str) -> None:
    """Move a closed run's answer onto the card that renders it, under `key`,
    and name that card on every stop that reported it.

    The result is reported on `subagent.stop` and the card is rendered off
    `subagent.start`, so until it is carried across the two the answer of every
    ordinary Task subagent is invisible in the feed: over the live store 1,482
    agents held a result their card could not reach. But the stop is a feed row
    of its own, so carrying the answer alone left it printed twice — rendered
    on the card, then the same text a line below as the flattened 200-char cut
    the stop reports ("Subagent done · Explore — Here's the complete map…").
    `agent_result_carried_by` is what lets the feed drop the second copy
    (`dispatch.rendersInConversation`) while the marker stays in the tree.

    Written only where the carry really happened: a window holding a stop
    without its start shows that answer nowhere else, and a row dropped there
    would take the answer with it.
    """
    answered = _answer(stops, spans, key)
    if not answered:
        return
    start['attributes'].update(answered)
    for stop in stops:
        if (stop.get('attributes') or {}).get('result_preview'):
            stop['attributes']['agent_result_carried_by'] = start['span_id']


def _stamp_agent_result(starts: list[dict], stops: list[dict],
                        spans: list[dict]) -> None:
    """A resumed agent is left alone: its runs each answered separately and
    `_stamp_agent_segments` gives every card its own, where one whole-agent
    result repeated across them would attribute the last run's answer to all.

    `len(starts)` counts the LOADED window, not the agent, so a page holding a
    resumed agent's second start beside its FIRST run's stop reads as
    single-run — and would hand run 2's card the answer run 1 gave. Only a stop
    ingested at/after the start can have closed it, which is the same
    append-only id order `_owning_start_id` segments on."""
    if len(starts) != 1:
        return
    start = starts[0]
    closed_by = [s for s in stops if _ingest_id(s) >= _ingest_id(start)]
    if not closed_by:
        return
    # The turns of THIS run only, by the same append-only id order
    # `_owning_start_id` segments on: an id-contiguous page can hold an earlier
    # run's turns without its start marker, and that is the run whose answer
    # this marker must not borrow. No ceiling pairs with it — the SubagentStop
    # hook posts the stop marker BEFORE catching the agent's transcript up, so
    # the answering turn is ingested AFTER the stop that reports it (measured:
    # stop id 10014150, its turn 10014162).
    lo = _ingest_id(start)
    run = [s for s in spans if _ingest_id(s) >= lo]
    _carry_answer(start, closed_by, run, 'agent_result_preview')


def _segment_duration_ms(segment: list[dict], start, stops) -> int | None:
    """One run's lifetime: its start marker to the stop that closed it,
    falling back to the extent of its spans while it is still open."""
    return (_marker_span_ms([start] if start else [], stops or [])
            or _agent_extent_ms(segment))


def _segment_shape(segment: list[dict], start: dict | None = None,
                   stops: list[dict] | None = None) -> dict:
    """Count and extent for ONE segment. Excludes PENDING for the reason
    `_agent_rollup` does: the append-only store keeps a placeholder beside its
    resolved twin and they are one call.

    A segment holding nothing but in-flight rows reports no shape at all — the
    same refusal `_agent_rollup` makes by omitting a figure no source supplied.
    That is the state a resumed agent is in for its first seconds, and `0
    spans` on the card would say the run did nothing rather than that it has
    not finished anything yet."""
    resolved = [s for s in segment if s.get('status_code') != 'PENDING']
    if not resolved:
        return {}
    fields = {
        'agent_segment_span_count': len(resolved),
        'agent_segment_tool_count': sum(
            1 for s in resolved if (s.get('name') or '').startswith('tool.')),
        'agent_segment_duration_ms': _segment_duration_ms(segment, start, stops),
    }
    return {k: v for k, v in fields.items() if v is not None}


def _chronological_rank(starts: list[dict]) -> dict[str, int]:
    """start span_id → its position in READING order.

    Which segment a span belongs to keys on the append-only id, because that is
    what `_reparent_subagents` nests on and the counts have to agree with the
    tree. The NUMBER on the card cannot: the feed is ordered by time, and a
    marker a repair pass synthesized carries a late id with an early timestamp
    (`repair._synthesize_start`). Numbering by id there labels the first card on
    screen `run 2 of 2` and hands the live roster line to the older run — the
    mis-attribution this whole split exists to remove."""
    order = sorted(starts,
                   key=lambda s: (s.get('start_time') or '', s.get('id') or 0))
    return {s['span_id']: i for i, s in enumerate(order)}


def _stamp_agent_segments(group: list[dict], spans: list[dict]) -> None:
    """Tell a RESUMED agent's markers apart.

    A `SendMessage` continuation emits a second `subagent.start` under the same
    `agent_id`, so one agent can own several cards. Every other per-agent
    source — the roster entry the card reads its liveness from, the child trace
    id, the launch it folds in — is keyed on the agent, which left the cards
    indistinguishable: the same "running", the same elapsed, the same step.
    The position (`agent_segment_index`/`_count`) is what lets a card say which
    run it is; the shape, the prompt and the result are what it says ABOUT that
    run. All three come off the run's own rows, so the second card stops
    borrowing the first one's launch prompt and the first one's totals.

    Unlike the whole-agent totals these are per-segment and DO sum to the
    agent, so repeating them across markers is safe. A single-segment agent is
    left untouched — no key here, nothing for a reader to reconcile."""
    starts = [m for m in group if m['name'] == 'subagent.start']
    if len(starts) < 2:
        return
    by_start = _segment_spans(starts, spans)
    stops_by_start = _segment_spans(
        starts, [m for m in group if m['name'] == 'subagent.stop'])
    rank = _chronological_rank(starts)
    for start in starts:
        own = by_start.get(start['span_id'], [])
        own_stops = stops_by_start.get(start['span_id'], [])
        start['attributes'].update({
            'agent_segment_index': rank[start['span_id']],
            'agent_segment_count': len(starts),
            **_segment_shape(own, start, own_stops),
            **_segment_asked(own),
        })
        _carry_answer(start, own_stops, own, 'agent_segment_result')


def _one_row_per_span(rows: list[dict]) -> list[dict]:
    """Collapse rows that are the same span, keeping the first of each.

    The rollup is computed on the way OUT of the fetch, before `merge_spans`
    runs — so on a session both writers recorded (`alias.trace_group` returns
    the hook trace and its `sdk-` twin) every span is here twice and every
    count is doubled. One measured agent reported 45 spans and 32 tools for
    the 29 and 16 it really had, against a roster and a child-trace view that
    both said 29.

    `(trace_id, span_id)` is unique by construction — the store's own index —
    so this needs no origin tag to be correct, and a resolved row is preferred
    over a placeholder for the same id in case one writer lagged."""
    seen: dict[tuple, dict] = {}
    for r in rows:
        key = (r.get('trace_id'), r.get('span_id'))
        kept = seen.get(key)
        if kept is None or (kept.get('status_code') == 'PENDING'
                            and r.get('status_code') != 'PENDING'):
            seen[key] = r
    return list(seen.values())


def _spans_by_agent(rows: list[dict], agent_id: str | None) -> dict[str, list[dict]]:
    """agent_id → the spans it owns, markers excluded and each span once."""
    owned: dict[str, list[dict]] = {}
    for r in rows:
        if r['name'] in _SUBAGENT_MARKER_NAMES:
            continue
        aid = agent_id or _agent_id_of(r)
        if aid:
            owned.setdefault(aid, []).append(r)
    return {aid: _one_row_per_span(rs) for aid, rs in owned.items()}


def _markers_by_agent(rows: list[dict], agent_id: str | None) -> dict[str, list[dict]]:
    """agent_id → its markers in ingest (DB id) order. Half the agents in the
    store have several — a resumed agent starts and stops once per segment —
    and the order decides which one holds the agent's totals, so it is taken
    from the append-only id rather than a timestamp."""
    markers: dict[str, list[dict]] = {}
    for r in rows:
        if r['name'] not in _SUBAGENT_MARKER_NAMES:
            continue
        aid = agent_id or _agent_id_of(r)
        if aid:
            markers.setdefault(aid, []).append(r)
    out = {aid: _one_row_per_span(ms) for aid, ms in markers.items()}
    for group in out.values():
        group.sort(key=lambda m: m.get('id') or 0)
    return out


def _attach_agent_rollups(rows: list[dict], *, agent_id: str | None = None) -> list[dict]:
    """Stamp each subagent marker in `rows` with totals computed from that
    agent's own spans.

    A marker cannot answer "what did this subagent do" on its own: 1,555 of the
    workflow agents in the store have no `subagent.stop` at all, so shape and
    extent have to come off the spans, and usage falls back to them wherever the
    stop marker's stamped totals are missing (`_billed_total`). Computed on the
    way out, over the rows already fetched — nothing is written back, and a
    window holding part of an agent's spans reports that part rather than
    pretending to a total.

    Markers are excluded from the population they summarize: a `subagent.start`
    is the announcement of the work, not the work.

    Every figure here is a WHOLE-AGENT total — the spend of the whole run, the
    count of every span it owns, the extent from its first to its last — so all
    of them land on the agent's earliest marker alone. Half the agents in the
    store carry more than one (a resumed agent starts again per segment), and
    repeating a total on each is an invitation to sum markers and over-report
    the bill by 2.5x. The later markers keep only `agent_child_trace_id`, which
    is an address rather than a quantity: a per-segment card still links to the
    child trace, and there is nothing on it to add up twice. What a later
    marker does get is its own SEGMENT's position and shape
    (`_stamp_agent_segments`) — per-segment figures that sum to the agent
    rather than repeating it. A single-run agent also gets its answer
    (`_stamp_agent_result`), which lives on the stop marker and so reaches no
    card on its own.

    `agent_id` names the one agent every row belongs to, for the child read
    (`_fetch_subagent_spans`), which has already cleared the tag the two writers
    disagreed on. On the parent read the tag is left on each row and must
    already be normalized (`_normalize_agent_ids`), or an SDK-written row is
    still keyed by its launching call and rolls up under nobody.
    """
    by_agent = _markers_by_agent(rows, agent_id)
    if not by_agent:
        return rows
    owned = _spans_by_agent(rows, agent_id)
    for aid, group in by_agent.items():
        stops = [m for m in group if m['name'] == 'subagent.stop']
        starts = [m for m in group if m['name'] == 'subagent.start']
        _stamp_agent_rollup(group[0], aid, owned.get(aid, []), stops, starts)
        for later in group[1:]:
            _stamp_child_trace_id(later, aid)
        _stamp_agent_segments(group, owned.get(aid, []))
        _stamp_agent_result(starts, stops, owned.get(aid, []))
    return rows


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


# Marks the turn a subagent trace derives from its own start marker, so the
# orphan graft can anchor on it (`_fallback_orphan_anchor`) without mistaking a
# NESTED agent's launch prompt for the trace's own.
AGENT_ROOT_KEY = 'agent_root'


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
                           window_end=None) -> list[dict]:
    """Span rows for a whole alias group, re-keyed to the canonical id.

    An SDK run's two traces are one session (see `lib/trace/alias.py`). The
    projection below groups by `trace_id`, so leaving the run's rows under
    their own id would render one session as two unrelated conversations.

    Each row is tagged with which writer produced it, because the reconcile in
    `merge.py` has to know which of a duplicate pair to keep — and the `source`
    column can't tell it (the runner's rows persist under the default
    `'hook'`). A single-id group tags everything `'hook'`, so the cross-source
    rules downstream are a no-op and unaliased sessions read exactly as before.

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


_SESSION_LIFECYCLE_NAMES = frozenset({'session.start', 'session.end'})
_COMPACT_BOUNDARY_NAMES = frozenset({'compact.pre', 'compact.post'})
# A `/rewind` marker: a conversation-level divider at the fork point. Like
# the other boundaries it stays a top-level node (grafted to the conversation
# root, never under a prompt), but unlike them it ADOPTS the discarded turns
# as children so the UI can collapse the abandoned branch behind it.
_REWIND_BOUNDARY_NAMES = frozenset({'rewind'})
# Boundary spans never nest under a prompt — they delimit conversation-level
# events (session start/end, /compact runs, /rewind). Grafting them under the
# most recent prompt would hide the boundary inside an unrelated turn's
# descendants and lose the visual divider.
_FIRST_CLASS_BOUNDARY_NAMES = (
    _SESSION_LIFECYCLE_NAMES | _COMPACT_BOUNDARY_NAMES | _REWIND_BOUNDARY_NAMES
)


def _is_pending(span: dict) -> bool:
    """A live placeholder span awaiting its resolved counterpart."""
    return span.get('status_code') == 'PENDING'


# Spans the deterministic ladder never re-parents: prompts/conversation
# are roots or anchors, boundaries delimit conversation-level events, and
# subagent markers are re-parented by agent_id in a dedicated pass.
_LADDER_SKIP_NAMES = (
    frozenset({'prompt', 'conversation', 'subagent.start', 'subagent.stop'})
    | _FIRST_CLASS_BOUNDARY_NAMES
)

# The per-turn response/thinking anchors. They are PEERS of each other under
# the turn's prompt — never parent/child — so the ladder must not let one nest
# under the other (that forms a 2-cycle the tree builder silently drops).
_RESP_THINK_ANCHOR_NAMES = frozenset({'assistant_response', 'assistant.thinking'})


def _turn_uuid_of(span: dict) -> str | None:
    """Turn uuid from the column, falling back to the attribute the live
    hook stamped (`attributes.turn_uuid`)."""
    return span.get('turn_uuid') or (span.get('attributes') or {}).get('turn_uuid')


def _source_prompt_id_of(span: dict) -> str | None:
    """Issuing-prompt id from the promoted column, falling back to the
    attribute the hook stamped (`attributes.source_prompt_id`) for rows
    written before the column was promoted. Same column-then-attribute
    shape as `_turn_uuid_of`."""
    return span.get('source_prompt_id') or (span.get('attributes') or {}).get('source_prompt_id')


def _build_prompt_by_turn(out: list[dict]) -> dict[str, str]:
    """turn_uuid → prompt anchor span_id, learned from the
    assistant_response / assistant.thinking spans that already carry a
    `prompt-*` parent (set at write time by the turn_trace poster). Lets a
    turn's tools fall to the prompt rung even when the turn emitted no
    `resp-`/`think-` span (a silent tool-only turn)."""
    by_turn: dict[str, str] = {}
    for s in out:
        if s['name'] not in ('assistant_response', 'assistant.thinking'):
            continue
        t = _turn_uuid_of(s)
        p = s.get('parent_id')
        if t and isinstance(p, str) and p.startswith('prompt-'):
            by_turn[t] = p
    return by_turn


def _build_prompt_by_source_id(out: list[dict]) -> dict[str, str]:
    """prompt_id value → prompt anchor span_id, learned from the
    `prompt` anchors that carry an `attributes.prompt_id` (stamped from the
    transcript's `promptId` by the turn_trace poster). The join partner for a
    tool span's `source_prompt_id`: the hook envelope mirrors the same
    `promptId` value onto every tool span, so this lets a NULL-parent tool
    nest under its issuing prompt by CLI ground truth. Legacy anchors emitted
    before the stamp carry no `prompt_id` and never enter this map."""
    by_pid: dict[str, str] = {}
    for s in out:
        if s['name'] != 'prompt':
            continue
        pid = (s.get('attributes') or {}).get('prompt_id')
        if isinstance(pid, str) and pid:
            by_pid[pid] = s['span_id']
    return by_pid


def _rung0_source_prompt_parent(
    s: dict, prompt_by_source_id: dict[str, str], by_id: dict,
) -> str | None:
    """Rung 0 candidate: the prompt anchor a non-anchor orphan's
    `source_prompt_id` names by value (CLI ground truth), or None. The
    `resp-`/`think-` anchors are excluded — they are the turn's prompt-level
    PEERS, so letting one take a prompt parent here would bypass the
    no-2-cycle rule the turn_uuid rungs below enforce."""
    if s['name'] in _RESP_THINK_ANCHOR_NAMES:
        return None
    spid = _source_prompt_id_of(s)
    anchor = prompt_by_source_id.get(spid) if spid else None
    if anchor and anchor in by_id and anchor != s['span_id']:
        return anchor
    return None


def _anchor_orphan_parent(t: str | None, prompt_by_turn: dict, by_id: dict, span_id: str) -> str | None:
    """Candidate parent for an orphan `resp-`/`think-` anchor: the turn's
    prompt anchor only — never rung 0 (see `_rung0_source_prompt_parent`) and
    never the OTHER turn-level anchor, which would form a 2-cycle."""
    if not t:
        return None
    cand = prompt_by_turn.get(t)
    return cand if cand and cand in by_id and cand != span_id else None


def _turn_anchor_parent(t: str | None, by_id: dict, span_id: str) -> str | None:
    """Candidate parent from the turn's own `resp-<turn>` / `think-<turn>`
    anchors, in that order, or None when neither exists in the window."""
    if not t:
        return None
    for cand in (f'resp-{t[:13]}', f'think-{t[:13]}'):
        if cand in by_id and cand != span_id:
            return cand
    return None


def _ladder_parent_for(
    s: dict, t: str | None, prompt_by_turn: dict,
    prompt_by_source_id: dict, by_id: dict,
) -> str | None:
    """Candidate parent for a non-anchor orphan, in precedence order:
    `resp-<turn>` → `think-<turn>` → rung 0 `source_prompt_id` value-join →
    the turn's prompt anchor. The turn-level anchors win first — they are the
    finer-grained parent; rung 0 (CLI ground truth, the hook envelope's
    issuing-prompt id) only fires when neither exists for the turn, but it
    still beats the turn's own coarser prompt anchor. Also catches a tool
    span whose `turn_uuid` never got attributed (which would otherwise skip
    the turn_uuid gate and fall to the chronological fallback)."""
    cand = _turn_anchor_parent(t, by_id, s['span_id'])
    if cand:
        return cand
    rung0 = _rung0_source_prompt_parent(s, prompt_by_source_id, by_id)
    if rung0:
        return rung0
    if not t:
        return None
    cand = prompt_by_turn.get(t)
    return cand if cand and cand in by_id and cand != s['span_id'] else None


def _ladder_orphans_by_turn(out: list[dict], by_id: dict) -> None:
    """Deterministic parent ladder for NULL-parent, non-boundary spans,
    chosen purely by deterministic id existence / stamped value (no
    timestamps) — see `_ladder_parent_for` for the precedence. This is the
    preference pass; spans it can't place (no source_prompt_id, no
    turn_uuid, or none of the rungs exist) are left NULL for the
    chronological `_graft_orphans_under_prompt` fallback. Mutates in place.

    The `resp-`/`think-` anchors of a turn are PEERS, not parent/child. An
    orphan anchor (a turn with no write-time prompt parent — chiefly a
    `task.notification`/background-completion turn that had no user-prompt
    anchor) must skip the anchor rungs, or `think-<t>` and `resp-<t>` pick
    each other and form a 2-cycle that `_build_span_tree` can't root,
    silently dropping the whole turn (and its child tools) — `_anchor_orphan_
    parent` takes the prompt rung only.

    Load-bearing for migration: an old session (parent_id NULL but
    turn_uuid populated, once repaired so the resp/think anchors carry
    prompt parents) heals here at read time with zero re-emission. A span
    whose anchor carries no stamped `prompt_id` falls straight through rung 0
    to the turn's prompt anchor unchanged."""
    prompt_by_turn = _build_prompt_by_turn(out)
    prompt_by_source_id = _build_prompt_by_source_id(out)
    for s in out:
        if s.get('parent_id') is not None:
            continue
        if s['name'] in _LADDER_SKIP_NAMES:
            continue
        t = _turn_uuid_of(s)
        if s['name'] in _RESP_THINK_ANCHOR_NAMES:
            cand = _anchor_orphan_parent(t, prompt_by_turn, by_id, s['span_id'])
        else:
            cand = _ladder_parent_for(s, t, prompt_by_turn, prompt_by_source_id, by_id)
        if cand:
            s['parent_id'] = cand


def _fallback_orphan_anchor(out: list[dict]) -> dict | None:
    """The span orphans graft under when a trace has no `prompt` of its own,
    or None when the trace has to stay flat.

    Only a subagent trace (`sa-<agent_id>`, see lib/trace/subagent_trace) has
    one. Three quarters of stored agents never got a `prompt-sa-…` launch
    prompt, and without an anchor every one of their spans projects as a root
    — a flat pile instead of a conversation. The turn derived from the agent's
    own start marker (`_agent_root_span`) is emitted for exactly that case and
    holds the tree. A launch prompt still wins where one exists, and an agent
    with neither stays flat rather than getting a synthesized anchor.

    Only that derived turn qualifies, never any `prompt` in the window: a
    NESTED agent's `prompt-sa-…` is another run's opening, and anchoring on it
    would hang this agent's work inside the one it delegated to.

    A parent trace is deliberately excluded: there a `subagent.start` is one
    marker among many agents', and grafting main-loop orphans under it would
    nest unrelated work inside a subagent.
    """
    from lib.trace.subagent_trace import is_subagent_trace_id

    if not out or not is_subagent_trace_id(out[0].get('trace_id')):
        return None
    roots = [s for s in out
             if (s.get('attributes') or {}).get(AGENT_ROOT_KEY)]
    return roots[0] if roots else None


def _graft_orphans_under_prompt(sorted_spans: list[dict], by_id: dict,
                                anchor: dict | None = None) -> None:
    """Graft each orphan span under the most recent `prompt`, or under
    `anchor` until one appears. Mutates the dicts in `by_id` in place.

    A live PENDING placeholder (promptlive-…) acts as a real prompt here: it
    starts at submit time, so it only becomes `current_prompt` for spans that
    come AFTER it — its own turn's in-flight tools/response, which haven't yet
    been parented (their `resp-`/`prompt-` anchors land on Stop). Without this
    the in-flight tools would orphan to the PREVIOUS prompt (or to root) and the
    placeholder itself would nest under the previous prompt instead of opening a
    new turn. The pre-P2b steal concern (grabbing a prior turn's work) no longer
    applies: assistant/tool spans now carry deterministic write-time parents, so
    a completed turn's spans are never orphans the placeholder could capture.
    First-class boundary spans never anchor orphans."""
    current_prompt = anchor
    for span in sorted_spans:
        resolved = by_id[span['span_id']]
        if resolved['name'] == 'prompt' and not is_agent_scoped_prompt(resolved):
            current_prompt = resolved
        elif resolved['name'] in _FIRST_CLASS_BOUNDARY_NAMES:
            continue
        elif (current_prompt is not None and resolved is not current_prompt
                and not resolved.get('parent_id')):
            resolved['parent_id'] = current_prompt['span_id']


def _graft_conversation_roots(out: list[dict], conv_span_id: str) -> None:
    """Graft orphan prompts + first-class boundary spans under the
    conversation span (when one exists). Never nests them under a
    prompt — a boundary must stay a top-level divider."""
    grafted_names = {'prompt', *_FIRST_CLASS_BOUNDARY_NAMES}
    for s in out:
        if s['name'] in grafted_names and not s.get('parent_id'):
            s['parent_id'] = conv_span_id


def _heal_dangling_parents(out: list[dict], by_id: dict) -> None:
    """Clear parent_ids pointing to a non-existent span. Happens when a
    write-time parent referenced an anchor that was never emitted (e.g.
    an unreadable transcript tail). The cleared span then falls to the
    ladder / chronological fallback like any other orphan."""
    valid_ids = set(by_id)
    for s in out:
        pid = s.get('parent_id')
        if pid and pid not in valid_ids:
            s['parent_id'] = None


def _attach_turn_to_following_prompt(
    turn_span: dict, s_start_dt: datetime, prompts_by_start: list[dict],
) -> None:
    for p in prompts_by_start:
        try:
            p_start_dt = datetime.fromisoformat(p['start_time'])
        except (TypeError, ValueError):
            continue
        delta = (p_start_dt - s_start_dt).total_seconds()
        if 0 <= delta < _TURN_LOOKAHEAD_SECONDS:
            turn_span['parent_id'] = p['span_id']
            return


# Spans that fire on UserPromptSubmit a few ms BEFORE the new prompt's
# anchor lands, so a chronological graft would wrongly attach them to the
# PREVIOUS prompt. `turn`: the per-turn usage marker; `memory.recall`: the
# `<recalled_experience>` injection span (emitted by the memory_recall
# handler, which runs before prompt_trace). Both carry no deterministic
# parent, so they need this chronological nudge onto the following prompt.
_SUBMIT_LOOKAHEAD_NAMES = frozenset({'turn', 'memory.recall'})


def _relabel_turns_by_lookahead(out: list[dict]) -> None:
    """Re-attribute submit-time spans that landed a few ms before a LATER
    prompt back onto that prompt. See `_TURN_LOOKAHEAD_SECONDS` and
    `_SUBMIT_LOOKAHEAD_NAMES`. These spans carry no deterministic parent,
    so this chronological nudge stays."""
    prompts_by_start = sorted(
        (s for s in out if s['name'] == 'prompt' and not _is_pending(s)
         and not is_agent_scoped_prompt(s)),
        key=lambda s: s['start_time'],
    )
    for s in out:
        if s['name'] not in _SUBMIT_LOOKAHEAD_NAMES:
            continue
        try:
            s_start_dt = datetime.fromisoformat(s['start_time'])
        except (TypeError, ValueError):
            continue
        _attach_turn_to_following_prompt(s, s_start_dt, prompts_by_start)


def _subagent_starts_by_agent(out: list[dict]) -> dict[str, list[dict]]:
    """agent_id → its `subagent.start` spans, in ingest (DB id) order."""
    starts: dict[str, list[dict]] = {}
    for s in out:
        if s['name'] != 'subagent.start':
            continue
        aid = (s.get('attributes') or {}).get('agent_id')
        if aid:
            starts.setdefault(aid, []).append(s)
    for lst in starts.values():
        lst.sort(key=lambda s: s.get('id') or 0)
    return starts


def _owning_start_id(starts: list[dict], span: dict) -> str | None:
    """The start this span's segment belongs to: the latest start ingested
    at/before it. A span ingested before any start (rescan interleaving)
    falls back to the first."""
    if not starts:
        return None
    sid = span.get('id') or 0
    owner = None
    for st in starts:
        if (st.get('id') or 0) <= sid:
            owner = st
    return (owner or starts[0])['span_id']


def _raw_parent_agent_edges(starts_by_agent: dict[str, list[dict]]) -> dict[str, str]:
    """agent_id → parent_agent_id, one edge per agent, read straight off its
    first start's `attributes.parent_agent_id` (self-references excluded).
    May still contain cycles — `_drop_cyclic_agent_edges` filters those."""
    edges: dict[str, str] = {}
    for agent_id, starts in starts_by_agent.items():
        for st in starts:
            parent = (st.get('attributes') or {}).get('parent_agent_id')
            if parent and parent != agent_id:
                edges[agent_id] = parent
                break
    return edges


def _drop_cyclic_agent_edges(edges: dict[str, str]) -> dict[str, str]:
    """Drop any agent→parent edge whose parent chain walks back to the agent
    itself (mutual/2-cycle or longer)."""
    dropped: set[str] = set()
    for child in edges:
        visited = {child}
        cur = edges.get(child)
        while cur is not None:
            if cur in visited:
                dropped.add(child)
                break
            visited.add(cur)
            cur = edges.get(cur)
    return {c: p for c, p in edges.items() if c not in dropped}


def _nested_parent_agent_edges(starts_by_agent: dict[str, list[dict]]) -> dict[str, str]:
    """agent_id → parent_agent_id, one edge per agent, with any self-reference
    or cycle dropped — the same precision-over-recall guard `claude_subagents.
    _resolve_parent_agents` applies at write time, reapplied here so LEGACY
    rows stamped before that guard existed (or any other bad data) can't feed
    `_build_span_tree` a mutually-referencing pair that vanishes entirely
    (neither end has a reachable root)."""
    return _drop_cyclic_agent_edges(_raw_parent_agent_edges(starts_by_agent))


def _reparent_nested_start(
    start_span: dict, starts_by_agent: dict, safe_parent_edges: dict[str, str],
) -> None:
    """A depth>=2 subagent's `subagent.start` nests under its SPAWNING agent's
    owning `subagent.start` segment (the latest such start ingested at/before
    it) when `attributes.parent_agent_id` resolves — the nested-spawn edge
    stamped by lib/trace/claude_subagents. Missing/legacy (no parent_agent_id,
    the edge didn't survive `_nested_parent_agent_edges`'s cycle guard, or the
    parent's start isn't in the window) → left as-is (flat under the main
    thread), preserving prior behavior."""
    parent_agent = (start_span.get('attributes') or {}).get('parent_agent_id')
    if not parent_agent:
        return
    agent_id = (start_span.get('attributes') or {}).get('agent_id')
    if safe_parent_edges.get(agent_id) != parent_agent:
        return
    target = _owning_start_id(starts_by_agent.get(parent_agent, []), start_span)
    if target and target != start_span['span_id']:
        start_span['parent_id'] = target


def _reparent_subagents(out: list[dict]) -> None:
    """Nest subagent-owned spans under their `subagent.start`. Claude Code
    tags every hook payload fired inside a subagent with `agent_id`,
    persisted onto the resulting span; here we redirect any such span onto
    its subagent so the tree becomes prompt → subagent → its tool calls.
    A resumed agent (SendMessage) emits a NEW subagent.start under the SAME
    agent_id, so ownership is per SEGMENT — the latest start preceding the
    span in ingest order. A flat agent_id → last-start map would pull the
    whole history (including the first segment's stop) under the resumed
    start, and the envelope widening would then drag that start's
    start_time back to the first segment — breaking every consumer that
    anchors an elapsed to it.

    A `subagent.start` is not reparented by `agent_id` (that would nest it
    under itself); instead, a nested (depth>=2) start reparents under its
    SPAWNING agent's start via `parent_agent_id` — `_reparent_nested_start`."""
    starts_by_agent = _subagent_starts_by_agent(out)
    if not starts_by_agent:
        return
    safe_parent_edges = _nested_parent_agent_edges(starts_by_agent)
    for s in out:
        if s['name'] == 'subagent.start':
            _reparent_nested_start(s, starts_by_agent, safe_parent_edges)
            continue
        aid = (s.get('attributes') or {}).get('agent_id')
        target = _owning_start_id(starts_by_agent.get(aid, []), s) if aid else None
        if target and target != s['span_id']:
            s['parent_id'] = target


def _span_id_suffix(span_id: str) -> str:
    """The `<uuid[:13]>` tail of a deterministic span id (`prompt-<u13>`,
    `resp-<u13>`, …). Empty for ids without a prefix. The tail is matched
    against a rewind's orphan key set."""
    return span_id.split('-', 1)[1] if '-' in span_id else ''


def _belongs_to_branch(span: dict, keys: set) -> bool:
    """True when `span` is part of a rewind's discarded branch: its
    deterministic-id tail is an orphan key (`prompt-`/`resp-`/`think-`/`cmd-`),
    or its `turn_uuid` belongs to an orphan turn — the path for tool spans,
    which are keyed by their issuing turn rather than their own id. Both match
    against the same `<uuid[:13]>` key set."""
    if _span_id_suffix(span['span_id']) in keys:
        return True
    tu = _turn_uuid_of(span)
    return bool(tu and tu[:13] in keys)


def _stamp_rewound(span: dict, fork_id: str, abandoned_prompt_ids: set) -> None:
    """Mark one span as belonging to a discarded branch; re-parent it under
    the marker when it is one of the branch's abandoned prompt anchors."""
    attrs = dict(span.get('attributes') or {})
    attrs['rewound_away'] = True
    attrs['rewind_fork_id'] = fork_id
    span['attributes'] = attrs
    if span['span_id'] in abandoned_prompt_ids:
        span['parent_id'] = fork_id


def _apply_rewind_marker(out: list[dict], marker: dict) -> None:
    """Stamp `rewound_away` + `rewind_fork_id` onto every span belonging to
    one rewind's discarded branch, and re-parent its abandoned prompt anchors
    under the marker so the branch collapses behind it."""
    a = marker.get('attributes') or {}
    keys = set(a.get('orphan_keys') or ())
    abandoned_prompt_ids = {f'prompt-{k}' for k in (a.get('abandoned_prompt_keys') or ())}
    fork_id = marker['span_id']
    for s in out:
        if s['span_id'] == fork_id or s['name'] in _REWIND_BOUNDARY_NAMES:
            continue
        if _belongs_to_branch(s, keys):
            _stamp_rewound(s, fork_id, abandoned_prompt_ids)


def _mark_rewound_away(out: list[dict]) -> None:
    """Apply every `/rewind` marker present in the window. No-op (one cheap
    scan) on the overwhelming majority of sessions that never rewind."""
    markers = [s for s in out if s['name'] in _REWIND_BOUNDARY_NAMES]
    for marker in markers:
        _apply_rewind_marker(out, marker)


def _graft_orphans(spans: list[dict]) -> list[dict]:
    """Return a copy of `spans` with parentage filled in. Does not mutate
    inputs or any DB row.

    Parentage is resolved deterministically wherever possible, with a
    chronological fallback only for spans that have no turn linkage:
      1. orphan prompts + boundaries → the conversation span (if any)
      2. dangling-parent self-heal
      3. `_ladder_orphans_by_turn` — the PREFERENCE pass: NULL-parent
         spans with a turn_uuid nest under `resp-`/`think-`/prompt anchor
         by deterministic id existence (the write-time parents from
         turn_trace usually mean there's nothing left to do here).
      4. `_graft_orphans_under_prompt` — chronological FALLBACK for the
         spans the ladder couldn't place (attachments, rule.check,
         permission.request, local commands, …): nest under the current
         real prompt, or under `_fallback_orphan_anchor` where a trace has
         no prompt at all. Harmless to the bug-prone spans because step 3 and
         the write-time posters already parented them — this only touches
         genuinely turn-less session events.
      5. `turn`-span lookahead re-attribution
      6. subagent re-parent by agent_id
    """
    if not spans:
        return spans
    out = [dict(s) for s in spans]
    sorted_spans = sorted(out, key=lambda s: s['start_time'])

    conversations = [s for s in out if s['name'] == 'conversation']
    if conversations:
        _graft_conversation_roots(out, conversations[0]['span_id'])

    by_id = {s['span_id']: s for s in out}
    _heal_dangling_parents(out, by_id)
    _ladder_orphans_by_turn(out, by_id)
    _graft_orphans_under_prompt(sorted_spans, by_id, _fallback_orphan_anchor(out))
    _relabel_turns_by_lookahead(out)
    _reparent_subagents(out)
    # Last: override the parentage above for discarded branches so abandoned
    # prompts collapse under their `rewind` marker instead of sitting as
    # conversation roots. Pure no-op when the window has no rewind markers.
    _mark_rewound_away(out)
    return out


def _widen_envelopes(spans: list[dict]) -> list[dict]:
    """Return a copy where every parent span's start/end covers all of its
    children. Pure — does not touch the DB.
    """
    if not spans:
        return spans
    out = [dict(s) for s in spans]
    by_id = {s['span_id']: s for s in out}

    children_by_parent: dict[str, list[str]] = {}
    for s in out:
        pid = s.get('parent_id')
        if pid:
            children_by_parent.setdefault(pid, []).append(s['span_id'])

    for parent_id, child_ids in children_by_parent.items():
        parent = by_id.get(parent_id)
        if not parent:
            continue
        child_spans = [by_id[c] for c in child_ids if c in by_id]
        if not child_spans:
            continue
        child_starts = [c['start_time'] for c in child_spans]
        child_ends = [c.get('end_time') or c['start_time'] for c in child_spans]
        new_start = min(parent['start_time'], *child_starts)
        new_end = max(parent.get('end_time') or parent['start_time'], *child_ends)
        parent['start_time'] = new_start
        parent['end_time'] = new_end
        # Widen start/end for layout, but never shrink a semantic duration.
        # Point-in-time spans (assistant_response / assistant.thinking carry
        # inference latency with start==end) would otherwise have their real
        # duration_ms overwritten by the trivial child-envelope width when a
        # server-side tool nests under them. Children fire during inference,
        # so the envelope never legitimately exceeds the semantic value.
        envelope_ms = int(
            (datetime.fromisoformat(new_end) - datetime.fromisoformat(new_start)).total_seconds() * 1000
        )
        parent['duration_ms'] = max(int(parent.get('duration_ms') or 0), envelope_ms)
    return out


def _persist_projection(conn, trace_id: str, raw: list[dict], projected: list[dict]) -> dict:
    """Apply the differences between `raw` and `projected` back to the DB.

    Returns counts of the writes made. Caller is responsible for commit().
    """
    raw_by_id = {s['span_id']: s for s in raw}
    parent_updates = 0
    envelope_updates = 0

    for s in projected:
        prev = raw_by_id.get(s['span_id'])
        if prev is None:
            continue
        if s.get('parent_id') != prev.get('parent_id'):
            conn.execute(
                "UPDATE session_spans SET parent_id = ? WHERE span_id = ? AND trace_id = ?",
                (s.get('parent_id'), s['span_id'], trace_id),
            )
            parent_updates += 1
        changed_start = s['start_time'] != prev['start_time']
        changed_end = s.get('end_time') != prev.get('end_time')
        if changed_start or changed_end:
            conn.execute(
                """UPDATE session_spans
                   SET start_time = ?, end_time = ?, duration_ms = ?
                   WHERE span_id = ? AND trace_id = ?""",
                (s['start_time'], s.get('end_time'), s.get('duration_ms'),
                 s['span_id'], trace_id),
            )
            envelope_updates += 1
    if parent_updates or envelope_updates:
        # Re-parenting and envelope widening change the served tree without
        # inserting a row, so the read-side memo cannot notice on its own.
        from lib.trace.trace_service import projection_cache
        projection_cache.invalidate(trace_id)
    return {'parent_updates': parent_updates, 'envelope_updates': envelope_updates}


_ACTIVE_GAP_THRESHOLD_MS = 60_000   # gaps ≤ this count as active processing
_ACTIVE_CONTAINER_NAMES = frozenset({
    'prompt', 'task.notification', 'conversation',
    'session.start', 'session.end',
    'compact.pre', 'compact.post', 'rewind',
    'subagent.start', 'subagent.stop',
})


def _compute_active_work_ms(spans: list[dict]) -> int:
    """Sum of short inter-event gaps among operation spans.

    Tool/turn/skill/rule spans in this codebase are point-in-time
    events — PostToolUse hooks fire after the tool returns, so
    `start_time == end_time`. Summing widths therefore yields zero,
    while summing root-prompt widths overcounts (prompts that the
    user walked away from mid-turn end up spanning days).

    Instead we sort the operation events by timestamp and add up
    consecutive gaps that are ≤ `_ACTIVE_GAP_THRESHOLD_MS`. Short
    gaps represent the agent doing something (model thinking, tool
    overhead, hook plumbing); longer gaps mean the user stepped
    away. Container spans (prompts, conversations, subagent markers,
    session lifecycle) are excluded — they're envelopes around the
    real events, not operations themselves.

    Pure — no DB. Caller passes the already-grafted+widened spans for
    consistency with what the UI renders, but the answer only depends
    on operation timestamps.
    """
    if not spans:
        return 0
    has_children: set[str] = set()
    by_id = {s['span_id']: s for s in spans}
    for s in spans:
        pid = s.get('parent_id')
        if pid and pid in by_id:
            has_children.add(pid)
    timestamps: list[datetime] = []
    for s in spans:
        if s['span_id'] in has_children:
            continue
        if s.get('name') in _ACTIVE_CONTAINER_NAMES:
            continue
        try:
            timestamps.append(datetime.fromisoformat(s['start_time']))
        except (TypeError, ValueError):
            continue
    if len(timestamps) < 2:
        return 0
    timestamps.sort()
    total_ms = 0
    for i in range(1, len(timestamps)):
        gap_ms = int((timestamps[i] - timestamps[i - 1]).total_seconds() * 1000)
        if 0 < gap_ms <= _ACTIVE_GAP_THRESHOLD_MS:
            total_ms += gap_ms
    return total_ms


def _build_span_tree(spans: list[dict]) -> list[dict]:
    """Produce the TreeTable-shaped node list the Vue frontend consumes."""
    if not spans:
        return []
    span_lookup = {s['span_id']: s for s in spans}
    children_map: dict[str, list[dict]] = {}
    roots: list[dict] = []
    for span in spans:
        pid = span.get('parent_id')
        if pid and pid in span_lookup:
            children_map.setdefault(pid, []).append(span)
        else:
            roots.append(span)

    for pid in children_map:
        children_map[pid].sort(key=lambda s: (s['start_time'], s['id']))
    roots.sort(key=lambda s: (s['start_time'], s['id']))

    def _to_nodes(spans_list):
        nodes = []
        for span in spans_list:
            child_spans = children_map.get(span['span_id'], [])
            child_nodes = _to_nodes(child_spans) if child_spans else []

            start_time = span['start_time']
            end_time = span['end_time'] or span['start_time']
            for child in child_spans:
                c_start = child['start_time']
                c_end = child.get('end_time') or c_start
                if c_start < start_time:
                    start_time = c_start
                if c_end > end_time:
                    end_time = c_end

            data = {
                **span,
                'start_time': start_time,
                'end_time': end_time,
            }
            if child_nodes:
                data['isGroup'] = True
                data['spans'] = child_spans
            else:
                data['isSpan'] = True

            node = {'key': f"span-{span['id']}", 'data': data}
            if child_nodes:
                node['children'] = child_nodes
            nodes.append(node)
        return nodes

    return _to_nodes(roots)
