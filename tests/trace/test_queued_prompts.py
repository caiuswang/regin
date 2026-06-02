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


def _deq(ts):
    return {'type': 'queue-operation', 'operation': 'dequeue', 'timestamp': ts}


def _popall(content, ts):
    return {'type': 'queue-operation', 'operation': 'popAll',
            'content': content, 'timestamp': ts}


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


def test_dequeue_pops_fifo(tmp_path, monkeypatch):
    # `dequeue` is the current Claude Code name for a single FIFO pop.
    tx = tmp_path / 't.jsonl'
    _write(tx, [_enq('a', 't1'), _enq('b', 't2'), _deq('t3')])
    monkeypatch.setattr('lib.trace.live_rescan._find_main_transcript',
                        lambda tid: str(tx))
    out = qp.current_queued_prompts('t')
    assert [q['content'] for q in out] == ['b']


def test_popall_clears_whole_queue(tmp_path, monkeypatch):
    tx = tmp_path / 't.jsonl'
    _write(tx, [_enq('a', 't1'), _enq('b', 't2'), _popall('a', 't3')])
    monkeypatch.setattr('lib.trace.live_rescan._find_main_transcript',
                        lambda tid: str(tx))
    assert qp.current_queued_prompts('t') == []


def test_edit_then_requeue_reflects_final_state(tmp_path, monkeypatch):
    # Editing a queued prompt = popAll (back to editor) + a fresh enqueue.
    tx = tmp_path / 't.jsonl'
    _write(tx, [
        _enq('/workflows', 't1'),
        _popall('/workflows', 't2'),      # pulled back to editor to edit
        _enq('/workflows edited', 't3'),  # re-queued after the edit
    ])
    monkeypatch.setattr('lib.trace.live_rescan._find_main_transcript',
                        lambda tid: str(tx))
    out = qp.current_queued_prompts('t')
    assert [q['content'] for q in out] == ['/workflows edited']


def test_dequeue_on_empty_queue_is_noop(tmp_path, monkeypatch):
    # Parsing may begin mid-stream; a pop with nothing queued must not error.
    tx = tmp_path / 't.jsonl'
    _write(tx, [_deq('t1'), _enq('a', 't2')])
    monkeypatch.setattr('lib.trace.live_rescan._find_main_transcript',
                        lambda tid: str(tx))
    out = qp.current_queued_prompts('t')
    assert [q['content'] for q in out] == ['a']


def test_no_transcript_returns_empty(monkeypatch):
    monkeypatch.setattr('lib.trace.live_rescan._find_main_transcript',
                        lambda tid: None)
    assert qp.current_queued_prompts('missing') == []
