"""Persist the retry the terminal is showing as a durable timeline span.

Why a span and not only the live field: `live_screen` describes NOW, and NOW
is gone by the time the operator asks why a turn took nine minutes. The span
is the only record that outlives the retry, and it is written as an ERROR
point span under the turn's own prompt anchor — the newest REAL main-agent one
(see `_prompt_anchor`) — so it reads as what it is: visible activity inside
that turn, not a terminal outcome.

Identity is `apiretry-<prompt-anchor-id>-<attempt>` and nothing else
(`-final` for the one banner that names no attempt number). Every
/map poll re-reads the same screen, so the id has to be a pure function of
what is on it: attempt 5 of this turn is one row however many times it is
observed, and attempt 6 is a different one. No timestamp takes part — an id
carrying one would split a single retry across polls and make the timeline
count re-reads instead of attempts.
"""

from __future__ import annotations

from datetime import datetime

SPAN_NAME = 'harness.api_retry'
SPAN_ID_PREFIX = 'apiretry-'
# The identity of the one banner that names no attempt number (`retrying once`
# — claude's last attempt). A turn has exactly one of these, so the literal is
# as deterministic as an integer, and it can never collide with a numbered
# attempt.
FINAL_ATTEMPT_KEY = 'final'


def retry_span_id(anchor_id: str, attempt) -> str:
    return f'{SPAN_ID_PREFIX}{anchor_id}-{attempt}'


def _attempt_key(state):
    """The attempt this banner identifies, or None when it names no retry."""
    if isinstance(state.retry_attempt, int):
        return state.retry_attempt
    return FINAL_ATTEMPT_KEY if state.retry_final else None


def _prompt_anchor(trace_ids) -> tuple[str, str] | None:
    """(trace_id, span_id) of the newest REAL main-agent `prompt` span, or None.

    Two families share `name == 'prompt'` and would otherwise win on id alone,
    and both are wrong parents:

      * the live `promptlive-` placeholder, which the serve-time merge retires
        once its real anchor lands — parenting to it leaves the retry span
        pointing at a row no reader will ever see, floating at session root.
        This is the PRIMARY case, not an edge: the placeholder is written when
        the prompt is submitted and the real anchor only when the turn
        flushes, and a retry happens inside exactly that gap by definition.
      * a subagent's `prompt-sa-` launch prompt, which nests a main-agent
        retry inside an unrelated child's turn.

    Both predicates come from `lib.trace.pending_spans`, which owns them, so
    this can't drift from the merge that enforces them.

    The trace_id rides along because an alias group's anchor can live under a
    member id rather than the canonical one, and a parent in a different trace
    would nest nowhere. No real anchor yet means None: dropping the retry beats
    hanging it off a parent that is about to be retired or belongs to someone
    else.
    """
    from sqlalchemy import func
    from sqlmodel import select
    from lib.orm import SessionLocal
    from lib.orm.models import SessionSpan
    from lib.trace.pending_spans import is_agent_scoped_prompt, is_pending_span_id

    with SessionLocal() as session:
        rows = session.execute(
            select(SessionSpan.trace_id, SessionSpan.span_id,
                   func.json_extract(SessionSpan.attributes, '$.agent_id'))
            .where(SessionSpan.trace_id.in_(list(trace_ids)))
            .where(SessionSpan.name == 'prompt')
            .order_by(SessionSpan.id.desc())
        ).all()
    for trace_id, span_id, agent_id in rows:
        if is_pending_span_id(span_id):
            continue
        if is_agent_scoped_prompt({'span_id': span_id},
                                  {'agent_id': agent_id}):
            continue
        return (trace_id, span_id)
    return None


def _already_written(trace_id: str, span_id: str) -> bool:
    from sqlmodel import select
    from lib.orm import SessionLocal
    from lib.orm.models import SessionSpan

    with SessionLocal() as session:
        return session.execute(
            select(SessionSpan.span_id)
            .where(SessionSpan.trace_id == trace_id)
            .where(SessionSpan.span_id == span_id)
        ).first() is not None


def _attributes(state) -> dict:
    attrs = {'label': state.retry_label or 'API error'}
    if isinstance(state.retry_attempt, int):
        attrs['attempt'] = state.retry_attempt
    if isinstance(state.retry_max, int):
        attrs['max_attempts'] = state.retry_max
    if state.retry_in:
        attrs['retry_in'] = state.retry_in
    if state.retry_final:
        attrs['final'] = True
    return attrs


def record_retry(group, state) -> str | None:
    """Write the retry `state` describes, returning its span_id or None.

    None covers every ordinary case: no retry on screen, no prompt anchor to
    nest under yet, and the re-observation of an attempt already recorded.
    Never raises — a summary must not fail over a decoration.
    """
    if state is None:
        return None
    key = _attempt_key(state)
    if key is None:
        return None
    try:
        anchor = _prompt_anchor(group)
        if anchor is None:
            return None
        trace_id, anchor_id = anchor
        span_id = retry_span_id(anchor_id, key)
        if _already_written(trace_id, span_id):
            return None
        from lib.trace import trace_service
        attrs = _attributes(state)
        trace_service.ingest_session_spans([({
            'trace_id': trace_id,
            'span_id': span_id,
            'parent_id': anchor_id,
            'name': SPAN_NAME,
            'kind': 'internal',
            'start_time': datetime.now().isoformat(),
            'duration_ms': 0,
            'status_code': 'ERROR',
            'status_message': attrs['label'],
        }, attrs)])
    except Exception:  # noqa: BLE001 - a screen read must never fail a summary
        from lib.activity_log import get_activity_logger
        get_activity_logger("agent_bridge").error(
            "api_retry_record_failed", exc_info=True)
        return None
    from lib.activity_log import get_activity_logger
    get_activity_logger("agent_bridge").write(
        "api_retry_recorded", trace_id=trace_id, span_id=span_id,
        attempt=key)
    return span_id
