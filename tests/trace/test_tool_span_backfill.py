"""Transcript tool-span backfill + user-interrupt capture (lib/trace/repair.py),
subagent launch-prompt capture (subagent_lifecycle), rescan throttle
(live_rescan), and bridge-steer queued-prompt coverage (trace/sessions)."""

from __future__ import annotations

import json

import pytest

import lib.orm.engine as db_module
import lib.hook_plugin as hook_plugin
from lib.trace import repair


def _write_transcript(path, entries):
    path.write_text(
        '\n'.join(json.dumps(e, separators=(',', ':')) for e in entries) + '\n')


def _assistant(uuid, ts, blocks):
    return {'type': 'assistant', 'uuid': uuid, 'parentUuid': None,
            'timestamp': ts, 'message': {'id': 'm-' + uuid, 'model': 'x',
                                         'content': blocks}}


def _tool_use(tid, name, inp):
    return {'type': 'tool_use', 'id': tid, 'name': name, 'input': inp}


def _tool_result(uuid, parent, ts, tuid, content, is_error=False):
    block = {'type': 'tool_result', 'tool_use_id': tuid, 'content': content}
    if is_error:
        block['is_error'] = True
    return {'type': 'user', 'uuid': uuid, 'parentUuid': parent,
            'timestamp': ts, 'message': {'content': [block]}}


def _fake_post_span_factory():
    def fake_post_span(**kw):
        span_id = kw.get('span_id')
        if not span_id:
            return True
        attrs = kw.get('attributes') or {}
        with db_module.get_connection() as conn:
            conn.execute(
                """INSERT OR REPLACE INTO session_spans
                   (trace_id, span_id, parent_id, name, kind, start_time,
                    end_time, duration_ms, attributes, status_code, tool_use_id)
                   VALUES (?, ?, ?, ?, 'internal', ?, ?, 0, ?, ?, ?)""",
                (kw['trace_id'], span_id, kw.get('parent_id'), kw['name'],
                 kw.get('start_time'), kw.get('end_time'),
                 json.dumps(attrs, ensure_ascii=False),
                 kw.get('status_code', 'OK'), attrs.get('tool_use_id')),
            )
            conn.commit()
        return True
    return fake_post_span


def _insert_pending(trace_id, tuid, name='tool.Bash'):
    from lib.trace.pending_spans import tool_pending_id
    with db_module.get_connection() as conn:
        conn.execute(
            """INSERT INTO session_spans
               (trace_id, span_id, parent_id, name, kind, start_time, end_time,
                duration_ms, attributes, status_code)
               VALUES (?, ?, NULL, ?, 'internal', ?, ?, 0, ?, 'PENDING')""",
            (trace_id, tool_pending_id(tuid), name,
             '2020-01-01T00:00:00', '2020-01-01T00:00:00',
             json.dumps({'tool_use_id': tuid, 'tool_name': name[len('tool.'):]})),
        )
        conn.commit()


def _spans(trace_id):
    with db_module.get_connection() as conn:
        return conn.execute(
            "SELECT span_id, name, status_code, attributes FROM session_spans "
            "WHERE trace_id = ? ORDER BY span_id", (trace_id,)).fetchall()


def _build_task_transcript(path):
    _write_transcript(path, [
        _assistant('a1', '2026-01-01T10:00:00Z', [
            _tool_use('tu_create1', 'TaskCreate',
                      {'subject': 'Do X', 'activeForm': 'Doing X'})]),
        _tool_result('u1', 'a1', '2026-01-01T10:00:01Z', 'tu_create1',
                     'Task #1 created successfully: Do X'),
        _assistant('a2', '2026-01-01T10:05:00Z', [
            _tool_use('tu_upd1', 'TaskUpdate',
                      {'taskId': '1', 'status': 'completed'})]),
        _tool_result('u2', 'a2', '2026-01-01T10:05:01Z', 'tu_upd1', 'ok'),
        _assistant('a3', '2026-01-01T10:06:00Z', [
            _tool_use('tu_bash', 'Bash',
                      {'command': 'echo hi', 'description': 'say hi'})]),
        _tool_result('u3', 'a3', '2026-01-01T10:06:01Z', 'tu_bash', 'hi'),
    ])


