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


def _user(content, meta=False):
    e = {'type': 'user', 'message': {'role': 'user', 'content': content}}
    if meta:
        e['isMeta'] = True
    return e


def test_consumed_texts_captures_processed_turns_normalized(tmp_path, monkeypatch):
    tx = tmp_path / 't.jsonl'
    _write(tx, [
        _user('was  answered\n'),                              # collapses whitespace
        _user('<caveat>local command</caveat>', meta=True),    # isMeta skipped
        {'type': 'assistant', 'message': {'content': 'reply'}},  # non-user skipped
    ])
    monkeypatch.setattr('lib.trace.live_rescan._find_main_transcript',
                        lambda tid: str(tx))
    assert qp.consumed_prompt_texts('t') == {'was answered'}


def test_consumed_texts_extracts_block_array_text(tmp_path, monkeypatch):
    tx = tmp_path / 't.jsonl'
    _write(tx, [_user([
        {'type': 'text', 'text': 'part one'},
        {'type': 'image', 'source': {}},        # non-text block skipped
        {'type': 'text', 'text': 'part two'},
    ])])
    monkeypatch.setattr('lib.trace.live_rescan._find_main_transcript',
                        lambda tid: str(tx))
    assert qp.consumed_prompt_texts('t') == {'part one part two'}


def test_consumed_texts_no_transcript_returns_empty(monkeypatch):
    monkeypatch.setattr('lib.trace.live_rescan._find_main_transcript',
                        lambda tid: None)
    assert qp.consumed_prompt_texts('missing') == set()


def test_consumed_texts_degrades_on_invalid_utf8(tmp_path, monkeypatch):
    # A partial-write transcript with a non-UTF-8 byte must not escape (the
    # call is unguarded in _merge_bridge_steers → would 500 the live poll).
    tx = tmp_path / 't.jsonl'
    tx.write_bytes(b'{"type":"user","message":{"content":"hi"}}\n\xff\xfe bad\n')
    monkeypatch.setattr('lib.trace.live_rescan._find_main_transcript',
                        lambda tid: str(tx))
    assert qp.consumed_prompt_texts('t') == set()
