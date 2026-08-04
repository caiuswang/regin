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


def _user(content, meta=False, ts=None):
    e = {'type': 'user', 'message': {'role': 'user', 'content': content}}
    if meta:
        e['isMeta'] = True
    if ts:
        e['timestamp'] = ts
    return e


def _notif(task_id, body, ts):
    return _enq(f'<task-notification>\n<task-id>{task_id}</task-id>\n'
                f'{body}\n</task-notification>', ts)


def _rm_targeted(content, ts):
    return {'type': 'queue-operation', 'operation': 'remove',
            'content': content, 'timestamp': ts}


def test_task_notification_reenqueue_supersedes_same_task_id(tmp_path, monkeypatch):
    # Claude Code rewrites a task notification in place when the task stops and
    # logs only the new enqueue; without supersede the first copy is never
    # popped, so the later dequeue takes it and the real prompt stays pinned.
    tx = tmp_path / 't.jsonl'
    _write(tx, [
        _notif('a46597c', '<tool-use-id>toolu_1</tool-use-id>', 't1'),
        _notif('a46597c', '<status>stopped</status>', 't2'),
        _enq('still waiting', 't3'),
        _deq('t4'),                       # consumes the (single) notification
    ])
    monkeypatch.setattr('lib.trace.live_rescan._find_main_transcript',
                        lambda tid: str(tx))
    out = qp.current_queued_prompts('t')
    assert [q['content'] for q in out] == ['still waiting']


def test_targeted_remove_drops_named_item_not_head(tmp_path, monkeypatch):
    tx = tmp_path / 't.jsonl'
    _write(tx, [
        _enq('head prompt', 't1'),
        _enq('named prompt', 't2'),
        _rm_targeted('named  prompt', 't3'),   # whitespace-normalized match
    ])
    monkeypatch.setattr('lib.trace.live_rescan._find_main_transcript',
                        lambda tid: str(tx))
    out = qp.current_queued_prompts('t')
    assert [q['content'] for q in out] == ['head prompt']


def test_targeted_remove_of_unknown_item_keeps_queue(tmp_path, monkeypatch):
    # Parsing can begin mid-stream: evicting the head to account for an item we
    # never saw is what pins the rest of the queue forever.
    tx = tmp_path / 't.jsonl'
    _write(tx, [_enq('kept', 't1'), _rm_targeted('never seen', 't2')])
    monkeypatch.setattr('lib.trace.live_rescan._find_main_transcript',
                        lambda tid: str(tx))
    assert [q['content'] for q in qp.current_queued_prompts('t')] == ['kept']


def test_queued_prompt_retires_once_its_turn_ran(tmp_path, monkeypatch):
    # A lost pop op must not pin a prompt forever: the transcript shows the
    # turn ran, which outranks the op stream.
    tx = tmp_path / 't.jsonl'
    _write(tx, [
        _enq('took too much time', '2026-08-02T17:14:17.896Z'),
        _user('took too much time', ts='2026-08-02T17:14:17.935Z'),
        # no dequeue was ever logged
    ])
    monkeypatch.setattr('lib.trace.live_rescan._find_main_transcript',
                        lambda tid: str(tx))
    assert qp.current_queued_prompts('t') == []


def test_identical_prompt_pairs_one_turn_each(tmp_path, monkeypatch):
    # Two "continue"s are two queue slots; one processed turn retires exactly
    # one of them, never both.
    tx = tmp_path / 't.jsonl'
    _write(tx, [
        _enq('continue', '2026-08-02T10:00:00.000Z'),
        _enq('continue', '2026-08-02T10:00:05.000Z'),
        _user('continue', ts='2026-08-02T10:00:01.000Z'),
    ])
    monkeypatch.setattr('lib.trace.live_rescan._find_main_transcript',
                        lambda tid: str(tx))
    out = qp.current_queued_prompts('t')
    assert [q['enqueued_at'] for q in out] == ['2026-08-02T10:00:05.000Z']


