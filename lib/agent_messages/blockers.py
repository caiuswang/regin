"""What is the operator parked on *right now* — derived, not replayed.

The feed is assembled from the two authorities that actually know whether an
agent is stopped, not from the notification cards that were emitted about it:

* **Sessions regin owns** (SDK runs): the in-process ask registry
  (`lib.agent_sdk.registry`). A parked call sits in `_asks` until the very
  moment it is resolved, so its presence *is* the parked state.
* **Sessions regin merely traces** (hook-observed): the newest PENDING
  decision span, minus the read-time retirement signals (its call's resolved
  twin, a completed same-named tool, a denial, a newer main-loop prompt).

Message rows are attached for identity (dismiss id, read state, push history)
but no longer decide presence — an emit that fired twice, or never fired,
cannot double or hide the banner. A CLI session that is an SDK run's child
(`agent_runs.cli_session_id`) is excluded from the hook leg so one agent is
never derived twice under its two ids.

Three rules carried over from the row-driven assembly, still load-bearing:

* **Read state plays no part.** Acknowledging a question is not answering it.
* **Liveness is recency, never `status`.** A session that dies without an end
  event stays `status='active'` forever.
* **Options come from the span (or the parked call), not the body.** The
  card's body is prose built for push channels; re-parsing it invents data.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta

from lib.activity_log import get_activity_logger

log = get_activity_logger("agent_messages")

# Mirrors the frontend `isActive` fallback and `STALE_FALLBACK_WINDOW_MS`.
LIVE_WINDOW_MINUTES = 10

# The parked-decision span classes. `permreq-` is the pending-id prefix
# `lib/trace/pending_spans.py` mints before a tool_use_id exists.
_DECISION_SPAN_NAMES = ('permission.request', 'tool.AskUserQuestion',
                        'tool.ExitPlanMode')

_MAX_BLOCKERS = 20

# A live row younger than this is left alone by the hygiene pass: the row is
# written moments *before* its span (hook leg) or its registry entry (SDK
# leg), so a read landing in that gap would otherwise dismiss a card whose
# park is about to appear.
_HEAL_GRACE_SECONDS = 120


def _cutoff() -> str:
    return (datetime.now() - timedelta(minutes=LIVE_WINDOW_MINUTES)
            ).strftime('%Y-%m-%dT%H:%M:%S')


def _live_hook_trace_ids() -> list[str]:
    """Recently-seen sessions the hook tier alone speaks for.

    Recency only: `status` is unreliable for exactly the sessions this
    matters for (an agent killed mid-prompt never writes its end event).
    SDK runs and their CLI children are the registry's to report.
    """
    from lib.agent_sdk.store import sdk_child_session_ids
    from lib.orm import SessionLocal
    from lib.orm.models import Session as SessionModel
    from sqlmodel import or_, select
    children = sdk_child_session_ids()
    with SessionLocal() as session:
        # NULL-safe: a row minted by raw span ingest carries no status at
        # all, and `status != 'ended'` silently drops NULL — exactly the
        # freshly-parked session this feed exists to surface.
        rows = session.exec(
            select(SessionModel.trace_id)
            .where(or_(SessionModel.status.is_(None),
                       SessionModel.status != 'ended'),
                   SessionModel.last_seen >= _cutoff())).all()
    return [t for t in rows
            if t and not t.startswith('sdk-') and t not in children]


def _decision_spans(trace_ids: list[str]) -> dict:
    """trace_id → {span_id: row} for every decision span.

    `session_spans` is UNIQUE on (trace_id, span_id) — a resolving prompt
    updates its row rather than landing a second one — so a `status_code`
    read here is the span's current state, not a snapshot that a later row
    might contradict.
    """
    if not trace_ids:
        return {}
    from lib.orm.engine import get_connection
    names = ','.join('?' * len(_DECISION_SPAN_NAMES))
    traces = ','.join('?' * len(trace_ids))
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT trace_id, span_id, name, status_code, start_time, attributes "
            "  FROM session_spans "
            f" WHERE trace_id IN ({traces}) "
            f"   AND (name IN ({names}) OR span_id LIKE 'permreq-%') "
            " ORDER BY id ASC",
            (*trace_ids, *_DECISION_SPAN_NAMES)).fetchall()
    finally:
        conn.close()
    by_trace: dict[str, dict] = {}
    for r in rows:
        by_trace.setdefault(r['trace_id'], {})[r['span_id']] = dict(r)
    return by_trace


def _attributes(span: dict) -> dict:
    raw = span.get('attributes')
    if not raw:
        return {}
    try:
        parsed = json.loads(raw) if isinstance(raw, str) else raw
    except (json.JSONDecodeError, ValueError, TypeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _answered_tool_use_ids(trace_ids: list[str], tool_use_ids: set[str]) -> set[str]:
    """Gated calls that have since resolved, whatever tool carried them.

    The resolved twin of a permission gate is the tool's own span — a
    `tool.Bash`, say — which no decision-span name matches, so it has to be
    looked up by `tool_use_id` rather than by class.
    """
    if not trace_ids or not tool_use_ids:
        return set()
    from lib.orm.engine import get_connection
    traces = ','.join('?' * len(trace_ids))
    ids = ','.join('?' * len(tool_use_ids))
    wanted = sorted(tool_use_ids)
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT DISTINCT json_extract(attributes,'$.tool_use_id') AS tu "
            "  FROM session_spans "
            f" WHERE trace_id IN ({traces}) AND status_code != 'PENDING' "
            f"   AND json_extract(attributes,'$.tool_use_id') IN ({ids})",
            (*trace_ids, *wanted)).fetchall()
    finally:
        conn.close()
    return {r['tu'] for r in rows if r['tu']}


def _latest_prompt_times(trace_ids: list[str]) -> dict[str, str]:
    """trace_id → newest MAIN-LOOP `prompt` span start_time.

    A main-loop prompt strictly newer than a park proves the park was
    decided: the terminal does not accept a prompt while a permission menu
    or plan approval holds it. Subagent launch prompts share `name='prompt'`
    (`prompt-sa-<agent_id>`) but prove nothing — a parallel subagent can
    start while the main agent is parked — so they are excluded by prefix.
    """
    if not trace_ids:
        return {}
    from lib.orm.engine import get_connection
    traces = ','.join('?' * len(trace_ids))
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT trace_id, MAX(start_time) AS at FROM session_spans "
            f" WHERE trace_id IN ({traces}) AND name = 'prompt' "
            "   AND span_id NOT LIKE 'prompt-sa-%' "
            " GROUP BY trace_id", tuple(trace_ids)).fetchall()
    finally:
        conn.close()
    return {r['trace_id']: r['at'] for r in rows if r['at']}


def _latest_tool_completions(trace_ids: list[str]) -> dict:
    """(trace_id, tool name) → newest non-PENDING `tool.<name>` start_time.

    The retirement signal for a park whose span carries no usable
    tool_use_id: the same-named tool completing after the park started means
    the gate was approved (a denial never runs the tool — see denials below).
    Main-loop completions only: the terminal serializes the main loop around
    a park, but a parallel subagent keeps running tools, and its same-named
    completion proves nothing about the parked prompt.
    """
    if not trace_ids:
        return {}
    from lib.orm.engine import get_connection
    from lib.trace.pending_spans import AGENT_ID_SQL
    traces = ','.join('?' * len(trace_ids))
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT trace_id, name, MAX(start_time) AS at FROM session_spans "
            f" WHERE trace_id IN ({traces}) AND name LIKE 'tool.%' "
            "   AND status_code != 'PENDING' "
            f"   AND {AGENT_ID_SQL} IS NULL "
            " GROUP BY trace_id, name", tuple(trace_ids)).fetchall()
    finally:
        conn.close()
    return {(r['trace_id'], r['name'][len('tool.'):]): r['at']
            for r in rows if r['at']}


def _latest_denials(trace_ids: list[str]) -> dict:
    """(trace_id, tool name) → newest `permission.denied` start_time. A
    denial resolves the park without ever running the tool, so the completed
    -twin signal above never fires for it. Main-loop only, as above."""
    if not trace_ids:
        return {}
    from lib.orm.engine import get_connection
    from lib.trace.pending_spans import AGENT_ID_SQL
    traces = ','.join('?' * len(trace_ids))
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT trace_id, json_extract(attributes,'$.tool_name') AS tn, "
            "       MAX(start_time) AS at FROM session_spans "
            f" WHERE trace_id IN ({traces}) AND name = 'permission.denied' "
            f"   AND {AGENT_ID_SQL} IS NULL "
            " GROUP BY trace_id, tn", tuple(trace_ids)).fetchall()
    finally:
        conn.close()
    return {(r['trace_id'], r['tn']): r['at'] for r in rows if r['tn'] and r['at']}


class _RetireSignals:
    """The read-time evidence that a pending span's question was decided."""

    def __init__(self, trace_ids: list[str], tool_use_ids: set[str]):
        self.answered = _answered_tool_use_ids(trace_ids, tool_use_ids)
        self.prompted = _latest_prompt_times(trace_ids)
        self.completed = _latest_tool_completions(trace_ids)
        self.denied = _latest_denials(trace_ids)

    def retires(self, trace_id: str, span: dict) -> bool:
        attrs = _attributes(span)
        started = span.get('start_time') or ''
        tu = attrs.get('tool_use_id')
        if tu and tu in self.answered:
            return True
        # Name-based matching is strictly the fallback for a park that names
        # no call. A park that DOES carry a tool_use_id has an exact signal
        # (the `answered` set above), and letting a same-named completion
        # retire it hands a parallel subagent's `tool.Bash` the power to
        # permanently dismiss a live main-loop park.
        tool = attrs.get('tool_name') or _span_tool_name(span)
        if not tu and tool and self._tool_decided(trace_id, tool, started):
            return True
        return (self.prompted.get(trace_id) or '') > started

    def _tool_decided(self, trace_id: str, tool: str, started: str) -> bool:
        return ((self.completed.get((trace_id, tool)) or '') > started
                or (self.denied.get((trace_id, tool)) or '') > started)


