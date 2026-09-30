"""Subagent roster built from marker spans over the whole session."""

from __future__ import annotations

import json

from datetime import datetime
from lib.trace.pending_spans import (
    AGENT_ID_SQL, INACTIVE_THRESHOLD_SEC as _AGENT_STALE_AFTER_SEC,
    parse_naive_ts as _roster_ts,
)


# ── Session agent roster (whole-session, window-independent) ─────────
# The /live tail is a paged window, so a client-side roster tally silently
# drops agents whose markers sit outside the loaded window — and "ghost"
# agents whose markers were lost to an ingest outage entirely (only their
# agent_id-tagged internal spans survive). Roster truth comes from the
# server over ALL spans; tail spans come from the window; "roster row
# exists but spans not loaded" is a designed state the client renders.

_AGENT_MARKER_NAMES = ('subagent.start', 'subagent.stop')
# Markers plus the launch span — the main timeline's representation of a
# subagent, never part of its internal span count.
_AGENT_SCOPE_MARKER_NAMES = _AGENT_MARKER_NAMES + ('tool.Agent',)


def _in_placeholders(names) -> str:
    return ','.join('?' for _ in names)


def _row_attrs(raw) -> dict:
    try:
        parsed = json.loads(raw) if raw else {}
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _roster_rows(trace_id: str):
    """(activity rows, marker rows, launch rows, session ended).

    ONE grouped pass over the indexed agent_id column feeds BOTH the roster
    badge and the phase verdict — the main bucket (aid IS NULL) is included so
    the phase needn't re-scan. Each row also carries `perm_ts`/`ask_ts` (newest
    PENDING permission / ask-or-plan span), so the waiting-on-a-human check
    reuses the same rows instead of a second GROUP BY.

    PENDING placeholders count toward `last_ts` — a live agent mid-way through a
    long tool has only its placeholder as fresh activity, and dropping it would
    stale a genuinely-running agent — but stay OUT of `internal_count` (the
    append-only store keeps resolved placeholders, so counting them inflates
    span_count).

    `last_key` names the agent's newest STEP as `<start_time>#<padded id>`, the
    same span-time-then-ingest-id ordering `_marker_order` uses and for the same
    reason: `assistant_response`/`assistant.thinking` have no hook and arrive in
    a transcript-rescan batch, so they carry the highest ids in the session
    while sitting EARLIER in time than the live tool they precede. Picking the
    newest by id alone made a running agent's card say "Thinking" while its
    timeline showed the shell command actually in flight. The composite is one
    string in a pass this query already makes — SQLite has no `max_by`, and a
    correlated per-agent lookup is the most expensive read on the trace view."""
    from lib.orm.engine import get_connection

    from lib.trace.trace_service import projection_cache

    scope_ph = _in_placeholders(_AGENT_SCOPE_MARKER_NAMES)
    marker_ph = _in_placeholders(_AGENT_MARKER_NAMES)
    conn = get_connection()
    try:
        # These four reads are a pure function of the stored rows — the
        # clock-dependent part of the roster is `_roster_status`, which runs on
        # the RESULT and stays live. Memoising here keeps a badge that stales
        # on age alone honest while dropping the per-poll `json_extract` scan
        # over every span's attributes (the single most expensive query on the
        # trace view's 4s reconcile).
        key = ('roster', trace_id,
               projection_cache.fingerprint(conn, [trace_id]))
        hit = projection_cache.get(key)
        if hit is not None:
            return hit
        act = conn.execute(
            f"SELECT {AGENT_ID_SQL} AS aid, "
            "       MIN(start_time) AS first_ts, MAX(start_time) AS last_ts, "
            f"      SUM(CASE WHEN name IN ({scope_ph}) OR status_code = 'PENDING' "
            "                THEN 0 ELSE 1 END) AS internal_count, "
            "       MAX(start_time || '#' || printf('%012d', id)) AS last_key, "
            "       MAX(json_extract(attributes,'$.agent_type')) AS agent_type, "
            "       MAX(CASE WHEN status_code = 'PENDING' "
            "                 AND (name = 'permission.request' OR span_id LIKE 'permreq-%') "
            "                THEN start_time END) AS perm_ts, "
            "       MAX(CASE WHEN status_code = 'PENDING' "
            "                 AND name IN ('tool.AskUserQuestion','tool.ExitPlanMode') "
            "                THEN start_time END) AS ask_ts "
            "  FROM session_spans "
            " WHERE trace_id = ? "
            " GROUP BY aid",
            (*_AGENT_SCOPE_MARKER_NAMES, trace_id)).fetchall()
        marks = conn.execute(
            "SELECT id, span_id, name, start_time, attributes FROM session_spans "
            f" WHERE trace_id = ? AND name IN ({marker_ph}) "
            " ORDER BY id ASC",
            (trace_id, *_AGENT_MARKER_NAMES)).fetchall()
        launches = conn.execute(
            "SELECT id, span_id, status_code, start_time, end_time, attributes "
            "  FROM session_spans WHERE trace_id = ? AND name = 'tool.Agent' "
            " ORDER BY id ASC",
            (trace_id,)).fetchall()
        sess = conn.execute(
            'SELECT status FROM sessions WHERE trace_id = ?',
            (trace_id,)).fetchone()
        last_spans = _last_span_rows(
            conn, [i for i in (_last_step_id(r['last_key']) for r in act
                               if r['aid']) if i])
    finally:
        conn.close()
    parsed_launches = [
        {'span_id': r['span_id'], 'status_code': r['status_code'],
         'ts': _roster_ts(r['start_time']), 'end': r['end_time'],
         'attrs': _row_attrs(r['attributes'])}
        for r in launches
    ]
    ended = bool(sess and sess['status'] == 'ended')
    rows = (_with_last_spans(act, last_spans), marks, parsed_launches, ended)
    projection_cache.put(key, rows, (trace_id,))
    return rows