def test_task_id_in_a_user_prompt_never_supersedes(tmp_path, monkeypatch):
    # Supersede-by-id is only right for the notifications Claude Code rewrites
    # in place; a prompt that merely quotes a task id must evict nothing.
    tx = tmp_path / 't.jsonl'
    _write(tx, [
        _notif('abc', '<tool-use-id>toolu_1</tool-use-id>', 't1'),
        _enq('what is <task-id>abc</task-id> doing?', 't2'),
        _enq('and <task-id>abc</task-id> again?', 't3'),
    ])
    monkeypatch.setattr('lib.trace.live_rescan._find_main_transcript',
                        lambda tid: str(tx))
    out = qp.current_queued_prompts('t')
    assert [q['enqueued_at'] for q in out] == ['t2', 't3']


def test_untimestamped_enqueue_does_not_block_its_siblings(tmp_path, monkeypatch):
    # An unorderable event keeps its own place but must not consume the
    # pairing cursor, or every later twin is pinned with it.
    tx = tmp_path / 't.jsonl'
    _write(tx, [
        _enq('continue', None),
        _enq('continue', '2026-08-02T10:00:00.000Z'),
        _user('continue', ts='2026-08-02T10:00:01.000Z'),
    ])
    monkeypatch.setattr('lib.trace.live_rescan._find_main_transcript',
                        lambda tid: str(tx))
    out = qp.current_queued_prompts('t')
    assert [q['enqueued_at'] for q in out] == [None]


def test_turn_older_than_enqueue_does_not_retire(tmp_path, monkeypatch):
    # An identical prompt processed BEFORE this one was queued is a different
    # turn — the queued copy is still waiting.
    tx = tmp_path / 't.jsonl'
    _write(tx, [
        _user('continue', ts='2026-08-02T09:00:00.000Z'),
        _enq('continue', '2026-08-02T10:00:00.000Z'),
    ])
    monkeypatch.setattr('lib.trace.live_rescan._find_main_transcript',
                        lambda tid: str(tx))
    assert [q['content'] for q in qp.current_queued_prompts('t')] == ['continue']


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


_EXIT_COMMAND_TURN = ('<command-name>/exit</command-name>\n'
                      '            <command-message>exit</command-message>\n'
                      '            <command-args></command-args>')


def test_consumed_texts_include_the_typed_form_of_a_command_turn(
        tmp_path, monkeypatch):
    # An executed slash command logs as command XML, never as the "/exit" the
    # sender typed — both forms must count as consumed or the bridge steer
    # chip outlives the command for its whole window.
    tx = tmp_path / 't.jsonl'
    _write(tx, [_user(_EXIT_COMMAND_TURN)])
    monkeypatch.setattr('lib.trace.live_rescan._find_main_transcript',
                        lambda tid: str(tx))
    assert '/exit' in qp.consumed_prompt_texts('t')


def test_command_args_join_the_reconstructed_typed_form(tmp_path, monkeypatch):
    tx = tmp_path / 't.jsonl'
    _write(tx, [_user('<command-name>/goal</command-name>\n'
                      '<command-message>goal</command-message>\n'
                      '<command-args>fix the bug</command-args>')])
    monkeypatch.setattr('lib.trace.live_rescan._find_main_transcript',
                        lambda tid: str(tx))
    assert '/goal fix the bug' in qp.consumed_prompt_texts('t')


def test_queued_slash_command_retires_once_its_command_turn_ran(
        tmp_path, monkeypatch):
    tx = tmp_path / 't.jsonl'
    _write(tx, [
        _enq('/exit', '2026-08-04T07:12:00.000Z'),
        _user(_EXIT_COMMAND_TURN, ts='2026-08-04T07:20:15.038Z'),
    ])
    monkeypatch.setattr('lib.trace.live_rescan._find_main_transcript',
                        lambda tid: str(tx))
    assert qp.current_queued_prompts('t') == []


def test_a_prompt_quoting_command_xml_is_not_a_command_turn(
        tmp_path, monkeypatch):
    # Only the reconstructed line joins the consumed set — a user ASKING about
    # command XML mid-sentence registers verbatim, and "/exit" alone must not
    # retire against it.
    tx = tmp_path / 't.jsonl'
    _write(tx, [
        _enq('/exit', '2026-08-04T07:00:00.000Z'),
        _user('why does <command-name>compact</command-name> hang?',
              ts='2026-08-04T07:00:01.000Z'),
    ])
    monkeypatch.setattr('lib.trace.live_rescan._find_main_transcript',
                        lambda tid: str(tx))
    assert [q['content'] for q in qp.current_queued_prompts('t')] == ['/exit']