def _span_tool_name(span: dict) -> str:
    name = span.get('name') or ''
    return name[len('tool.'):] if name.startswith('tool.') else ''


def _question_of(attrs: dict) -> dict:
    """The first question on a parked ask, or {} for a plan/tool gate."""
    questions = attrs.get('questions')
    if isinstance(questions, list) and questions:
        first = questions[0]
        return first if isinstance(first, dict) else {}
    return {}


def _options_of(question: dict) -> list[dict]:
    """Option labels + descriptions, positionally indexed.

    The index is the payload `bridge-answer` takes, so it must stay the
    span's own ordering — never a re-sort or a de-duplication.
    """
    out = []
    for index, opt in enumerate(question.get('options') or []):
        label = opt.get('label') if isinstance(opt, dict) else opt
        if not label:
            continue
        out.append({
            'index': index,
            'label': str(label),
            'description': (opt.get('description') or ''
                            if isinstance(opt, dict) else ''),
        })
    return out


def _answerable(*, kind: str, declared_kind: str, options: list[dict],
                multi_select: bool, bridge_reachable: bool, sdk_owned: bool):
    """Which operator surface may act on this card from anywhere, or None.

    `question` drives the option buttons over `bridge-answer`; `decision`
    drives allow/deny over `bridge-decide`, which only a session regin owns
    can take — there is no way to carry a typed decision into someone
    else's terminal, and guessing keystrokes for one is what must not happen.
    A multi-select ask needs per-option toggles the bridge cannot drive
    blindly, so it stays read-only here exactly as it does in LiveQaSheet.
    """
    if kind == 'question':
        # `index is None` marks an option recovered from the card's prose:
        # there is nothing to index into, so clicking it would be a guess.
        selectable = options and all(o['index'] is not None for o in options)
        if bridge_reachable and selectable and not multi_select:
            return 'question'
        return None
    # `declared_kind`, never the display default. `_prompt_fields` falls back
    # to 'tool' for a park whose attrs carry no kind, and gating on that
    # offered allow/deny for an ask that needs an option index — a decision
    # the parked call cannot even accept.
    if sdk_owned and declared_kind in ('plan', 'tool'):
        return 'decision'
    return None