# What a subagent card can say about the agent's newest span: enough to name
# the step without the span body. `preview` is the tool's own headline —
# command, file, or launch description — and empty when the span has none.
_LAST_SPAN_PREVIEW_KEYS = ('command_preview', 'file_path', 'description',
                           'pattern', 'query', 'url')


def _last_step_id(last_key) -> int | None:
    """The span id inside a `<start_time>#<padded id>` composite, or None."""
    if not isinstance(last_key, str) or '#' not in last_key:
        return None
    try:
        return int(last_key.rsplit('#', 1)[1])
    except ValueError:
        return None


def _last_span_rows(conn, ids: list[int]) -> dict[int, dict]:
    """id → the newest span of each agent, as the roster's `last_span`.

    A marker (`subagent.start`/`subagent.stop`) is the parent's account of the
    agent, not a step it took, so an agent whose newest row is its own stop
    marker reports no last span rather than "subagent.stop".
    """
    if not ids:
        return {}
    ph = ','.join('?' * len(ids))
    rows = conn.execute(
        "SELECT id, name, status_code, start_time, attributes "
        f"  FROM session_spans WHERE id IN ({ph})", ids).fetchall()
    out: dict[int, dict] = {}
    for r in rows:
        if r['name'] in _AGENT_MARKER_NAMES:
            continue
        a = _row_attrs(r['attributes'])
        preview = next((a[k] for k in _LAST_SPAN_PREVIEW_KEYS
                        if isinstance(a.get(k), str) and a[k]), '')
        out[r['id']] = {
            'name': r['name'], 'status_code': r['status_code'],
            'start_time': r['start_time'], 'preview': preview[:120],
        }
    return out


def _with_last_spans(act, last_spans: dict[int, dict]) -> list[dict]:
    """The grouped activity rows as dicts, each carrying its `last_span`."""
    out = []
    for r in act:
        d = dict(r)
        d['last_span'] = last_spans.get(_last_step_id(d.get('last_key')))
        out.append(d)
    return out


def _waiting_from_activity(row) -> str | None:
    """Newest PENDING ask/permission timestamp for one agent — the later of the
    row's `perm_ts`/`ask_ts`. `_roster_entry_base` compares it against the
    agent's newest span to decide whether it is blocked on a human."""
    cands = [t for t in (row['perm_ts'], row['ask_ts']) if t]
    if not cands:
        return None
    return max(cands, key=lambda t: _roster_ts(t) or datetime.min)


