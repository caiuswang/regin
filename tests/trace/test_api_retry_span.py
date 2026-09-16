"""`lib/agent_bridge/retry_events.py` — the durable record of an API retry.

The screen is re-read on every /map poll, so the same retry is observed many
times. Identity is `apiretry-<prompt-anchor>-<attempt>` and carries no clock,
which is what makes re-observation a no-op and a NEW attempt a new row. A
time-bucketed id would instead turn one stalled turn into a pile of rows that
count polls rather than attempts.
"""

from __future__ import annotations

from lib.agent_bridge.retry_events import (
    FINAL_ATTEMPT_KEY, SPAN_ID_PREFIX, SPAN_NAME, record_retry,
)
from lib.agent_bridge.screen_state import ScreenState


def _prompt_anchor(trace_id: str, span_id: str = 'prompt-abc', attrs=None) -> str:
    from lib.trace.trace_service import ingest_session_spans

    ingest_session_spans([({
        'trace_id': trace_id, 'span_id': span_id, 'parent_id': None,
        'name': 'prompt', 'kind': 'internal',
        'start_time': '2026-09-07T10:00:00', 'end_time': None,
        'duration_ms': 0, 'status_code': 'OK', 'status_message': None,
    }, attrs if attrs is not None else {'text': 'do the thing'})])
    return span_id


def _retrying(attempt: int, label: str = 'Connection error') -> ScreenState:
    return ScreenState(busy=True, verb='Working', elapsed_sec=378,
                       retry_label=label, retry_in='30s',
                       retry_attempt=attempt, retry_max=10)


def _retry_rows(trace_id: str) -> list[dict]:
    from sqlmodel import select
    from lib.orm import SessionLocal
    from lib.orm.models import SessionSpan

    with SessionLocal() as session:
        rows = session.execute(
            select(SessionSpan.span_id, SessionSpan.parent_id,
                   SessionSpan.name, SessionSpan.status_code,
                   SessionSpan.attributes)
            .where(SessionSpan.trace_id == trace_id)
            .where(SessionSpan.name == SPAN_NAME)
            .order_by(SessionSpan.span_id.asc())
        ).mappings().all()
    return [dict(r) for r in rows]


def test_the_same_retry_observed_three_times_is_one_row():
    trace_id = 'sess-retry-1'
    anchor = _prompt_anchor(trace_id)
    for _ in range(3):
        record_retry([trace_id], _retrying(5))

    rows = _retry_rows(trace_id)
    assert len(rows) == 1
    assert rows[0]['span_id'] == f'{SPAN_ID_PREFIX}{anchor}-5'
    assert rows[0]['parent_id'] == anchor
    assert rows[0]['status_code'] == 'ERROR'


def test_two_attempts_in_one_turn_are_two_rows():
    trace_id = 'sess-retry-2'
    anchor = _prompt_anchor(trace_id)
    record_retry([trace_id], _retrying(5))
    record_retry([trace_id], _retrying(6))

    rows = _retry_rows(trace_id)
    assert [r['span_id'] for r in rows] == [
        f'{SPAN_ID_PREFIX}{anchor}-5', f'{SPAN_ID_PREFIX}{anchor}-6',
    ]


def test_the_span_carries_what_the_row_renders():
    import json

    trace_id = 'sess-retry-3'
    _prompt_anchor(trace_id)
    record_retry([trace_id], _retrying(3, label='Usage limit reached (2pm)'))

    attrs = json.loads(_retry_rows(trace_id)[0]['attributes'])
    assert attrs['attempt'] == 3
    assert attrs['max_attempts'] == 10
    assert attrs['retry_in'] == '30s'
    assert attrs['label'] == 'Usage limit reached (2pm)'


def test_a_screen_with_no_retry_writes_nothing():
    trace_id = 'sess-retry-4'
    _prompt_anchor(trace_id)
    assert record_retry([trace_id], ScreenState(busy=True, verb='Working')) is None
    assert record_retry([trace_id], None) is None
    assert _retry_rows(trace_id) == []


def test_a_retry_with_no_prompt_anchor_yet_is_not_guessed_a_parent():
    trace_id = 'sess-retry-5'
    assert record_retry([trace_id], _retrying(2)) is None
    assert _retry_rows(trace_id) == []


def test_the_span_id_is_a_pure_function_of_anchor_and_attempt():
    from lib.agent_bridge.retry_events import retry_span_id

    assert retry_span_id('prompt-abc', 5) == retry_span_id('prompt-abc', 5)
    assert retry_span_id('prompt-abc', 5) != retry_span_id('prompt-abc', 6)
    assert retry_span_id('prompt-abc', 5) != retry_span_id('prompt-xyz', 5)


# ── Choosing the anchor ─────────────────────────────────────────────
# Three id families answer to `name == 'prompt'`. Only one of them is a parent
# a reader will ever see, and the other two are the ordinary state of a live
# session rather than edge cases — so `id desc` alone picks the wrong one most
# of the time.

def test_the_live_placeholder_is_not_the_anchor():
    """`promptlive-` is written when the prompt is submitted and the real
    anchor only when the turn flushes. A retry happens inside exactly that
    gap, and the merge retires the placeholder — a span parented to it floats
    at session root with no turn at all."""
    from lib.trace.pending_spans import prompt_placeholder_id

    trace_id = 'sess-retry-anchor-1'
    real = _prompt_anchor(trace_id, 'prompt-real-0001')
    _prompt_anchor(trace_id, prompt_placeholder_id(trace_id, 'do the thing'))

    assert record_retry([trace_id], _retrying(5)) is not None
    assert _retry_rows(trace_id)[0]['parent_id'] == real