def _body_options(body: str) -> list[dict]:
    """Option labels recovered from the card's prose, for DISPLAY only.

    `_format_question` writes each option as a `• ` line, and for a
    hook-observed prompt that body is the only record of them — the span
    carries no `questions`. They get no index because there is nothing to
    index into: showing them is honest, clicking them would be a guess.
    """
    return [{'index': None, 'label': line.strip()[2:].strip(), 'description': ''}
            for line in (body or '').split('\n')
            if line.strip().startswith('• ') and line.strip()[2:].strip()]


# `__…__` is deliberately NOT here. A permission body quotes real paths and
# identifiers (`requested_permission` is the command being approved), and
# treating dunders as emphasis turned `__init__.py` into `init.py` — showing
# the operator a different file than the one they are approving. The inner
# edges must be non-space so `a ** b ** c` survives as arithmetic.
_PAIRED_EMPHASIS = re.compile(r'(\*\*|~~)(\S(?:.*?\S)?)\1')


def _unwrap_line(line: str) -> str:
    """Drop a single `_`/`*` pair wrapping a whole line, leaving dunders be."""
    for mark in ('_', '*'):
        if (len(line) > 2 and line.startswith(mark) and line.endswith(mark)
                and line[1] != mark and line[-2] != mark):
            return line[1:-1]
    return line