def _marker_order(m) -> tuple:
    """Span-time ordering with ingest-id tiebreak. The raw store's marker
    times are authoritative here (unlike the client's merge-rewritten view),
    and a REPAIRED marker (reconstruct_subagent_markers) is ingested long
    after the events it represents — id-only ordering would let a
    synthesized start float past the real stop that closes it."""
    ts = _roster_ts(m['start_time'])
    return (ts or datetime.min, m['id'])


def _roster_segments(marks) -> dict:
    """agent_id → {'starts': [...], 'stops': [...]}, each time-ordered."""
    seg: dict[str, dict] = {}
    for r in marks:
        a = _row_attrs(r['attributes'])
        aid = a.get('agent_id')
        if not aid:
            continue
        entry = seg.setdefault(aid, {'starts': [], 'stops': []})
        key = 'starts' if r['name'] == 'subagent.start' else 'stops'
        entry[key].append({'id': r['id'], 'span_id': r['span_id'],
                           'start_time': r['start_time'], 'attrs': a})
    for entry in seg.values():
        entry['starts'].sort(key=_marker_order)
        entry['stops'].sort(key=_marker_order)
    return seg


def _roster_latest_segment(entry):
    """(latest_start, matching_stop). Segment pairing — a resumed agent
    re-starts under the SAME agent_id, so the stop that closes a segment is
    the first one ordered at/after its start; an agent whose start marker
    was lost may still have a stop (start-less segment)."""
    starts, stops = entry['starts'], entry['stops']
    if not starts:
        return None, (stops[-1] if stops else None)
    latest = starts[-1]
    key = _marker_order(latest)
    stop = next((x for x in stops if _marker_order(x) >= key), None)
    return latest, stop


def _launch_type(attrs) -> str | None:
    ti = attrs.get('tool_input')
    return attrs.get('subagent_type') or (
        ti.get('subagent_type') if isinstance(ti, dict) else None)


def _launch_resolved(launch) -> bool:
    """True when a `tool.Agent` launch cleanly completed: `OK` (never
    `PENDING`/`ERROR`), not `denied`, and carrying an `end_time` — the Task
    call it represents only resolves once the subagent it spawned finished, so
    this is proof of completion even when that agent's own `subagent.stop`
    marker was lost. A denied/`ERROR` launch is the interrupted signal
    (`_roster_interrupted`), not this one."""
    if launch['attrs'].get('denied'):
        return False
    return launch['status_code'] == 'OK' and bool(launch.get('end'))


def _launch_for_tu(launches, tu):
    """The non-error launch (the PENDING placeholder) sharing a deny's
    tool_use_id — the deny's only trustworthy anchor."""
    if not tu:
        return None
    for r in launches:
        if r['status_code'] == 'ERROR' or r['attrs'].get('denied'):
            continue
        if r['attrs'].get('tool_use_id') == tu:
            return r
    return None


def _anchored_start(open_entries, launch, taken):
    """The start this launch actually spawned: the nearest same-type start
    at/after the launch's own timestamp."""
    typ = _launch_type(launch['attrs'])
    lts = launch['ts']
    if not typ or not lts:
        return None
    best, best_dt = None, None
    for e in open_entries:
        if e['agent_id'] in taken or e['agent_type'] != typ:
            continue
        if not e['start_ts'] or e['start_ts'] < lts:
            continue
        dt = (e['start_ts'] - lts).total_seconds()
        if best is None or dt < best_dt:
            best, best_dt = e, dt
    return best


def _roster_interrupted(launches, open_entries) -> set[str]:
    """agent_ids whose launch was denied/killed. A deny claims ONLY through
    its anchor chain — deny tool_use_id → the launch placeholder sharing it
    → the start that launch spawned. An unanchored deny (its agent never
    started) claims nothing: guessing by nearest-same-type could mark an
    innocent running agent interrupted."""
    out: set[str] = set()
    for r in launches:
        if r['status_code'] != 'ERROR' and not r['attrs'].get('denied'):
            continue
        launch = _launch_for_tu(launches, r['attrs'].get('tool_use_id'))
        if launch is None:
            continue
        best = _anchored_start(open_entries, launch, out)
        if best:
            out.add(best['agent_id'])
    return out


