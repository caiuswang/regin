"""Serve-time dedup of a rejected tool call rendered twice (lib/trace/merge.py).

A provider that reports a rejected call through PostToolUseFailure *and*
records the denial in its own transcript (historical Kimi sessions) left two
rows for one call in the append-only store: a `tool.failure` (red failure
card) and a `denied=True` deny span (amber "Interrupted" inline row).
`merge_spans` retires the failure at read time; the store is never touched.

Grounded on the live shape of session_2db41cb9: failure span carries the
tool_use_id in its column AND attrs, the `tooldeny-*` span carries it only in
attrs (`denied: True`, `deny_kind: 'deny'`).
"""

from __future__ import annotations

from lib.trace.merge import merge_spans

_TU = 'tool_LztlVKcfGNOPD32NuO7Vjt3i'


def _row(span_id, name, attrs, *, status='OK', tool_use_id=None, tid='t1', id=1):
    return {
        'id': id, 'trace_id': tid, 'span_id': span_id, 'parent_id': None,
        'name': name, 'kind': 'internal',
        'start_time': '2026-06-16T17:26:00', 'end_time': None, 'duration_ms': 0,
        'status_code': status, 'status_message': None,
        'attributes': attrs, 'turn_uuid': None, 'tool_use_id': tool_use_id,
    }


def _failure(tu=_TU, *, tid='t1', id=1, agent='kimi'):
    return _row(
        f'fail-{id}', 'tool.failure',
        {'tool_name': 'Bash', 'tool_use_id': tu, 'is_interrupt': False,
         'error': 'Tool "Bash" was not run because the user rejected '
                  'the approval request.', 'agent_type': agent},
        status='ERROR', tool_use_id=tu, tid=tid, id=id,
    )


def _deny(tu=_TU, *, tid='t1', id=2, agent='kimi'):
    return _row(
        f'tooldeny-{tu[:13]}', 'tool.Bash',
        {'tool_name': 'Bash', 'tool_use_id': tu, 'denied': True,
         'deny_kind': 'deny', 'denial_reason': 'Running: echo DENY_ME_PLEASE',
         'agent_type': agent},
        status='ERROR', tid=tid, id=id,
    )


def _ids(rows, **kw):
    return [s['span_id'] for s in merge_spans(rows, **kw)]


def test_failure_and_deny_for_same_call_render_once():
    """(a) Both rows for ONE rejected call → only the deny survives."""
    served = _ids([_failure(), _deny()])
    assert served == [f'tooldeny-{_TU[:13]}']


def test_failure_alone_survives():
    """(b) A genuine tool failure with no deny is untouched."""
    assert _ids([_failure()]) == ['fail-1']


def test_deny_alone_survives():
    """(c) A deny with no paired failure is untouched."""
    assert _ids([_deny()]) == [f'tooldeny-{_TU[:13]}']


def test_unrelated_deny_does_not_retire_a_genuine_failure():
    """The dedup is call-scoped: a real failure on call A and a deny on call B
    in the SAME session both keep their card."""
    rows = [_failure('tu_A', id=1), _deny('tu_B', id=2)]
    assert _ids(rows) == ['fail-1', 'tooldeny-tu_B']


def test_deny_in_another_trace_does_not_retire_the_failure():
    rows = [_failure(id=1, tid='t1'), _deny(id=2, tid='t2')]
    assert sorted(_ids(rows)) == sorted(['fail-1', f'tooldeny-{_TU[:13]}'])


def test_failure_without_tool_use_id_never_matches():
    """A failure carrying no tool_use_id can't be proven to be the denied call,
    so it is kept even next to a deny."""
    row = _row('fail-x', 'tool.failure', {'tool_name': 'Bash'}, status='ERROR')
    assert sorted(_ids([row, _deny(id=2)])) == \
        sorted(['fail-x', f'tooldeny-{_TU[:13]}'])


def test_claude_window_is_unchanged():
    """(d) A Claude fixture — prompt anchor, resolved tool, a denied call with
    NO paired failure, response — passes through the merge identically."""
    rows = [
        _row('prompt-u1', 'prompt', {'text': 'do it', 'agent_type': 'claude'}, id=1),
        _row('tool-1', 'tool.Read',
             {'tool_name': 'Read', 'tool_use_id': 'toolu_ok'},
             tool_use_id='toolu_ok', id=2),
        _deny('toolu_deny', id=3, agent='claude'),
        _row('resp-1', 'assistant_response',
             {'text': 'denied', 'agent_type': 'claude'}, id=4),
    ]
    before = [dict(r) for r in rows]
    served = merge_spans(rows)
    assert [s['span_id'] for s in served] == [r['span_id'] for r in before]
    assert [s['attributes'] for s in served] == [r['attributes'] for r in before]
    assert rows == before


def test_claude_failure_and_deny_on_the_same_call_still_dedups():
    """Provider-agnostic by design: if a Claude session ever emitted both rows
    for one call, it is the same double render and dedups the same way."""
    rows = [_failure(id=1, agent='claude'), _deny(id=2, agent='claude')]
    assert _ids(rows) == [f'tooldeny-{_TU[:13]}']