def test_a_subagent_launch_prompt_is_not_the_anchor():
    """`prompt-sa-<agent>` shares the name and wins on id, which would nest a
    MAIN-agent retry inside an unrelated child's turn."""
    trace_id = 'sess-retry-anchor-2'
    real = _prompt_anchor(trace_id, 'prompt-real-0002')
    _prompt_anchor(trace_id, 'prompt-sa-ae0ab98c76a139ac8',
                   attrs={'agent_id': 'ae0ab98c76a139ac8'})

    assert record_retry([trace_id], _retrying(5)) is not None
    assert _retry_rows(trace_id)[0]['parent_id'] == real


def test_an_agent_scoped_prompt_is_caught_by_its_attribute_not_only_its_id():
    """`is_agent_scoped_prompt` reads `attributes.agent_id` too — a scoped
    prompt written under a plain id is still not a main-agent anchor."""
    trace_id = 'sess-retry-anchor-3'
    real = _prompt_anchor(trace_id, 'prompt-real-0003')
    _prompt_anchor(trace_id, 'prompt-scoped-0003', attrs={'agent_id': 'a99'})

    assert record_retry([trace_id], _retrying(5)) is not None
    assert _retry_rows(trace_id)[0]['parent_id'] == real


def test_a_session_with_only_placeholder_and_subagent_prompts_drops_the_retry():
    """No real anchor is not a licence to guess one."""
    from lib.trace.pending_spans import prompt_placeholder_id

    trace_id = 'sess-retry-anchor-4'
    _prompt_anchor(trace_id, prompt_placeholder_id(trace_id, 'do the thing'))
    _prompt_anchor(trace_id, 'prompt-sa-a711cdc498c0ea288',
                   attrs={'agent_id': 'a711cdc498c0ea288'})

    assert record_retry([trace_id], _retrying(5)) is None
    assert _retry_rows(trace_id) == []


def test_the_chosen_anchor_survives_the_serve_time_merge():
    """End-to-end: the parent the span points at must still be in the window
    the reader gets, or the row renders at session root."""
    import json

    from lib.trace.merge import merge_spans
    from lib.trace.pending_spans import prompt_placeholder_id
    from sqlmodel import select
    from lib.orm import SessionLocal
    from lib.orm.models import SessionSpan

    trace_id = 'sess-retry-anchor-5'
    _prompt_anchor(trace_id, 'prompt-real-0005')
    placeholder = prompt_placeholder_id(trace_id, 'do the thing')
    _prompt_anchor(trace_id, placeholder)
    span_id = record_retry([trace_id], _retrying(5))

    with SessionLocal() as session:
        rows = session.execute(
            select(SessionSpan.trace_id, SessionSpan.span_id,
                   SessionSpan.parent_id, SessionSpan.name,
                   SessionSpan.start_time, SessionSpan.attributes)
            .where(SessionSpan.trace_id == trace_id)
        ).mappings().all()
    merged = merge_spans([{**r, 'attributes': json.loads(r['attributes'])}
                          for r in rows])
    surviving = {s['span_id'] for s in merged}
    # The premise: the merge really does retire the placeholder here. Without
    # this the assertion below could pass on a fixture where nothing was ever
    # at risk.
    assert placeholder not in surviving
    retry = next(s for s in merged if s['span_id'] == span_id)
    assert retry['parent_id'] in surviving


# ── The banner that names no attempt ────────────────────────────────
# `error.noResponse`'s last frame is `· retrying once, waiting up to <dur>`:
# a retry is in flight and the screen states no number for it. The span still
# has to be identified, so the identity is the literal `-final` — a turn has
# exactly one last attempt, which makes it as deterministic as an integer and
# unable to collide with a numbered one.

def _final_retry(label: str = 'No response from the API after 16m 30s'):
    return ScreenState(busy=True, verb='Working', elapsed_sec=990,
                       retry_label=label, retry_in='8m',
                       retry_attempt=None, retry_max=None, retry_final=True)


def test_the_final_retry_is_recorded_under_a_final_identity():
    trace_id = 'sess-retry-final-1'
    anchor = _prompt_anchor(trace_id, 'prompt-real-f001')
    assert record_retry([trace_id], _final_retry()) == (
        f'{SPAN_ID_PREFIX}{anchor}-{FINAL_ATTEMPT_KEY}')

    rows = _retry_rows(trace_id)
    assert len(rows) == 1
    assert rows[0]['parent_id'] == anchor
    assert rows[0]['status_code'] == 'ERROR'


def test_the_final_retry_is_idempotent_and_distinct_from_numbered_attempts():
    trace_id = 'sess-retry-final-2'
    anchor = _prompt_anchor(trace_id, 'prompt-real-f002')
    record_retry([trace_id], _retrying(3))
    for _ in range(3):
        record_retry([trace_id], _final_retry())

    assert {r['span_id'] for r in _retry_rows(trace_id)} == {
        f'{SPAN_ID_PREFIX}{anchor}-3',
        f'{SPAN_ID_PREFIX}{anchor}-{FINAL_ATTEMPT_KEY}',
    }


def test_the_final_retrys_attributes_state_what_the_screen_said_and_no_more():
    """No `attempt` key at all rather than a fabricated number, and `final`
    carrying the fact the counter was absent."""
    import json

    trace_id = 'sess-retry-final-3'
    _prompt_anchor(trace_id, 'prompt-real-f003')
    record_retry([trace_id], _final_retry())

    attrs = json.loads(_retry_rows(trace_id)[0]['attributes'])
    assert 'attempt' not in attrs
    assert 'max_attempts' not in attrs
    assert attrs['final'] is True
    assert attrs['retry_in'] == '8m'
    assert attrs['label'] == 'No response from the API after 16m 30s'
