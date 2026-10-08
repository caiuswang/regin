"""`GET /api/session-switcher` — the roster behind the ⌘J session switcher.

One payload feeds two surfaces: the palette's Sessions facet (with an empty
query) and the status rail pinned under every trace view. Both want the same
answer — *which sessions are live, what state is each in, and what is the last
thing it did* — so they share one request rather than each growing its own.

The state verdict is NOT re-derived here: it is the server phase from
`sessions.py` (`_session_phase`), the same verdict `/live` renders, mapped into
the switcher's shorter vocabulary by the client. Adding a second definition of
"is this session working" is how the two surfaces would drift apart.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

from flask import jsonify, request

from lib.activity_log import get_activity_logger
from lib.fulltext.span_docs import category_of, span_body
from lib.orm.engine import get_connection
from lib.orm.models import Session as SessionModel
from lib.orm import SessionLocal
from lib.palette.rows import session_rows
from lib.trace.alias import aliased_run_ids, trace_group
from lib.trace.pending_spans import HUMAN_BLOCKER_NAMES
from web.blueprints.trace import trace_bp
from web.blueprints.trace.sessions import (
    _RUN_ORIGINS,
    _alias_group_ids,
    _owned_run_phase,
    _roster_with_activity,
    _session_phase,
    _session_repo_name,
)

log = get_activity_logger("live_session")

# How many sessions the switcher offers. The list is a *choose* surface, not a
# history browser — 1–9 are the hotkeys, and the rail has to fit on one row.
DEFAULT_LIMIT = 12
MAX_LIMIT = 24
# How many rows to phase before trimming to `limit`. Recency alone is the wrong
# cut: the surface whose job is "who is waiting on you" must not drop a session
# parked on a question just because three others were touched more recently. So
# read a wider window, phase it, put the waiting and the working first, and
# only then take `limit`. The factor is bounded because phasing is per-session.
CANDIDATE_FACTOR = 3
MAX_CANDIDATES = 40
# Tail rows per session. Enough for the preview to read as a conversation,
# short enough that N sessions stay one cheap query each.
TAIL_ROWS = 7
# A one-line body is the row's "current activity"; anything longer is the
# preview's problem.
LINE_CLIP = 160
# How short a trace-id prefix may be before it stops being an address. Four
# characters is already ~1-in-65k over a uuid, and anything shorter answers
# a keystroke that is still mid-paste with a screenful of unrelated rows.
MIN_ID_CHARS = 4
# The cap on an id lookup. A prefix usually names one session; the bound is
# only there for a four-character stub.
ID_LOOKUP_LIMIT = 12

# Which phases outrank recency, and in what order. This MUST order the same way
# the client's `STATE_RANK` does (frontend/src/utils/sessionSwitcher.js): the
# client folds these eight phases into five states and re-sorts by that fold,
# so a disagreement here would have the server keep one session past the cap
# and the client rank a different one first. Read down the column:
#   waiting-* -> awaiting | working/stopping -> working | idle -> idle
#   starting  -> starting | inactive-stale -> inactive | ended -> ended
# `idle` is its own state, above `starting`: a run regin owns that is sitting
# at the prompt is alive and attachable, where a starting one has nothing to
# attach to yet.
# `test_priority_covers_every_phase` pins the exhaustiveness half of that.
_PRIORITY = ('waiting-permission', 'waiting-input', 'retrying',
             'working', 'stopping', 'idle',
             'starting', 'inactive-stale', 'ended')

# The spans that mean "stopped, waiting for a human". Mirrors the perm_ts /
# ask_ts pair `_roster_rows` groups on — same names, same PENDING rule.
_BLOCKER_NAMES = HUMAN_BLOCKER_NAMES
# Attributes worth quoting back as "what it wants", newest blocker first.
_NEEDS_KEYS = ('question', 'prompt', 'plan', 'description', 'command', 'tool_name')


def _attrs(raw) -> dict:
    try:
        parsed = json.loads(raw) if raw else {}
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _clip(text: str, limit: int = LINE_CLIP) -> str:
    flat = ' '.join(str(text or '').split())
    return flat if len(flat) <= limit else flat[:limit - 1] + '…'


def _tail_for(conn, group: list[str]) -> list[dict]:
    """The session's last `TAIL_ROWS` spans, oldest-first, as `{ts, kind, text}`.

    PENDING placeholders are kept deliberately — unlike the aggregate readers,
    this is a *liveness* view, and the placeholder for a tool call still
    running is the truest answer to "what is it doing right now".
    """
    marks = ','.join('?' for _ in group)
    rows = conn.execute(
        "SELECT name, start_time, status_code, attributes FROM session_spans "
        f" WHERE trace_id IN ({marks}) "
        " ORDER BY start_time DESC, id DESC LIMIT ?",
        (*group, TAIL_ROWS)).fetchall()
    out = []
    for row in reversed(rows):
        attrs = _attrs(row['attributes'])
        out.append({
            'ts': row['start_time'],
            'name': row['name'],
            'kind': category_of(row['name']),
            'pending': row['status_code'] == 'PENDING',
            'text': _clip(span_body(row['name'], attrs)),
        })
    return out


def _needs_for(conn, group: list[str]) -> str | None:
    """What a waiting session is waiting for — the newest PENDING blocker's
    own words, so the facet reads as an inbox rather than as a status list."""
    marks = ','.join('?' for _ in group)
    names = ','.join('?' for _ in _BLOCKER_NAMES)
    from lib.trace.pending_spans import AGENT_ID_SQL, never_ran_pending_agents
    forks = sorted({a for _, a in never_ran_pending_agents(conn, group)})
    skip = (f" AND ({AGENT_ID_SQL} IS NULL OR {AGENT_ID_SQL} NOT IN "
            f"({','.join('?' for _ in forks)}))") if forks else ''
    row = conn.execute(
        "SELECT name, attributes FROM session_spans "
        f" WHERE trace_id IN ({marks}) AND status_code = 'PENDING' "
        f"   AND (name IN ({names}) OR span_id LIKE 'permreq-%'){skip} "
        " ORDER BY start_time DESC, id DESC LIMIT 1",
        (*group, *_BLOCKER_NAMES, *forks)).fetchone()
    if not row:
        return None
    attrs = _attrs(row['attributes'])
    for key in _NEEDS_KEYS:
        value = attrs.get(key)
        if isinstance(value, str) and value.strip():
            return _clip(value, 240)
    return _clip(span_body(row['name'], attrs), 240) or row['name']


def _row_select():
    """The column set both readers project. One list, because a column added
    for the roster and missed by the id lookup would render a row that is
    silently missing its context bar or its token counts."""
    from sqlmodel import select as _select
    return _select(
        SessionModel.trace_id, SessionModel.title, SessionModel.status,
        SessionModel.cwd, SessionModel.prompts, SessionModel.span_count,
        SessionModel.started_at, SessionModel.last_start_at,
        SessionModel.last_seen, SessionModel.model,
        SessionModel.peak_context_tokens,
        SessionModel.peak_main_context_tokens,
        SessionModel.live_context_tokens,
        SessionModel.input_tokens, SessionModel.output_tokens,
    ).where(SessionModel.is_test == 0)


def _like_prefix(raw: str) -> str:
    """`raw` as a LIKE prefix pattern with the wildcards it may contain
    neutralised — a pasted id is a literal, and `_` in particular is a
    single-character wildcard that a hand-written trace id can legitimately
    carry."""
    escaped = raw.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')
    return escaped + '%'


def _fold_aliases(matched: list, canon: dict, limit: int) -> list:
    """`matched` collapsed onto one row per canonical session, in the recency
    order it arrived in, then capped."""
    wanted: list[str] = []
    matched_for: dict[str, object] = {}
    for row in matched:
        cid = canon[row.trace_id]
        if cid in matched_for:
            continue
        wanted.append(cid)
        matched_for[cid] = row
    wanted = wanted[:limit]

    by_id = {row.trace_id: row for row in matched}
    missing = [cid for cid in wanted if cid not in by_id]
    if missing:
        with SessionLocal() as session:
            for row in session.exec(_row_select().where(
                    SessionModel.trace_id.in_(missing))).all():
                by_id[row.trace_id] = row
    # A canonical id with no `sessions` row of its own — a run whose child has
    # not registered yet — falls back to the row that actually matched, which
    # is still a working address.
    return [by_id.get(cid) or matched_for[cid] for cid in wanted]


def _rows_by_id_prefix(prefix: str, limit: int) -> list:
    """Sessions whose trace id starts with `prefix`, newest-seen first.

    Deliberately skips `_candidate_rows`' curation. That filter exists because
    the roster is a *choose* surface and has to be worth choosing from; a typed
    id is an address, and an address must resolve to whatever it names — a
    finished run, a session with no cwd, the row you were just looking at.
    Only `is_test` stays out, and that is a fixture, not a session.

    Aliases are RESOLVED, not merely deduplicated: a regin-launched run carries
    spans under two trace ids and either is a legitimate thing to paste, so a
    hit on the runner-side id answers with the row its session is really keyed
    on. Returning the runner's row would render the right link under the wrong
    title, turn count and span count.

    The SQL limit is deliberately loose and the real cap is applied after the
    fold: trimming first would let an alias pair silently cost the page a row.
    """
    with SessionLocal() as session:
        stmt = (_row_select()
                .where(SessionModel.trace_id.like(_like_prefix(prefix),
                                                  escape='\\'))
                .order_by(SessionModel.last_seen.desc())
                .limit(limit * 2))
        matched = list(session.exec(stmt).all())
    conn = get_connection()
    try:
        canon = {row.trace_id: trace_group(conn, row.trace_id)[0]
                 for row in matched}
    finally:
        conn.close()

    return _fold_aliases(matched, canon, limit)


def _candidate_rows(limit: int) -> list:
    """Recent real interactive sessions, newest-seen first.

    Runs (a `_RUN_ORIGINS` origin) and test fixtures are out: neither is
    something a human attaches to. Alias rows are dropped so a regin-launched
    run appears once, under the id every other reader already uses.

    A session with no `cwd` is out too, and that one is about what "attach"
    MEANS: the switcher hands you a working session, and a row that never
    recorded a working directory is not one you can be inside of. In practice
    these are spans POSTed by hand against a made-up trace id — they carry no
    prompt, no repo and a handful of spans, and left in they crowd out real
    sessions at the cap. The full session LIST still shows them; it is a
    record of everything traced, which is a different question.

    The origin test is `NULL or not-a-run`, never `== 'session'`: the column
    was added late and every session traced before it reads NULL, so equality
    would hide the entire back catalogue. Same rule as the list endpoint's
    `workflow=hide`.
    """
    from sqlmodel import or_ as _or
    with SessionLocal() as session:
        hidden = aliased_run_ids(session)
        stmt = (
            _row_select()
            .where(SessionModel.cwd.is_not(None))
            .where(_or(SessionModel.origin.is_(None),
                       SessionModel.origin.not_in(_RUN_ORIGINS)))
        )
        if hidden:
            stmt = stmt.where(SessionModel.trace_id.notin_(hidden))
        stmt = stmt.order_by(SessionModel.last_seen.desc()).limit(limit)
        return list(session.exec(stmt).all())


def _context_pct(row) -> float | None:
    from lib.tokens.model_windows import infer_window
    peak = row.peak_context_tokens
    if not isinstance(peak, int):
        return None
    window = infer_window(row.model, peak)
    if not window:
        return None
    live = next((v for v in (row.live_context_tokens,
                             row.peak_main_context_tokens, peak)
                 if isinstance(v, int)), peak)
    return round(live * 100.0 / window, 1)


def _phase_of(row) -> str:
    """The server's own verdict for one session — the ranking key, and the only
    thing the trimmed-away candidates are read for."""
    roster, activity, ended = _roster_with_activity(row.trace_id)
    phase, _agent_phase = _session_phase(
        activity, roster, row.status == 'ended' or ended,
        _owned_run_phase(row.trace_id))
    return phase


def _rank(phase: str) -> int:
    return _PRIORITY.index(phase) if phase in _PRIORITY else len(_PRIORITY)


def _row_payload(conn, row, phase: str, *, with_tail: bool) -> dict:
    group = _alias_group_ids(row.trace_id)
    waiting = phase in ('waiting-permission', 'waiting-input')
    return {
        'trace_id': row.trace_id,
        'title': row.title,
        'repo': _session_repo_name(row.trace_id, row.cwd),
        'cwd': row.cwd,
        'phase': phase,
        'status': row.status,
        'turn': row.prompts,
        'span_count': row.span_count,
        'started_at': row.started_at,
        'last_start_at': row.last_start_at,
        'last_seen': row.last_seen,
        'input_tokens': row.input_tokens,
        'output_tokens': row.output_tokens,
        'context_pct': _context_pct(row),
        'needs': _needs_for(conn, group) if waiting else None,
        # Two thirds of this payload, and only the ⌘J palette's preview pane
        # reads it — one row at a time, while the palette is open. The rail
        # that holds the ambient 10s poll never touches it, so it is asked for
        # rather than assumed.
        **({'tail': _tail_for(conn, group)} if with_tail else {}),
    }


def _with_rows(conn, sessions: list[dict]) -> list[dict]:
    """Each roster row's `PaletteRow`, built for the page in one batch and
    phased by the verdict the roster already ranked on. A builder failure
    costs the rows, never the roster."""
    try:
        rows = session_rows(conn, [s['trace_id'] for s in sessions],
                            phases={s['trace_id']: s['phase'] for s in sessions})
    except Exception:
        log.error('palette_rows_failed', exc_info=True)
        rows = {}
    for entry in sessions:
        entry['row'] = rows.get(entry['trace_id'])
    return sessions


def _payload(sessions: list[dict]) -> dict:
    return jsonify({
        'sessions': sessions,
        # Same basis as every span timestamp, so a viewer in another timezone
        # subtracting its own clock never reads an elapsed time that leaks the
        # offset (see `_session_summary`'s server_now).
        'server_now': datetime.now().isoformat(),
        # The zoned twin, for rows whose stamps carry an explicit offset
        # (spans posted verbatim from a client's toISOString). `serverAgeMs`
        # picks the anchor that MATCHES the row's format so the parse-mode
        # offset cancels; without both it can only ever match one of them.
        'server_now_utc': datetime.now(timezone.utc).isoformat(),
    })


def _id_lookup(prefix: str, *, with_tail: bool):
    """`?id=<prefix>` — the palette's `#<session-id>` address sigil.

    Answering server-side is the whole point: the roster is twelve rows and the
    trace view holds a paginated window, so neither client-side set can contain
    the session someone just pasted the id of.
    """
    if len(prefix) < MIN_ID_CHARS:
        return _payload([])
    rows = _rows_by_id_prefix(prefix, ID_LOOKUP_LIMIT)
    conn = get_connection()
    try:
        sessions = _with_rows(conn, [
            _row_payload(conn, row, _phase_of(row), with_tail=with_tail)
            for row in rows])
    finally:
        conn.close()
    log.read('switcher_id_lookup', prefix_len=len(prefix), count=len(sessions))
    return _payload(sessions)


# NOT `/api/sessions/switcher`: `/api/sessions/<trace_id>` already claims that
# path and would resolve "switcher" as a trace id, answering with an empty
# session tree instead of 404. Same shape as `/api/session-tags`.
@trace_bp.route('/api/session-switcher')
def api_sessions_switcher():
    """The switcher roster: recent interactive sessions with phase + tail.

    Query params:
      limit=<n>  — how many sessions to return (default 12, max 24).
      tail=1     — include each row's `tail` (the palette's preview chat).
      id=<prefix> — resolve a trace-id prefix instead of reading the roster.
                    The palette's `#<session-id>` address sigil; returns the
                    same row shape so the switcher renders it unchanged.
    """
    with_tail = request.args.get('tail', '').lower() in ('1', 'true')
    prefix = (request.args.get('id') or '').strip()
    if prefix:
        return _id_lookup(prefix, with_tail=with_tail)
    try:
        limit = int(request.args.get('limit') or DEFAULT_LIMIT)
    except (TypeError, ValueError):
        limit = DEFAULT_LIMIT
    limit = max(1, min(limit, MAX_LIMIT))

    # Two passes on purpose. Ranking needs the phase of every candidate, but
    # the tail and the "what does it want" lookup are only ever READ — they are
    # the body of a row, and a candidate that loses the cut has no body. Doing
    # them in one pass paid that cost for three rows out of four.
    rows = _candidate_rows(min(limit * CANDIDATE_FACTOR, MAX_CANDIDATES))
    ranked = [(row, _phase_of(row)) for row in rows]
    # Stable sort: rows arrive newest-first, so sessions of equal rank keep
    # their recency order.
    ranked.sort(key=lambda pair: _rank(pair[1]))
    conn = get_connection()
    try:
        sessions = _with_rows(conn, [
            _row_payload(conn, row, phase, with_tail=with_tail)
            for row, phase in ranked[:limit]])
    finally:
        conn.close()
    log.read('switcher_roster', count=len(sessions))
    return _payload(sessions)