def _strip_emphasis(line: str) -> str:
    """Unwrap markdown emphasis for a surface that renders text, not markdown.

    The body is authored for the push channels, which DO render markdown, so
    the markers stay in `body` and come off only here. Conservative on
    purpose: a lone `*` or `_` in a permission prompt is far more often a glob
    or a filename than emphasis, and unwrapping those corrupts exactly the
    text the operator has to read before approving it. Newlines survive — the
    banner renders the question `pre-line`.
    """
    return _unwrap_line(_PAIRED_EMPHASIS.sub(r'\2', line))


def _question_text(body: str) -> str:
    """The body with its `• ` option lines removed and emphasis unwrapped."""
    return '\n'.join(_strip_emphasis(line.strip())
                     for line in (body or '').split('\n')
                     if line.strip() and not line.strip().startswith('• '))


def _asked(question: dict, message: dict) -> dict:
    """What the operator is being asked, and the choices offered."""
    body = message.get('body') or ''
    fallback = _question_text(body) or message.get('title') or ''
    return {
        'question': question.get('question') or fallback,
        'header': question.get('header') or '',
        'options': _options_of(question) or _body_options(body),
        'multi_select': bool(question.get('multiSelect')),
    }


def _prompt_fields(attrs: dict, question: dict, message: dict) -> dict:
    """The human-facing half of a card, plus what names the gated call."""
    # `kind` is stamped only by the SDK tier; an ask reaching us as a
    # `tool.AskUserQuestion` placeholder carries none, and its questions are
    # what identify it — same rule as `liveRows.isAskSpan`.
    return {
        **_asked(question, message),
        'kind': attrs.get('kind') or ('question' if question else 'tool'),
        'requested_permission': attrs.get('requested_permission') or '',
        'tool_name': attrs.get('tool_name') or '',
    }


def _card_stub(trace_id: str, attrs: dict, *, msg_key: str,
               created_at: str, span_id: str | None) -> dict:
    """A card for a park no emit ever produced a row for (events disabled,
    push failed). id=None — there is no row to dismiss, and the frontend
    hides the Dismiss control for it; the park itself is the state."""
    from lib.agent_messages.event_notify import _format_permission
    title, body = _format_permission(attrs)
    return {
        'id': None, 'trace_id': trace_id, 'msg_type': 'blocker',
        'msg_key': msg_key, 'title': title, 'body': body,
        'span_id': span_id, 'links': [], 'pinned': False, 'version': 1,
        'webhook_status': None, 'read_at': None, 'acked_at': None,
        'dismissed_at': None, 'is_test': False,
        'created_at': created_at, 'updated_at': created_at,
        'session_title': None,
    }