class _EveryType:
    """Set-like that contains every agent_type. An unresolved launch that names
    no `subagent_type` (Kimi omits it whenever the call didn't pass one) could
    have spawned ANY still-open agent, so no type is safely attributable."""

    def __contains__(self, _agent_type) -> bool:
        return True


def _ambiguous_launch_types(launches):
    """agent_types whose implicit-stop attribution is unsafe: any type with a
    `tool.Agent` launch that has NOT cleanly resolved (still PENDING, or
    ERROR/denied). Only when EVERY same-type launch resolved is the launch↔agent
    mapping unambiguous — see `_apply_implicit_stops`. An unresolved launch of
    UNKNOWN type poisons every type: it is exactly the running agent the
    heuristic must not convict."""
    resolved_by_type: dict = {}
    for r in launches:
        typ = _launch_type(r['attrs'])
        if not typ:
            if not _launch_resolved(r):
                return _EveryType()
            continue
        resolved_by_type[typ] = (
            resolved_by_type.get(typ, True) and _launch_resolved(r))
    return {t for t, ok in resolved_by_type.items() if not ok}


def _apply_implicit_stops(launches, open_entries, interrupted_ids) -> None:
    """Give a still-open agent (start marker, no `subagent.stop`) an implicit
    stop derived from its resolved `tool.Agent` launch's `end_time`, so a lost
    `subagent.stop` doesn't strand a finished agent showing 'running'.

    No launch→agent_id identity link exists — the launch span carries the
    Task's `tool_use_id` but not the resulting `agent_id`; the `subagent.start`
    marker carries `agent_id` but not that `tool_use_id` — so a launch can only
    be tied to an entry by same-type proximity, which is ambiguous while a
    same-type sibling is still running (nearest-start can convict the wrong,
    genuinely-running agent). We therefore REFUSE under ambiguity: no open entry
    of a type with any unresolved same-type launch is demoted (it stays running
    until a real stop lands or it goes stale). Only when every same-type launch
    has resolved is each open entry provably some resolved launch's agent, and we
    stamp each from its nearest resolved launch. Trade-off: a finished agent may
    show 'running' longer, but a running agent is never shown 'finished' — the
    far milder error. An `interrupted` agent is also skipped (`_roster_status`
    checks `stop` before `interrupted`, so this must not stomp that verdict)."""
    ambiguous = _ambiguous_launch_types(launches)
    eligible = [
        e for e in open_entries
        if e['agent_id'] not in interrupted_ids
        and e['agent_type'] not in ambiguous
    ]
    taken: set[str] = set()
    for r in launches:
        if not _launch_resolved(r):
            continue
        best = _anchored_start(eligible, r, taken)
        if best is None:
            continue
        taken.add(best['agent_id'])
        best['stop'] = {'span_id': None, 'start_time': r['end'], 'attrs': {}}


def _launch_description(attrs) -> str:
    ti = attrs.get('tool_input')
    return attrs.get('description') or (
        ti.get('description') if isinstance(ti, dict) else '') or ''


def _roster_description(launches, agent_type, start_ts, claimed) -> str:
    """The launch description for one agent: nearest same-type tool.Agent
    launch not yet claimed by another agent (mirrors the client's
    useAgentLaunchMerge pairing)."""
    best, best_dt = None, None
    for r in launches:
        if r['span_id'] in claimed or _launch_type(r['attrs']) != agent_type:
            continue
        if not _launch_description(r['attrs']):
            continue
        dt = abs((r['ts'] - start_ts).total_seconds()) \
            if (r['ts'] and start_ts) else 0.0
        if best is None or dt < best_dt:
            best, best_dt = r, dt
    if not best:
        return ''
    claimed.add(best['span_id'])
    return _launch_description(best['attrs'])


def _entry_agent_type(latest, stop, act) -> str:
    for src in (latest, stop):
        typ = src['attrs'].get('agent_type') if src else None
        if typ:
            return typ
    return act.get('agent_type') or 'agent'


