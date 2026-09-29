"""Characterization tests for the pure span-projection helpers in
lib.trace.projection (`_widen_envelopes`, `_compute_active_work_ms`)."""

from __future__ import annotations

from lib.trace.projection import _compute_active_work_ms, _widen_envelopes


def _span(sid, start, end=None, parent=None, name='tool.Read', duration=None):
    return {'span_id': sid, 'parent_id': parent, 'name': name,
            'start_time': start, 'end_time': end, 'duration_ms': duration}


def test_widen_envelopes_empty_returns_input():
    spans: list[dict] = []
    assert _widen_envelopes(spans) is spans


def test_widen_envelopes_covers_children_and_keeps_semantic_duration():
    spans = [
        _span('resp', '2026-01-01T00:00:05', '2026-01-01T00:00:05',
              name='assistant_response', duration=30_000),
        _span('srv', '2026-01-01T00:00:03', None, parent='resp'),
        _span('p', '2026-01-01T00:00:10', None, name='prompt'),
        _span('t', '2026-01-01T00:00:11', '2026-01-01T00:00:14', parent='p'),
        _span('orphan', '2026-01-01T00:00:20', None, parent='missing'),
    ]
    out = {s['span_id']: s for s in _widen_envelopes(spans)}
    got = {sid: (s['start_time'], s['end_time'], s['duration_ms'])
           for sid, s in out.items()}
    assert got == {
        'resp': ('2026-01-01T00:00:03', '2026-01-01T00:00:05', 30_000),
        'srv': ('2026-01-01T00:00:03', None, None),
        'p': ('2026-01-01T00:00:10', '2026-01-01T00:00:14', 4_000),
        't': ('2026-01-01T00:00:11', '2026-01-01T00:00:14', None),
        'orphan': ('2026-01-01T00:00:20', None, None),
    }


def test_compute_active_work_ms_cases():
    cases = [
        ([], 0),
        ([_span('a', '2026-01-01T00:00:00')], 0),
        ([
            _span('p', '2026-01-01T00:00:00', name='prompt'),
            _span('a', '2026-01-01T00:00:01', parent='p'),
            _span('b', '2026-01-01T00:00:11', parent='p'),
            _span('c', '2026-01-01T00:05:00', parent='p'),
            _span('d', '2026-01-01T00:05:30'),
            _span('e', '2026-01-01T00:05:30'),
            _span('bad', 'not-a-time'),
            _span('none', None),
            _span('sa', '2026-01-01T00:05:40', name='subagent.start'),
        ], 40_000),
        ([
            _span('parent', '2026-01-01T00:00:00'),
            _span('kid', '2026-01-01T00:00:20', parent='parent'),
            _span('kid2', '2026-01-01T00:00:50', parent='parent'),
        ], 30_000),
    ]
    got = [_compute_active_work_ms(spans) for spans, _ in cases]
    assert got == [want for _, want in cases]