def _decision_rows(trace_ids: set[str], *, include_tests: bool) -> dict:
    """(trace_id, msg_key) → newest decision row, dismissed or not.

    The newest row is what dismissal state is judged against: an older
    dismissed duplicate must not suppress a newer live card, and vice versa.
    """
    if not trace_ids:
        return {}
    from lib.agent_messages.event_notify import DECISION_KEYS
    from lib.agent_messages.store import serialize_rows
    from lib.orm import SessionLocal
    from lib.orm.models import AgentMessage
    from sqlmodel import select
    with SessionLocal() as session:
        stmt = select(AgentMessage).where(
            AgentMessage.trace_id.in_(sorted(trace_ids)),
            AgentMessage.msg_key.in_(DECISION_KEYS))
        if not include_tests:
            stmt = stmt.where(AgentMessage.is_test == 0)
        rows = session.exec(stmt.order_by(AgentMessage.id.asc())).all()
        serialized = serialize_rows(session, rows)
    return {(r['trace_id'], r['msg_key']): r for r in serialized}


def _dismissed_for(row: dict | None, *, span_id: str | None,
                   parked_since: str) -> bool:
    """Did the user already dismiss THIS park ("never show again")?

    Matched by the gated call's id when both sides carry one; otherwise by
    time — a dismissal stamped before the park began belonged to an earlier
    prompt under the same key. `_HEAL_GRACE_SECONDS` of slack covers the
    emit-before-span write order.
    """
    if not row or not row.get('dismissed_at'):
        return False
    if span_id and row.get('span_id'):
        return row['span_id'] == span_id
    since = _shifted(parked_since, -_HEAL_GRACE_SECONDS)
    return (row.get('updated_at') or '') >= since


def _shifted(stamp: str, seconds: int) -> str:
    try:
        return (datetime.fromisoformat(stamp)
                + timedelta(seconds=seconds)).isoformat()
    except (TypeError, ValueError):
        return stamp or ''


# ── The SDK leg: parks read off the ask registry ─────────────────────

def _sdk_parked() -> list[dict]:
    """One entry per parked call in a run this process owns.

    The registry is the same process the Flask app runs in (it is what
    `_bridge_reachability` already consults), so this read is exact: an ask
    listed here is blocked on the operator right now.
    """
    from lib.agent_sdk import policy, registry
    from lib.agent_sdk.store import cli_session_for
    out = []
    for ask in registry.all_pending_asks():
        attrs = policy.notify_attrs(ask.kind, ask.tool_name, ask.tool_input,
                                    ask.tool_use_id)
        attrs.setdefault('kind', ask.kind)
        # The CLI child id rides along: the sessions list shows the CHILD's
        # row (the sdk- row is alias-hidden), so the "awaiting decision"
        # highlight has to be able to match the id the user actually sees.
        child = cli_session_for(ask.trace_id)
        out.append({'trace_id': ask.trace_id, 'attrs': attrs,
                    'span_id': ask.tool_use_id or None,
                    'parked_since': ask.parked_at,
                    'alias_trace_ids': [child] if child else [],
                    'kind': ask.kind, 'sdk': True})
    return out


# ── The hook leg: parks derived from PENDING decision spans ──────────

def _hook_parked(live_traces: list[str], rows: dict) -> list[dict]:
    """Both hook-session legs: at most one span-derived permission park per
    session, plus its row-derived plan-review card if one is live.

    One permission park, not every PENDING span: the terminal parks its main
    loop on a single decision at a time, and stray placeholders that never
    got retired (a random-id `permission.request` has no key for ingest to
    resolve it by) would otherwise each raise a card of their own.
    """
    spans_by_trace = _decision_spans(live_traces)
    signals = _retire_signals(live_traces, spans_by_trace)
    parks = (_surviving_park(trace_id, spans, signals)
             for trace_id, spans in spans_by_trace.items())
    return ([p for p in parks if p is not None]
            + _plan_row_parks(rows, live_traces, signals))


def _retire_signals(live_traces: list[str],
                    spans_by_trace: dict) -> "_RetireSignals":
    tool_use_ids = {
        _attributes(s).get('tool_use_id')
        for spans in spans_by_trace.values() for s in spans.values()
        if s.get('status_code') == 'PENDING' and _attributes(s).get('tool_use_id')}
    return _RetireSignals(list(live_traces), tool_use_ids)