@pytest.fixture
def _patched(monkeypatch):
    monkeypatch.setattr(hook_plugin, 'post_span', _fake_post_span_factory())
    monkeypatch.setattr(repair, '_agent_transcripts', lambda tid: [],
                        raising=False)
    monkeypatch.setattr('lib.trace.claude_subagents._agent_transcripts',
                        lambda tid: [])


def test_backfill_recovers_tasks_and_bash_and_is_idempotent(
        tmp_path, monkeypatch, _patched):
    trace_id = 'bf-tasks-1'
    transcript = tmp_path / f'{trace_id}.jsonl'
    _build_task_transcript(transcript)
    monkeypatch.setattr(repair, '_find_transcript', lambda t: str(transcript))

    first = repair.backfill_transcript_tool_spans(trace_id)
    assert first['spans_backfilled'] == 3          # create + update + bash
    names = {r['name'] for r in _spans(trace_id)}
    assert {'tool.TaskCreate', 'tool.TaskUpdate', 'tool.Bash'} <= names

    # TaskCreate recovered its task_id from the "Task #1 created" result text —
    # without it the fold in _fetch_session_task_list drops the event.
    create = next(r for r in _spans(trace_id) if r['name'] == 'tool.TaskCreate')
    assert json.loads(create['attributes'])['task_id'] == '1'

    before = _spans(trace_id)
    second = repair.backfill_transcript_tool_spans(trace_id)
    assert second['spans_backfilled'] == 0          # idempotent
    assert _spans(trace_id) == before


def test_backfill_resolves_stuck_pending_via_merge(
        tmp_path, monkeypatch, _patched):
    trace_id = 'bf-pending-1'
    transcript = tmp_path / f'{trace_id}.jsonl'
    _write_transcript(transcript, [
        _assistant('a1', '2026-01-01T10:00:00Z', [
            _tool_use('tu_stuck', 'Bash', {'command': 'pkill x'})]),
        _tool_result('u1', 'a1', '2026-01-01T10:00:02Z', 'tu_stuck', 'done'),
    ])
    monkeypatch.setattr(repair, '_find_transcript', lambda t: str(transcript))
    _insert_pending(trace_id, 'tu_stuck')

    repair.backfill_transcript_tool_spans(trace_id)

    from lib.trace.merge import merge_spans
    rows = []
    for r in _spans(trace_id):
        rows.append({'trace_id': trace_id, 'span_id': r['span_id'],
                     'name': r['name'], 'status_code': r['status_code'],
                     'attributes': json.loads(r['attributes']),
                     'start_time': '2026-01-01T10:00:00'})
    merged = merge_spans(rows)
    ids = {s['span_id'] for s in merged}
    from lib.trace.pending_spans import tool_pending_id
    assert tool_pending_id('tu_stuck') not in ids   # pending retired
    assert 'bftool-tu_stuck' in ids                 # resolved twin present


def test_backfill_skips_running_tool_without_result(
        tmp_path, monkeypatch, _patched):
    trace_id = 'bf-running-1'
    transcript = tmp_path / f'{trace_id}.jsonl'
    _write_transcript(transcript, [
        _assistant('a1', '2026-01-01T10:00:00Z', [
            _tool_use('tu_running', 'Bash', {'command': 'sleep 999'})]),
    ])
    monkeypatch.setattr(repair, '_find_transcript', lambda t: str(transcript))
    _insert_pending(trace_id, 'tu_running')

    result = repair.backfill_transcript_tool_spans(trace_id)
    assert result['spans_backfilled'] == 0
    assert 'bftool-tu_running' not in {r['span_id'] for r in _spans(trace_id)}


