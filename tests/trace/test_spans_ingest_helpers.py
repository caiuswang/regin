"""Characterization of the span-ingest helpers `_validate_span` and
`_announce_spans` (web/blueprints/trace/spans_ingest.py)."""

from __future__ import annotations

import pytest

from web.blueprints.trace import spans_ingest as si

_OK = {'trace_id': 't', 'span_id': 's', 'name': 'n',
       'start_time': '2026-01-01T00:00:00'}


def _span(**over):
    span = dict(_OK)
    span.update(over)
    return {k: v for k, v in span.items() if v is not _DROP}


_DROP = object()


@pytest.mark.parametrize('span,max_bytes,expected', [
    ('nope', 100, (None, 'span must be an object')),
    (_span(trace_id=_DROP), 100, (None, 'missing or blank trace_id')),
    (_span(span_id='  '), 100, (None, 'missing or blank span_id')),
    (_span(name=''), 100, (None, 'missing or blank name')),
    (_span(start_time='yesterday'), 100,
     (None, 'start_time must be a valid ISO 8601 timestamp')),
    (_span(end_time='later'), 100,
     (None, 'end_time must be a valid ISO 8601 timestamp')),
    (_span(end_time=''), 100, ({}, None)),
    (_span(attributes=[1]), 100, (None, 'attributes must be an object')),
    (_span(attributes={'is_test': 'yes', 'k': 1}), 100,
     ({'is_test': True, 'k': 1}, None)),
    (_span(attributes={'is_test': 'no', 'k': 1}), 100, ({'k': 1}, None)),
    (_span(attributes={'k': 'x' * 50}), 20,
     (None, 'attributes too large: 59 bytes (max: 20)')),
])
def test_validate_span(span, max_bytes, expected):
    assert si._validate_span(span, max_bytes) == expected


def test_validate_span_unserializable_attrs():
    attrs, reason = si._validate_span(_span(attributes={'k': object()}), 100)
    assert attrs is None
    assert reason.startswith('attributes not JSON-serializable: ')


class _Conn:
    def close(self):
        pass


@pytest.fixture
def announce(monkeypatch):
    from lib.notifications import hub
    import lib.orm.engine as engine
    import lib.trace.alias as alias
    from lib.trace.trace_service import projection_cache

    state = {'subs': 1, 'frames': [], 'fail': set()}

    def canonical(tid):
        if 'canonical' in state['fail']:
            raise RuntimeError('boom')
        return {'alias': 'a', 'none': None}.get(tid, tid)

    def connect():
        if 'conn' in state['fail']:
            raise RuntimeError('boom')
        return _Conn()

    def broadcast(name, payload, topic=None):
        if 'broadcast' in state['fail']:
            raise RuntimeError('boom')
        state['frames'].append((name, payload, topic))

    monkeypatch.setattr(hub, 'subscriber_count', lambda: state['subs'])
    monkeypatch.setattr(hub, 'broadcast_event', broadcast)
    monkeypatch.setattr(alias, 'canonical_trace_id', canonical)
    monkeypatch.setattr(alias, 'trace_group', lambda conn, c: (c, c + '-alias'))
    monkeypatch.setattr(engine, 'get_connection', connect)
    monkeypatch.setattr(projection_cache, 'epoch', lambda group: f'ep:{group[0]}')
    return state


_BATCH = [
    ({'trace_id': 'a', 'span_id': '1'}, {}),
    ({'trace_id': 'alias', 'span_id': '2'}, {}),
    ({'span_id': 'orphan'}, {}),
    ({'trace_id': 'none'}, {}),
    ({'trace_id': 'b'}, {}),
]


def test_announce_spans_one_frame_per_canonical(announce):
    from lib.notifications import hub
    si._announce_spans(_BATCH)
    assert announce['frames'] == [
        (hub.TRACE_SPANS_EVENT,
         {'trace_id': 'a', 'epoch': 'ep:a', 'span_ids': ['1', '2']}, 'trace:a'),
        (hub.TRACE_SPANS_EVENT,
         {'trace_id': 'none', 'epoch': 'ep:none', 'span_ids': []}, 'trace:none'),
        (hub.TRACE_SPANS_EVENT,
         {'trace_id': 'b', 'epoch': 'ep:b', 'span_ids': []}, 'trace:b'),
    ]


@pytest.mark.parametrize('subs,fail,batch', [
    (0, set(), _BATCH),
    (1, set(), [({'span_id': 'x'}, {})]),
    (1, {'canonical'}, _BATCH),
    (1, {'conn'}, _BATCH),
    (1, {'broadcast'}, _BATCH),
])
def test_announce_spans_stays_silent(announce, subs, fail, batch):
    announce['subs'] = subs
    announce['fail'] = fail
    si._announce_spans(batch)
    assert announce['frames'] == []