def _entry_marker_facts(latest, stop) -> dict:
    attrs = latest['attrs'] if latest else {}
    # Workflow-run markers carry the full task prompt (no hook-truncated
    # preview) — clip it to preview size rather than surfacing nothing.
    preview = attrs.get('prompt_preview') or (attrs.get('prompt') or '')[:400]
    return {
        'start_span_id': latest['span_id'] if latest else None,
        'description': attrs.get('description') or attrs.get('label') or '',
        'prompt_preview': preview,
        'marker_seen': (stop or latest or {}).get('start_time'),
    }


def _roster_is_waiting(ask_seen, last_seen) -> bool:
    """Blocked on a human: the newest PENDING ask/permission IS the agent's
    newest span (a later resolved span means the ask was answered)."""
    ask_ts = _roster_ts(ask_seen)
    seen_ts = _roster_ts(last_seen)
    return bool(ask_ts and seen_ts and ask_ts >= seen_ts)


def _roster_first_start_ts(seg_entry):
    starts = (seg_entry or {}).get('starts') or []
    return _roster_ts(starts[0]['start_time']) if starts else None


def _roster_declared_done(latest) -> bool:
    return bool(latest and latest['attrs'].get('state') == 'done')


def _roster_entry_base(aid, seg_entry, activity, waiting) -> dict:
    """Segment + activity facts for one agent, before status/description."""
    latest, stop = _roster_latest_segment(
        seg_entry or {'starts': [], 'stops': []})
    act = activity.get(aid) or {}
    facts = _entry_marker_facts(latest, stop)
    # `started_at` anchors the LIVE elapsed, so it stays on the current run.
    # How long the agent took overall is a different question, and a resumed
    # one answers it from its first run — see `_roster_output_entry`.
    started_at = latest['start_time'] if latest else act.get('first_ts')
    last_seen = act.get('last_ts') or facts['marker_seen']
    return {
        'agent_id': aid,
        'agent_type': _entry_agent_type(latest, stop, act),
        'started_at': started_at,
        'start_ts': _roster_ts(started_at),
        'stop': stop,
        'start_span_id': facts['start_span_id'],
        'first_start_ts': _roster_first_start_ts(seg_entry),
        'last_seen': last_seen,
        'span_count': int(act.get('internal_count') or 0),
        'description': facts['description'],
        'prompt_preview': facts['prompt_preview'],
        'waiting': _roster_is_waiting(waiting.get(aid), last_seen),
        'last_span': act.get('last_span'),
        # Workflow-run ingest records completion as `state: done` on the start
        # marker itself — those agents never get a subagent.stop span.
        'declared_done': _roster_declared_done(latest),
    }


def _roster_status(entry, interrupted_ids, now, ended) -> str:
    if entry['stop'] or entry.get('declared_done'):
        return 'finished'
    if entry['agent_id'] in interrupted_ids:
        return 'interrupted'
    # An ended session cannot have a running (or waiting) agent — an
    # unstopped one is stale immediately.
    if ended:
        return 'stale'
    if entry['waiting']:
        return 'waiting'
    # Age alone stales an unstopped agent: a genuinely-running one always
    # has fresh activity — resolved spans or PENDING placeholders, both of
    # which advance last_ts — so 10 min of total silence means dead, even
    # when the agent's last span is the newest thing in the session.
    seen = _roster_ts(entry['last_seen'])
    if seen and (now - seen).total_seconds() > _AGENT_STALE_AFTER_SEC:
        return 'stale'
    return 'running'


def _roster_output_entry(e, status, launches, claimed) -> dict:
    stop_ts = _roster_ts(e['stop']['start_time']) if e['stop'] else None
    # From the agent's FIRST run, not its current one: a resumed agent's last
    # segment is minutes of a run that took an hour, and the sheet presents
    # this as how long the agent took. Identical for the single-run majority.
    began = e.get('first_start_ts') or e['start_ts']
    duration_ms = None
    if stop_ts and began:
        duration_ms = max(0, int((stop_ts - began).total_seconds() * 1000))
    result_preview = e['stop']['attrs'].get('result_preview') if e['stop'] else ''
    return {
        'agent_id': e['agent_id'],
        'agent_type': e['agent_type'],
        'description': e['description'] or _roster_description(
            launches, e['agent_type'], e['start_ts'], claimed),
        'status': status,
        'started_at': e['started_at'],
        'last_seen': e['last_seen'],
        'duration_ms': duration_ms,
        'result_preview': result_preview or '',
        'start_span_id': e['start_span_id'],
        'span_count': e['span_count'],
        'prompt_preview': e['prompt_preview'],
        'last_span': e.get('last_span'),
    }


