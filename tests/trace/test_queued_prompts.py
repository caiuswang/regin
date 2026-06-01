"""Currently-queued user prompts derived from transcript queue-operations."""

from __future__ import annotations

import json

import lib.trace.queued_prompts as qp


def _write(path, ops):
    path.write_text('\n'.join(json.dumps(o) for o in ops) + '\n')


def _enq(content, ts):
    return {'type': 'queue-operation', 'operation': 'enqueue',
            'content': content, 'timestamp': ts}


def _rm(ts):
    return {'type': 'queue-operation', 'operation': 'remove', 'timestamp': ts}


def test_fifo_keeps_still_queued_and_filters_system(tmp_path, monkeypatch):
    tx = tmp_path / 't.jsonl'
    _write(tx, [
        _enq('<task-notification>sys</task-notification>', 't0'),  # system auto-queue
        _enq('first prompt', 't1'),
        _enq('[Image #3] [Image #4]', 't2'),
        _rm('t3'),                                                  # FIFO pops the system item
    ])
    monkeypatch.setattr('lib.trace.live_rescan._find_main_transcript',
                        lambda tid: str(tx))
    out = qp.current_queued_prompts('t')
    assert [q['content'] for q in out] == ['first prompt', '[Image #3] [Image #4]']
    assert out[0]['enqueued_at'] == 't1'


def test_empty_when_all_dequeued(tmp_path, monkeypatch):
    tx = tmp_path / 't.jsonl'
    _write(tx, [_enq('p', 't1'), _rm('t2')])
    monkeypatch.setattr('lib.trace.live_rescan._find_main_transcript',
                        lambda tid: str(tx))
    assert qp.current_queued_prompts('t') == []


def test_system_only_queue_surfaces_nothing(tmp_path, monkeypatch):
    tx = tmp_path / 't.jsonl'
    _write(tx, [_enq('<task-notification>x</task-notification>', 't1')])
    monkeypatch.setattr('lib.trace.live_rescan._find_main_transcript',
                        lambda tid: str(tx))
    assert qp.current_queued_prompts('t') == []


def test_no_transcript_returns_empty(monkeypatch):
    monkeypatch.setattr('lib.trace.live_rescan._find_main_transcript',
                        lambda tid: None)
    assert qp.current_queued_prompts('missing') == []
