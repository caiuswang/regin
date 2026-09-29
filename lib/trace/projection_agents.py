"""Subagent id normalization and the per-agent rollups (extent, tokens, result,
segments) stamped onto launch spans."""

from __future__ import annotations

import re
from datetime import datetime


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


# Marks the turn a subagent trace derives from its own start marker, so the
# orphan graft can anchor on it (`_fallback_orphan_anchor`) without mistaking a
# NESTED agent's launch prompt for the trace's own.
AGENT_ROOT_KEY = 'agent_root'


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