def test_backfill_flags_user_interrupt(tmp_path, monkeypatch, _patched):
    trace_id = 'bf-interrupt-1'
    transcript = tmp_path / f'{trace_id}.jsonl'
    _write_transcript(transcript, [
        _assistant('a1', '2026-01-01T10:00:00Z', [
            _tool_use('tu_int', 'Bash', {'command': 'long-thing'})]),
        _tool_result('u1', 'a1', '2026-01-01T10:00:05Z', 'tu_int',
                     [{'type': 'text',
                       'text': '[Request interrupted by user for tool use]'}],
                     is_error=True),
    ])
    monkeypatch.setattr(repair, '_find_transcript', lambda t: str(transcript))
    _insert_pending(trace_id, 'tu_int')

    repair.backfill_transcript_tool_spans(trace_id)
    span = next(r for r in _spans(trace_id) if r['span_id'] == 'bftool-tu_int')
    attrs = json.loads(span['attributes'])
    assert span['status_code'] == 'ERROR'
    assert attrs['is_interrupt'] is True
    assert attrs['interrupt_source'] == repair.INTERRUPT_SOURCE_USER


def test_has_stuck_pending_tools_age_gate(monkeypatch):
    trace_id = 'bf-gate-1'
    _insert_pending(trace_id, 'tu_gate')       # start_time 2020 → old
    assert repair.has_stuck_pending_tools(trace_id, older_than_sec=60) is True
    # A far-future threshold means "older than <now - 100yr>" — always true for
    # a 2020 row; a zero-age gate is also true. Confirm a clean trace is False.
    assert repair.has_stuck_pending_tools('bf-gate-none') is False


# ── Subagent launch-prompt capture ──────────────────────────────────

def test_subagent_prompt_span_emitted_once(tmp_path, monkeypatch):
    from hook_manager.handlers import subagent_lifecycle as sl
    trace_id = 'sa-prompt-1'
    agent_id = 'a1234567'
    sub = tmp_path / f'agent-{agent_id}.jsonl'
    _write_transcript(sub, [
        {'type': 'user', 'uuid': 'p0', 'parentUuid': None,
         'timestamp': '2026-01-01T10:00:00Z',
         'message': {'content': 'You are the BUILDER. Do the thing.'}},
        _assistant('sa1', '2026-01-01T10:00:05Z',
                   [{'type': 'text', 'text': 'on it'}]),
    ])
    posted = []
    monkeypatch.setattr('lib.hook_plugin.post_span',
                        lambda **k: posted.append(k) or True)

    sl._emit_subagent_prompt(trace_id, str(sub), agent_id)
    sl._emit_subagent_prompt(trace_id, str(sub), agent_id)   # idempotent id
    prompt_posts = [p for p in posted if p.get('span_id') == f'prompt-sa-{agent_id}']
    assert len(prompt_posts) == 2                       # same deterministic id
    assert all(p['name'] == 'prompt' for p in prompt_posts)
    attrs = prompt_posts[0]['attributes']
    assert attrs['agent_id'] == agent_id
    assert attrs['text'].startswith('You are the BUILDER')


def test_subagent_resumable_emits_prompt_only_on_first_scan(tmp_path, monkeypatch):
    from hook_manager.handlers import subagent_lifecycle as sl
    sub = tmp_path / 'agent-b9.jsonl'
    _write_transcript(sub, [
        {'type': 'user', 'uuid': 'p0', 'parentUuid': None,
         'timestamp': '2026-01-01T10:00:00Z',
         'message': {'content': 'task text'}},
    ])
    calls = {'n': 0}
    monkeypatch.setattr(sl, '_emit_subagent_prompt',
                        lambda *a, **k: calls.__setitem__('n', calls['n'] + 1))
    monkeypatch.setattr(sl, '_subagent_capture', lambda p, a: (False, None))

    sl.emit_subagent_responses_resumable('t', str(sub), 'b9', None)
    sl.emit_subagent_responses_resumable('t', str(sub), 'b9', object())
    assert calls['n'] == 1                              # only the state=None scan


# ── Rescan throttle ─────────────────────────────────────────────────

