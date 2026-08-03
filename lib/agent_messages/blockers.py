"""What is the operator parked on *right now* — read fresh, not replayed.

The blocker banner cannot be driven by the event stream alone. A frame is
delivered once, so a reload, a route change into a page that mounts the
banner, or simply arriving after the prompt fired leaves a stopped agent
with no surface at all. This module answers the question once, whenever a
client (re)mounts; the stream then carries every change on top of it.

Three rules the assembly exists to hold:

* **Read state plays no part.** Acknowledging a question is not answering
  it, so a `read_at` gate would drop the banner for an agent still stopped —
  the trap `useLiveDecisions.js` and the `resolved`-reason vocabulary both
  exist to close.
* **Liveness is recency, never `status`.** A session that dies without an
  end event stays `status='active'` forever, so `_session_active_clause`
  (which trusts that column) would nag about long-orphaned cards. A card
  younger than the window counts as live on its own: it was written by a
  hook that was running seconds ago, and requiring the sessions row to have
  caught up first makes a real banner flash and disappear.
* **Options come from the span, not the body.** The card's body is prose
  built for push channels; re-parsing it for option labels invents data.
  The parked span carries the real `questions[…].options`, so the banner's
  buttons can name an `option_index` the bridge will actually deliver.
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
# Undismissed decision cards outnumber live ones by an order of magnitude,
# so the pre-filter read has to be much wider than the cap it feeds.
_CANDIDATE_SCAN = 200


def _cutoff() -> str:
    return (datetime.now() - timedelta(minutes=LIVE_WINDOW_MINUTES)
            ).strftime('%Y-%m-%dT%H:%M:%S')


def _live_trace_ids(trace_ids: list[str]) -> set[str]:
    """Which of these sessions could still receive an answer.

    Recency only: `status` is unreliable for exactly the sessions this
    matters for (an agent killed mid-prompt never writes its end event).
    """
    if not trace_ids:
        return set()
    from lib.orm import SessionLocal
    from lib.orm.models import Session as SessionModel
    from sqlmodel import select
    with SessionLocal() as session:
        rows = session.exec(
            select(SessionModel.trace_id)
            .where(SessionModel.trace_id.in_(trace_ids),
                   SessionModel.status != 'ended',
                   SessionModel.last_seen >= _cutoff())).all()
    return {r for r in rows}


def _waiting_trace_ids(messages: list[dict]) -> set[str]:
    """Sessions that could still receive an answer.

    A card younger than the window is its own liveness proof: the hook that
    wrote it was running seconds ago. Requiring the sessions row to have
    caught up first makes a genuine banner flash and vanish.
    """
    fresh = _cutoff()
    ids = [m['trace_id'] for m in messages if m.get('trace_id')]
    recent_card = {m['trace_id'] for m in messages
                   if m.get('trace_id') and (m.get('created_at') or '') >= fresh}
    return _live_trace_ids(ids) | recent_card


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


def _candidate_ids(tool_use_id: str) -> set[str]:
    """The span ids that can carry this gated call.

    `notify_permission_request` stamps the card with the full `tool_use_id`,
    but the parked span is a placeholder keyed on a **truncated** copy of it
    (`pending-<tu[:13]>` / `permreq-<tu[:13]>`, minted by
    `lib.trace.pending_spans`). Matching the two by equality therefore never
    succeeds — measured on the live DB, 21 of 21 cards with a `span_id` had
    no equal span row — which silently demoted every card to the
    newest-pending fallback below.
    """
    from lib.trace.pending_spans import perm_pending_id, tool_pending_id
    return {tool_use_id, perm_pending_id(tool_use_id),
            tool_pending_id(tool_use_id)}


def _pick_span(message: dict, spans: dict):
    """The parked span this card is about, or None.

    A card that names a call but whose span cannot be found stays span-less on
    purpose. The fallback below picks the newest pending span in the session,
    and 201 sessions in the live DB hold more than one at once — binding a
    card to a *different* prompt's options would hand `bridge-answer` an
    `option_index` into the wrong question. Read-only is the safe failure.
    """
    tool_use_id = message.get('span_id')
    if tool_use_id:
        wanted = _candidate_ids(tool_use_id)
        matched = [s for s in spans.values()
                   if s['span_id'] in wanted
                   or _attributes(s).get('tool_use_id') == tool_use_id]
        return matched[0] if matched else None
    pending = [s for s in spans.values() if s.get('status_code') == 'PENDING']
    if not pending:
        return None
    return max(pending, key=lambda s: s.get('start_time') or '')


def _answered_tool_use_ids(trace_ids: list[str], tool_use_ids: set[str]) -> set[str]:
    """Gated calls that have since resolved, whatever tool carried them.

    The resolved twin of a permission gate is the tool's own span — a
    `tool.Bash`, say — which no decision-span name matches, so it has to be
    looked up by `tool_use_id` rather than by class. This is the one signal
    strong enough to retire a blocker on its own: `events.resolve` frequently
    never delivers, and an un-dismissed card would otherwise nag forever.
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
    # to 'tool' for a card whose span could not be found, and gating on that
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