def _plan_row_parks(rows: dict, live_traces: list[str],
                    signals: "_RetireSignals") -> list[dict]:
    """A live `plan-pending` card on a hook session IS its own presence.

    Unlike a permission park, "a plan awaits your review" has no span or
    registry entry behind it — the emit is the only record the condition
    ever existed. It coexists with a permission park in the same session
    (approve the plan, and the first gated tool can park before anything
    completes), so it is a second card, not a variant of the first. A newer
    main-loop prompt still retires it: nothing stays awaiting review across
    the operator typing.
    """
    from lib.agent_messages.event_notify import PLAN_KEY
    live = set(live_traces)
    return [
        {'trace_id': trace_id, 'attrs': {'kind': 'plan'},
         'span_id': row.get('span_id') or None,
         'parked_since': row.get('created_at') or '',
         'kind': 'plan', 'sdk': False, 'row': row}
        for (trace_id, key), row in rows.items()
        if key == PLAN_KEY and trace_id in live
        and not row.get('dismissed_at') and not row.get('is_test')
        and not _prompted_after_raise(signals, trace_id, row)]


def _prompted_after_raise(signals: "_RetireSignals", trace_id: str,
                          row: dict) -> bool:
    raised = max(row.get('created_at') or '', row.get('updated_at') or '')
    return (signals.prompted.get(trace_id) or '') > raised


def _surviving_park(trace_id: str, spans: dict, signals) -> dict | None:
    pending = [s for s in spans.values()
               if s.get('status_code') == 'PENDING'
               and not signals.retires(trace_id, s)]
    if not pending:
        return None
    span = max(pending, key=lambda s: s.get('start_time') or '')
    attrs = _attributes(span)
    return {'trace_id': trace_id, 'attrs': attrs,
            'span_id': attrs.get('tool_use_id') or None,
            'parked_since': span.get('start_time') or '',
            'kind': attrs.get('kind')
            or ('question' if _question_of(attrs) else 'tool'),
            'sdk': False}


# ── Assembly ─────────────────────────────────────────────────────────

def _key_for(park: dict) -> str:
    from lib.agent_messages.event_notify import PERM_KEY, PLAN_KEY
    return PLAN_KEY if park['kind'] == 'plan' else PERM_KEY


def _session_titles(trace_ids: set[str]) -> dict[str, str]:
    if not trace_ids:
        return {}
    from lib.orm import SessionLocal
    from lib.orm.models import Session as SessionModel
    from sqlmodel import select
    with SessionLocal() as session:
        rows = session.exec(
            select(SessionModel.trace_id, SessionModel.title)
            .where(SessionModel.trace_id.in_(sorted(trace_ids)))).all()
    return {t: title for t, title in rows if title}


def _build(park: dict, row: dict | None, bridge: dict, titles: dict) -> dict:
    attrs = park['attrs']
    base = row or _card_stub(park['trace_id'], attrs,
                             msg_key=_key_for(park),
                             created_at=park['parked_since'],
                             span_id=park['span_id'])
    prompt = _prompt_fields(attrs, _question_of(attrs), base)
    declared = attrs.get('kind') or ''
    return {
        **base, **prompt, **bridge,
        # The park's own call id, never a placeholder. `bridge-decide`
        # forwards this as `tool_use_id` and the runner exact-matches it, so
        # a `permreq-<tu13>` id would guarantee "no pending permission
        # request" — whereas None lets the runner resolve the oldest park.
        'span_id': park['span_id'],
        'alias_trace_ids': park.get('alias_trace_ids') or [],
        'session_title': (row or {}).get('session_title')
        or titles.get(park['trace_id']),
        'answerable': _answerable(
            kind=prompt['kind'], declared_kind=declared,
            options=prompt['options'], multi_select=prompt['multi_select'],
            bridge_reachable=bridge['bridge_reachable'],
            sdk_owned=bridge['sdk_owned']),
    }