def test_rescan_throttle_skips_unchanged_within_interval(tmp_path, monkeypatch):
    import time as _time
    import lib.trace.live_rescan as lr
    main = tmp_path / 'sess.jsonl'
    main.write_text('{}\n')
    lr._rescan_gate.clear()

    assert lr._should_skip_rescan('t1', str(main)) is False   # never scanned
    lr._record_rescan_gate('t1', str(main))
    assert lr._should_skip_rescan('t1', str(main)) is True     # unchanged+recent

    future = 9_999_999_999
    import os
    os.utime(main, (future, future))
    assert lr._should_skip_rescan('t1', str(main)) is False    # mtime changed

    lr._record_rescan_gate('t1', str(main))
    lr._rescan_gate['t1'] = (lr._rescan_gate['t1'][0],
                             _time.monotonic() - lr._MIN_RESCAN_INTERVAL_SEC - 1)
    assert lr._should_skip_rescan('t1', str(main)) is False    # past interval
    lr._rescan_gate.clear()


def test_trigger_rescan_throttle_prevents_spawn(tmp_path, monkeypatch):
    import lib.trace.live_rescan as lr
    main = tmp_path / 'sess.jsonl'
    main.write_text('{}\n')
    lr._rescan_gate.clear()
    lr._running.clear()
    lr._record_rescan_gate('tX', str(main))
    monkeypatch.setattr(lr, '_find_main_transcript', lambda t: str(main))
    started = []
    monkeypatch.setattr(
        lr.threading, 'Thread',
        lambda **k: type('T', (), {'start': lambda self: started.append(1)})())
    lr.trigger_rescan('tX')
    assert started == []            # unchanged + recent → throttled
    lr._rescan_gate.clear()
    lr._running.clear()


# ── Bridge-steer queued coverage ────────────────────────────────────

def test_merge_bridge_steers_dedups_and_appends(monkeypatch):
    from web.blueprints.trace import sessions as s
    monkeypatch.setattr(
        s, '_recent_bridge_steers',
        lambda tid: [{'content': 'already queued', 'delivered_at': '2026-01-01 10:00:00'},
                     {'content': 'brand new steer', 'delivered_at': '2026-01-01 10:00:05'}])
    queued = [{'content': 'already queued', 'enqueued_at': 'x'}]
    out = s._merge_bridge_steers('t', queued)
    # transcript copy kept once; the un-queued steer appended tagged 'bridge'
    assert sum(1 for q in out if s._steer_key(q['content']) == 'already queued') == 1
    bridge = [q for q in out if q.get('source') == 'bridge']
    assert len(bridge) == 1 and bridge[0]['content'] == 'brand new steer'


def test_merge_bridge_steers_suppresses_consumed_steer(monkeypatch):
    # A steer already answered leaves the pending queue but is still inside the
    # delivery window; without the consumed-turn dedup its chip re-surfaces.
    from web.blueprints.trace import sessions as s
    monkeypatch.setattr(
        s, '_recent_bridge_steers',
        lambda tid: [{'content': 'was  answered', 'delivered_at': '2026-01-01 10:00:00'},
                     {'content': 'still pending', 'delivered_at': '2026-01-01 10:00:05'}])
    monkeypatch.setattr('lib.trace.queued_prompts.consumed_prompt_texts',
                        lambda tid: {'was answered'})  # normalized transcript turn
    out = s._merge_bridge_steers('t', [])
    bridge = [q['content'] for q in out if q.get('source') == 'bridge']
    assert bridge == ['still pending']  # consumed steer dropped, pending kept


def test_recent_bridge_steers_window_and_delivered_gate(monkeypatch):
    from datetime import datetime, timedelta, timezone
    from web.blueprints.trace import sessions as s

    class _Settings:
        class agent_bridge:
            enabled = True
    monkeypatch.setattr('lib.settings.settings', _Settings, raising=False)
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    recent = now.strftime('%Y-%m-%d %H:%M:%S')
    stale = (now - timedelta(seconds=s._BRIDGE_STEER_WINDOW_SEC + 30)
             ).strftime('%Y-%m-%d %H:%M:%S')
    rows = [
        {'body': 'fresh', 'delivered': 1, 'delivered_at': recent},
        {'body': 'old', 'delivered': 1, 'delivered_at': stale},
        {'body': 'undelivered', 'delivered': 0, 'delivered_at': recent},
    ]
    monkeypatch.setattr('lib.agent_bridge.store.list_bridge_messages',
                        lambda tid, limit=20: rows)
    out = s._recent_bridge_steers('t')
    bodies = {o['content'] for o in out}
    assert bodies == {'fresh'}
