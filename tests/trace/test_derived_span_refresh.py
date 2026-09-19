"""A span whose body is built from ANOTHER row must refresh when that row lands.

`_body_needed` withholds a span's body once the client holds it: below the
watermark, undirtied, and settled. That rests on "a body only changes when its
own row changes" — true for every hook-written attribute, false for one the
serve-time merge derives. A backgrounded shell is the case: the completion
arrives as a separate `task.notification` INSERT, the merge stamps the result
onto the already-settled `tool.Bash` row, and its id never moves — so the badge
stayed `running` until a full page reload.
"""

from __future__ import annotations

from lib.trace.merge import DERIVED_FROM_ID_KEY, merge_spans
from web.blueprints.trace.sessions import _body_needed


def _bash_span(**attrs):
    return {
        'id': 10, 'trace_id': 't', 'span_id': 'bash1', 'parent_id': 'p',
        'name': 'tool.Bash', 'status_code': 'OK',
        'start_time': '2026-09-19T11:17:26.100000',
        'end_time': '2026-09-19T11:17:26.103000', 'duration_ms': 3,
        'attributes': {'tool_name': 'Bash', 'command_preview': 'npm run dev',
                       **attrs},
    }


def _notification_span(row_id=99, **attrs):
    return {
        'id': row_id, 'trace_id': 't', 'span_id': 'task-bo58vwcaw',
        'parent_id': 'p', 'name': 'task.notification', 'status_code': 'OK',
        'start_time': '2026-09-19T11:17:56.100000',
        'end_time': '2026-09-19T11:17:56.100000', 'duration_ms': 0,
        'attributes': {'task_id': 'bo58vwcaw', 'status': 'completed', **attrs},
    }


def test_the_join_records_which_row_it_derived_from():
    out = merge_spans([_bash_span(background_task_id='bo58vwcaw'),
                       _notification_span(row_id=99)])
    bash = next(s for s in out if s['span_id'] == 'bash1')

    assert bash['attributes'][DERIVED_FROM_ID_KEY] == 99


def test_an_unfinished_shell_derives_from_nothing():
    out = merge_spans([_bash_span(background_task_id='bo58vwcaw')])
    bash = next(s for s in out if s['span_id'] == 'bash1')

    assert DERIVED_FROM_ID_KEY not in bash['attributes']


def test_a_settled_span_below_the_watermark_is_withheld():
    """The rule this must not weaken: an ordinary settled Bash call the client
    already holds is not re-sent on every poll."""
    assert _body_needed(_bash_span(), watermark=50, dirty=set()) is False


def test_a_body_derived_from_an_unseen_row_ships():
    span = _bash_span(background_task_id='bo58vwcaw',
                      **{DERIVED_FROM_ID_KEY: 99})

    assert _body_needed(span, watermark=50, dirty=set()) is True


def test_it_stops_shipping_once_the_source_row_is_held():
    """Identifier-based, so it settles: once the client has read past the
    notification's id the body is withheld again — no poll-forever."""
    span = _bash_span(background_task_id='bo58vwcaw',
                      **{DERIVED_FROM_ID_KEY: 99})

    assert _body_needed(span, watermark=120, dirty=set()) is False