def _test_cards(rows: dict) -> list[dict]:
    """The Playwright seeding path: an `is_test` row is its own presence —
    there is no real park behind it, and there never will be."""
    return [{'trace_id': r['trace_id'], 'attrs': {}, 'span_id': r.get('span_id'),
             'parked_since': r.get('created_at') or '', 'kind': 'tool',
             'sdk': False, 'row': r}
            for r in rows.values()
            if r.get('is_test') and not r.get('dismissed_at')]


def _heal_orphans(rows: dict, claimed_ids: set, parked_traces: set) -> None:
    """Retire — for good — live decision rows whose park no longer exists.

    Presence no longer depends on this (the derivation above already ignores
    them); it keeps the unread badge honest and clears the card in any tab
    already showing it. Grace-windowed so a row written moments before its
    park registers is never reaped, and best-effort throughout — a heal
    failure must not break the read.
    """
    from lib.agent_messages import store
    from lib.notifications import hub
    horizon = (datetime.now()
               - timedelta(seconds=_HEAL_GRACE_SECONDS)).isoformat()
    for (trace_id, _key), row in rows.items():
        if (row.get('dismissed_at') or row.get('is_test')
                or row['id'] in claimed_ids or trace_id in parked_traces):
            continue
        if (row.get('updated_at') or '') >= horizon:
            continue
        try:
            store.dismiss(row['id'])
            hub.broadcast_event('resolved', {
                'trace_id': trace_id,
                'message_ids': [row['id']],
                'reason': 'dismissed',
            })
            hub.broadcast_counts()
            log.write("blocker_self_healed", message_id=row['id'],
                      trace_id=trace_id)
        except Exception:  # noqa: BLE001 — hygiene must never break the feed
            log.error("blocker_heal_failed", exc_info=True)


def live_blockers(*, include_tests: bool = False,
                  limit: int = _MAX_BLOCKERS) -> list[dict]:
    """Every decision waiting on a human, oldest first.

    Oldest first because the banner pages through them and the longest-parked
    agent is the one to answer next.
    """
    live_traces = _live_hook_trace_ids()
    sdk_parks = _sdk_parked()
    # Rows are read for every LIVE trace, not just the parked ones: an
    # orphaned card in a session with no park left is exactly what the
    # hygiene pass exists to retire.
    rows = _decision_rows(
        {p['trace_id'] for p in sdk_parks} | set(live_traces)
        | _sdk_row_traces(sdk_parks),
        include_tests=include_tests)
    parked = sdk_parks + _hook_parked(live_traces, rows)
    if include_tests:
        parked += _test_cards(rows)
    titles = _session_titles({p['trace_id'] for p in parked})
    out, claimed = [], set()
    for park in sorted(parked, key=lambda p: p['parked_since']):
        card = _card_for(park, rows, titles, claimed)
        if card is not None:
            out.append(card)
        if len(out) >= limit:
            break
    _heal_orphans(rows, claimed, {p['trace_id'] for p in parked})
    log.read("live_blockers_listed", count=len(out),
             sdk=sum(1 for p in parked if p.get('sdk')))
    return out


def _card_for(park: dict, rows: dict, titles: dict,
              claimed: set) -> dict | None:
    """One park → its card, or None when the user dismissed this very ask."""
    from web.blueprints.trace.sessions import _bridge_reachability
    row = park.get('row') or rows.get((park['trace_id'], _key_for(park)))
    if row is not None and row.get('dismissed_at') and 'row' not in park:
        if _dismissed_for(row, span_id=park['span_id'],
                          parked_since=park['parked_since']):
            return None
        row = None
    if row is not None:
        claimed.add(row['id'])
    bridge = ({'bridge_reachable': True, 'bridge_pane': None,
               'sdk_owned': True} if park['sdk']
              else _bridge_reachability(park['trace_id']))
    return _build(park, row, bridge, titles)


def _sdk_row_traces(parked: list[dict]) -> set[str]:
    """The CLI-child ids of the SDK parks — their historical rows (from the
    dual-writer era, or a race) are still this feed's to heal."""
    from lib.agent_sdk.store import cli_session_for
    out = set()
    for park in parked:
        if not park.get('sdk'):
            continue
        child = cli_session_for(park['trace_id'])
        if child:
            out.add(child)
    return out


__all__ = ["live_blockers", "LIVE_WINDOW_MINUTES"]
