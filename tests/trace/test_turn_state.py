"""A traced session's status reads its own turn boundaries, not span age.

D286: a session parked at the prompt read `working` in the list because a
lone late span (a `config.change`) put it inside the 10-minute recency window,
and recency alone mapped to `working`.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta

import pytest

from hook_manager.core import HookPayload


def _ts(minutes_ago: float) -> str:
    return (datetime.now() - timedelta(minutes=minutes_ago)).strftime('%Y-%m-%dT%H:%M:%S.%f')


def _seed(tid, spans, last_seen_min=1.0):
    from lib.orm.engine import SessionLocal
    from lib.orm.models import Session as SessionModel
    from lib.orm.models.trace import SessionSpan
    with SessionLocal() as db:
        db.add(SessionModel(trace_id=tid, status='active',
                            last_seen=_ts(last_seen_min), started_at=_ts(60)))
        for i, (name, minutes_ago, attrs) in enumerate(spans):
            db.add(SessionSpan(trace_id=tid, span_id=f'{tid}-{i}', name=name,
                               start_time=_ts(minutes_ago),
                               attributes=json.dumps(attrs or {})))
        db.commit()


def _row_status(tid):
    from lib.orm.engine import SessionLocal
    from lib.orm.models import Session as SessionModel
    from web.blueprints.trace.sessions import listing as S
    with SessionLocal() as db:
        return db.query(S._row_status_expr(phases={})).filter(
            SessionModel.trace_id == tid).scalar()


STOP = {'hook_event': 'Stop'}


@pytest.mark.parametrize('tid, spans, want_state, want_status', [
    # The defect: turn closed long ago, a late config.change keeps it recent.
    ('parked', [('prompt', 50, None), ('turn', 40, STOP), ('config.change', 1, None)],
     'idle', 'idle'),
    ('legacy_parked', [('prompt', 50, None), ('hook.stop_summary', 40, None)],
     'idle', 'idle'),
    ('mid_turn', [('turn', 40, STOP), ('prompt', 2, None), ('tool.Bash', 1, None)],
     'working', 'working'),
    ('notified', [('prompt', 50, None), ('turn', 40, STOP), ('task.notification', 1, None)],
     'working', 'working'),
    ('delegated', [('prompt', 50, None),
                   ('turn', 40, {**STOP, 'delegated_in_flight': 2}),
                   ('hook.stop_summary', 39, None)],
     'working', 'working'),
    ('prompt_turn_only', [('prompt', 2, None), ('turn', 2, {'hook_event': 'UserPromptSubmit'})],
     None, 'working'),
    ('no_stop_ever', [('tool.Bash', 1, None)], None, 'working'),
])
def test_turn_state_and_list_status(tmp_db, tid, spans, want_state, want_status):
    from web.blueprints.trace.sessions.turn_state import turn_state
    _seed(tid, spans)
    assert turn_state(tid) == want_state
    assert _row_status(tid) == want_status


def test_stale_parked_session_stays_inactive(tmp_db):
    _seed('old', [('prompt', 50, None), ('turn', 40, STOP)], last_seen_min=30)
    assert _row_status('old') == 'inactive'


def test_status_facet_counts_idle(tmp_db):
    from web.blueprints.trace.sessions import listing as S
    _seed('parked', [('prompt', 50, None), ('turn', 40, STOP), ('config.change', 1, None)])
    _seed('busy', [('prompt', 2, None)])
    from lib.orm.engine import SessionLocal
    from lib.orm.models import Session as SessionModel
    with SessionLocal() as db:
        active = {r[0] for r in db.query(SessionModel.trace_id)
                  .filter(S._session_active_clause(True, S._row_status_expr(phases={})))}
    assert active == {'parked', 'busy'}


def _payload(event, **raw):
    return HookPayload.from_stdin_json(event, {'hook_event_name': event,
                                               'session_id': 's', **raw})


def test_stop_turn_span_counts_delegated_work_not_shells():
    from hook_manager.handlers.turn_trace.entry import _turn_span_attrs
    attrs = _turn_span_attrs(_payload('Stop', background_tasks=[
        {'id': 'a', 'type': 'local_agent'}, {'id': 'b', 'type': 'shell'}]), 'm')
    assert attrs == {'model': 'm', 'hook_event': 'Stop', 'delegated_in_flight': 1}


def test_prompt_turn_span_marks_its_event_only():
    from hook_manager.handlers.turn_trace.entry import _turn_span_attrs
    assert _turn_span_attrs(_payload('UserPromptSubmit'), 'm') == {
        'model': 'm', 'hook_event': 'UserPromptSubmit'}


def test_stop_without_snapshot_reports_no_count():
    from hook_manager.handlers.turn_trace.entry import _turn_span_attrs
    assert 'delegated_in_flight' not in _turn_span_attrs(_payload('Stop'), 'm')