def _waiting_map(act_rows) -> dict:
    """agent_id → newest PENDING ask/permission ts, for subagents that have
    one. Derived from the shared rows' perm_ts/ask_ts (no extra query)."""
    out: dict = {}
    for r in act_rows:
        w = _waiting_from_activity(r) if r['aid'] else None
        if w:
            out[r['aid']] = w
    return out


def _agents_that_ran(act_rows, segments) -> list:
    """Subagent activity rows, minus agent_ids that never ran.

    An agent_id with no marker and no resolved span is a Claude Code side
    fork whose PreToolUse fired for a call the harness never executed (see
    `pending_spans.never_ran_blocker_agents`). A ghost whose markers were lost still has
    resolved spans, so it stays."""
    return [r for r in act_rows
            if r['aid'] and (r['aid'] in segments or r['internal_count'])]


def _build_roster(act_rows, marks, launches, ended) -> list[dict]:
    """Assemble roster entries from the shared grouped rows. `waiting` (newest
    PENDING ask/permission per agent) is derived from each row's perm_ts/ask_ts
    rather than a separate query."""
    segments = _roster_segments(marks)
    ran = _agents_that_ran(act_rows, segments)
    activity = {r['aid']: dict(r) for r in ran}
    waiting = _waiting_map(ran)
    entries = [
        _roster_entry_base(aid, segments.get(aid), activity, waiting)
        for aid in set(activity) | set(segments)
    ]
    entries.sort(key=lambda e: e['started_at'] or '')
    open_entries = [e for e in entries if not e['stop']]
    interrupted = _roster_interrupted(launches, open_entries)
    _apply_implicit_stops(launches, open_entries, interrupted)
    now = datetime.now()
    claimed: set[str] = set()
    return [
        _roster_output_entry(
            e, _roster_status(e, interrupted, now, ended),
            launches, claimed)
        for e in entries
    ]


def _roster_with_activity(trace_id: str) -> tuple:
    """(roster, per-agent activity map incl. main under None, ended) from ONE
    grouped pass. The activity map feeds the phase verdict so it needn't
    re-scan session_spans a third time per poll.

    Roster: one entry per agent_id, from marker segments (ingest-id ordered)
    plus orphan agents whose markers were lost but whose agent_id-tagged spans
    survive. Status mirrors the client rules: running / finished / interrupted
    (deny markers) / stale (silent past the stale window while the main agent
    moved on), evaluated with server time."""
    act_rows, marks, launches, ended = _roster_rows(trace_id)
    activity = {r['aid']: dict(r) for r in act_rows}
    if not act_rows and not marks:
        return [], activity, ended
    roster = _build_roster(act_rows, marks, launches, ended)
    _fill_workflow_agent_descriptions(trace_id, roster)
    return roster, activity, ended


def _fill_workflow_agent_descriptions(trace_id: str, roster: list) -> None:
    """Fill description-less workflow-subagent entries with their script
    label and task prompt, joined from the captured run trace. No-op when
    nothing is missing or the run trace isn't ingested yet."""
    def needs_fill(e):
        return e['agent_type'] == 'workflow-subagent' and not e['description']
    if not any(needs_fill(e) for e in roster):
        return
    from lib.trace.workflow_labels import workflow_agent_attrs
    agent_attrs = workflow_agent_attrs(trace_id)
    for e in roster:
        src = agent_attrs.get(e['agent_id'])
        if not needs_fill(e) or not src:
            continue
        e['description'] = src.get('label') or e['description']
        if not e['prompt_preview'] and src.get('prompt'):
            e['prompt_preview'] = src['prompt'][:400]