def _build(message: dict, span, bridge: dict) -> dict:
    attrs = _attributes(span) if span else {}
    prompt = _prompt_fields(attrs, _question_of(attrs), message)
    return {
        **message, **prompt, **bridge,
        # ONLY the card's own id, never the matched span's. `bridge-decide`
        # forwards this as `tool_use_id` and `agent_sdk.registry` exact-matches
        # it against the parked call, so emitting a placeholder id
        # (`permreq-<tu13>`) guarantees "no pending permission request" —
        # whereas emitting nothing lets the runner resolve the oldest park,
        # which is the correct behaviour for a card that never carried an id.
        'span_id': message.get('span_id'),
        'answerable': _answerable(
            kind=prompt['kind'], declared_kind=attrs.get('kind') or '',
            options=prompt['options'], multi_select=prompt['multi_select'],
            bridge_reachable=bridge['bridge_reachable'],
            sdk_owned=bridge['sdk_owned']),
    }


def live_blockers(*, include_tests: bool = False,
                  limit: int = _MAX_BLOCKERS) -> list[dict]:
    """Every decision waiting on a human, oldest first.

    Oldest first because the banner pages through them and the longest-parked
    agent is the one to answer next.
    """
    from lib.agent_messages import store
    from web.blueprints.trace.sessions import _bridge_reachability
    # Scanned wide, then capped AFTER filtering. Capping the read instead
    # would let stale-but-undismissed cards — 139 of them in the live DB —
    # crowd a genuinely parked session out of the feed entirely.
    messages = store.list_decision_messages(
        limit=_CANDIDATE_SCAN, include_tests=include_tests)
    waiting = _waiting_trace_ids(messages)
    spans = _decision_spans(sorted(waiting))
    answered = _answered_tool_use_ids(
        sorted(waiting), {m['span_id'] for m in messages if m.get('span_id')})
    out = []
    for message in messages:
        trace_id = message.get('trace_id')
        if trace_id not in waiting or message.get('span_id') in answered:
            continue
        span = _pick_span(message, spans.get(trace_id) or {})
        # A named span that has resolved means the prompt was answered —
        # the card just never got its dismiss.
        if span is not None and span.get('status_code') != 'PENDING':
            continue
        out.append(_build(message, span, _bridge_reachability(trace_id)))
        if len(out) >= limit:
            break
    out.reverse()
    # `saturated` names the condition that reintroduces the crowding bug the
    # post-filter cap fixed: once the scan itself fills up, an older card in a
    # still-live session can fall off the end unseen.
    log.read("live_blockers_listed", count=len(out), candidates=len(messages),
             saturated=len(messages) >= _CANDIDATE_SCAN)
    return out


__all__ = ["live_blockers", "LIVE_WINDOW_MINUTES"]
