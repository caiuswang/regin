"""Every live hook span carries the envelope `prompt_id` as `source_prompt_id`,
and the serve-time graft joins on that value instead of on timestamps."""

from __future__ import annotations

import sqlite3

import pytest

from hook_manager.core import HookPayload
from lib.trace import projection_graft
from lib.trace.projection_graft import _graft_orphans, _graft_orphans_under_prompt
from web import app as app_module

SESSION = 'sess-prompt-id'
PID = 'p1'


@pytest.fixture
def trace_db(tmp_path, monkeypatch):
    db_path = tmp_path / 'trace.db'
    import lib.orm.engine as db_module
    monkeypatch.setattr(db_module, 'DB_PATH', str(db_path))
    db_module.init_db()
    return db_path


@pytest.fixture
def ingest_through_api(trace_db, monkeypatch):
    """Route every hook `post_event` into the real /api/session-spans ingest."""
    app = app_module.create_app()
    app.config['TESTING'] = True
    client = app.test_client()
    import lib.hook_plugin as plugin

    def _post_event(endpoint, data):
        if endpoint != 'session_spans':
            return True
        r = client.post('/api/session-spans', json=data)
        assert r.status_code == 200, r.get_data(as_text=True)
        return True

    monkeypatch.setattr(plugin, 'post_event', _post_event)
    return trace_db


def _payload(event: str, **extras) -> HookPayload:
    raw = {'hook_event_name': event, 'session_id': SESSION, 'prompt_id': PID}
    raw.update(extras)
    return HookPayload.from_stdin_json(event, raw)


def _source_prompt_ids_by_name(db_path) -> dict[str, set]:
    conn = sqlite3.connect(str(db_path))
    try:
        rows = conn.execute(
            "SELECT name, source_prompt_id FROM session_spans WHERE trace_id = ?",
            (SESSION,),
        ).fetchall()
    finally:
        conn.close()
    out: dict[str, set] = {}
    for name, spid in rows:
        out.setdefault(name, set()).add(spid)
    return out


def test_every_hook_span_carries_source_prompt_id(ingest_through_api, tmp_path):
    from hook_manager.handlers import permission_events, prompt_trace, rule_check
    from hook_manager.handlers.memory_recall import _emit_recall_span
    from hook_manager.handlers.turn_trace.entry import _post_turn_model_span
    from lib.settings import settings

    prompt_trace._emit_placeholder(_payload('UserPromptSubmit', prompt='hi'), 'hi')
    _post_turn_model_span(_payload('UserPromptSubmit', prompt='hi'), 'claude-x')
    _post_turn_model_span(_payload('Stop'), 'claude-x')
    _emit_recall_span(_payload('UserPromptSubmit', prompt='hi'), '<block/>', [], 'inline')
    permission_events._emit_span(
        _payload('PermissionRequest', tool_name='Bash', tool_input={'command': 'ls'}),
        'permission.request', status='PENDING')
    pre = _payload('PreToolUse', tool_name='Edit',
                   tool_input={'file_path': str(tmp_path / 'a.py')})
    rule_check._emit_rule_check_span(
        SESSION, str(tmp_path / 'a.py'), str(tmp_path),
        applicable_rules=[], engine_tags=[], total_rules=0, status='ok',
        raw=pre.raw)

    by_name = _source_prompt_ids_by_name(ingest_through_api)
    wanted = {'prompt', 'turn', 'permission.request', 'rule.check'}
    if settings.agent_memory.trace_recall:
        wanted.add('memory.recall')
    assert wanted <= set(by_name), by_name
    for name in wanted:
        assert by_name[name] == {PID}, (name, by_name[name])


def _prompt(span_id, start, pid):
    return {'span_id': span_id, 'parent_id': None, 'name': 'prompt',
            'attributes': {'prompt_id': pid}, 'start_time': start,
            'end_time': None, 'duration_ms': 0}


def test_turn_joins_prompt_by_prompt_id_without_lookahead():
    spans = [
        _prompt('prompt-one', '2026-01-01T00:00:00.000000', 'pid-1'),
        {'span_id': 'turn-2', 'parent_id': None, 'name': 'turn',
         'attributes': {'source_prompt_id': 'pid-2'},
         'start_time': '2026-01-01T00:00:00.295000', 'end_time': None,
         'duration_ms': 0},
        _prompt('prompt-two', '2026-01-01T00:00:00.300000', 'pid-2'),
    ]
    by_id = {s['span_id']: s for s in _graft_orphans(spans)}
    assert by_id['turn-2']['parent_id'] == 'prompt-two'
    assert not hasattr(projection_graft, '_relabel_turns_by_lookahead')
    assert not hasattr(projection_graft, '_TURN_LOOKAHEAD_SECONDS')


def test_orphan_with_prompt_id_never_uses_time_fallback():
    earlier = _prompt('prompt-earlier', '2026-01-01T00:00:00', 'pid-earlier')
    later = _prompt('prompt-later', '2026-01-01T00:05:00', 'pid-later')
    orphan = {'span_id': 'perm-1', 'parent_id': None, 'name': 'permission.request',
              'attributes': {'source_prompt_id': 'pid-later'},
              'start_time': '2026-01-01T00:02:00', 'end_time': None,
              'duration_ms': 0}
    spans = [earlier, orphan, later]
    by_id = {s['span_id']: s for s in spans}
    _graft_orphans_under_prompt(sorted(spans, key=lambda s: s['start_time']), by_id)
    assert by_id['perm-1']['parent_id'] == 'prompt-later'


def test_orphan_with_prompt_id_joins_live_placeholder_before_anchor_lands():
    prev = _prompt('prompt-prev', '2026-01-01T00:00:00', 'pid-prev')
    turn = {'span_id': 'turn-new', 'parent_id': None, 'name': 'turn',
            'attributes': {'source_prompt_id': 'pid-new'},
            'start_time': '2026-01-01T00:01:00.000', 'end_time': None,
            'duration_ms': 0}
    placeholder = {'span_id': 'promptlive-abc', 'parent_id': None, 'name': 'prompt',
                   'status_code': 'PENDING',
                   'attributes': {'live_placeholder': True, 'source_prompt_id': 'pid-new'},
                   'start_time': '2026-01-01T00:01:00.010', 'end_time': None,
                   'duration_ms': 0}
    by_id = {s['span_id']: s for s in _graft_orphans([prev, turn, placeholder])}
    assert by_id['turn-new']['parent_id'] == 'promptlive-abc'


def test_orphan_with_unknown_prompt_id_stays_root():
    prev = _prompt('prompt-prev', '2026-01-01T00:00:00', 'pid-prev')
    rc = {'span_id': 'rc-1', 'parent_id': None, 'name': 'rule.check',
          'attributes': {'source_prompt_id': 'pid-missing'},
          'start_time': '2026-01-01T00:01:00', 'end_time': None, 'duration_ms': 0}
    by_id = {s['span_id']: s for s in _graft_orphans([prev, rc])}
    assert by_id['rc-1']['parent_id'] is None
