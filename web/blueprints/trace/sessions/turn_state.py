"""Whether a traced session's main agent is mid-turn or parked at the prompt.

Read off the turn boundaries the hooks already persist, never off span age:
a turn opens on a `prompt` (or a `task.notification`, which starts a turn
with no UserPromptSubmit) and closes on the `Stop`-path `turn` span. A Stop
that reported delegated work still in flight (`delegated_in_flight`) does not
park the session — the agent is waiting on it, and the notification that
settles it opens the next turn.

`hook.stop_summary` is accepted as a Stop too: it is the transcript row Claude
Code writes after its Stop hooks ran, and the only marker a turn closed before
the `turn` span carried `hook_event`.

`None` means no Stop has ever been recorded (a provider that does not report
one) — callers keep their recency fallback for that, not a guess.
"""

from __future__ import annotations

from sqlalchemy import and_, case, func, literal, or_
from sqlalchemy.orm import aliased
from sqlmodel import select

from lib.orm.models import Session as SessionModel
from lib.orm.models.trace import SessionSpan

TURN_OPENERS = ('prompt', 'task.notification')


def _hook_event(span):
    return func.json_extract(span.attributes, '$.hook_event')


def _is_stop_turn(span):
    return and_(span.name == 'turn', _hook_event(span) == 'Stop')


def _is_stop(span):
    return or_(_is_stop_turn(span), span.name == 'hook.stop_summary')


def turn_state_expr(trace_id_col=SessionModel.trace_id):
    """`'idle'` | `'working'` | NULL, correlated on `trace_id_col`."""
    opener, stop, last = aliased(SessionSpan), aliased(SessionSpan), aliased(SessionSpan)
    last_open = (select(func.max(opener.start_time))
                 .where(opener.trace_id == trace_id_col,
                        opener.name.in_(TURN_OPENERS))
                 .scalar_subquery())
    last_stop = (select(func.max(stop.start_time))
                 .where(stop.trace_id == trace_id_col, _is_stop(stop))
                 .scalar_subquery())
    delegated = (select(func.json_extract(last.attributes, '$.delegated_in_flight'))
                 .where(last.trace_id == trace_id_col, _is_stop_turn(last))
                 .order_by(last.start_time.desc())
                 .limit(1)
                 .scalar_subquery())
    closed = and_(last_stop >= func.coalesce(last_open, literal('')),
                  func.coalesce(delegated, 0) == 0)
    return case((last_stop.is_(None), None), (closed, 'idle'), else_='working')


def turn_state(trace_id: str) -> str | None:
    from lib.orm import SessionLocal
    with SessionLocal() as session:
        return session.exec(select(turn_state_expr(literal(trace_id)))).one()
