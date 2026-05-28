from __future__ import annotations

import json
import sqlite3

from lib.trace.repair import repair_session_spans


def _write_transcript(path, entries):
    path.write_text('\n'.join(json.dumps(e, separators=(',', ':')) for e in entries) + '\n')


def test_repair_unlocks_turn_with_missing_tool_use_error_child(tmp_path, monkeypatch):
    """A cached assistant turn can still be incomplete when its `resp-*`
    row exists but a synthesized `toolerr-*` child is missing.

    Regression for the real Write-without-Read failure shape: repair used
    to look only for span_ids derived from the turn uuid, so a present
    `assistant_response` row masked the missing child keyed by
    `tool_use_id`.
    """
    import lib.orm.engine as db_module
    import lib.hook_plugin as hook_plugin

    trace_id = 'trace-repair-1'
    turn_uuid = '2747844c-8590-423c-af6d-13559c0d88f3'
    tool_use_id = 'toolu_015guhWd8G6YJbhxSnhX69Ue'
    transcript = tmp_path / f'{trace_id}.jsonl'
    _write_transcript(transcript, [
        {
            'type': 'user',
            'uuid': 'user-1',
            'parentUuid': None,
            'timestamp': '2026-05-20T17:52:00Z',
            'message': {'content': 'write the file'},
        },
        {
            'type': 'assistant',
            'uuid': turn_uuid,
            'parentUuid': 'user-1',
            'timestamp': '2026-05-20T17:52:10Z',
            'message': {
                'id': 'msg-1',
                'model': 'claude-opus-4-7',
                'content': [
                    {'type': 'text', 'text': 'Writing the final artifact.'},
                    {
                        'type': 'tool_use',
                        'id': tool_use_id,
                        'name': 'Write',
                        'input': {
                            'file_path': '/tmp/agent-output.json',
                            'content': '{}',
                        },
                    },
                ],
                'usage': {
                    'input_tokens': 5,
                    'output_tokens': 18,
                    'cache_read_input_tokens': 0,
                    'cache_creation_input_tokens': 0,
                },
            },
        },
        {
            'type': 'user',
            'uuid': 'user-2',
            'parentUuid': turn_uuid,
            'timestamp': '2026-05-20T17:52:15Z',
            'message': {
                'content': [
                    {
                        'type': 'tool_result',
                        'tool_use_id': tool_use_id,
                        'is_error': True,
                        'content': (
                            '<tool_use_error>File has not been read yet. '
                            'Read it first before writing to it.</tool_use_error>'
                        ),
                    },
                ],
            },
        },
    ])

    state_dir = tmp_path / 'state'
    monkeypatch.setenv('REGIN_TURN_TRACE_STATE_DIR', str(state_dir))
    cache_path = state_dir / f'{trace_id}.txt'
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(turn_uuid + '\n')

    with db_module.get_connection() as conn:
        conn.execute(
            """INSERT INTO session_spans
               (trace_id, span_id, parent_id, name, kind,
                start_time, end_time, duration_ms, attributes, status_code)
               VALUES (?, ?, NULL, ?, 'internal', ?, ?, 0, ?, 'OK')""",
            (
                trace_id,
                f'resp-{turn_uuid[:13]}',
                'assistant_response',
                '2026-05-20T21:52:10',
                '2026-05-20T21:52:10',
                json.dumps({'turn_uuid': turn_uuid, 'text': 'Writing the final artifact.'}),
            ),
        )
        conn.commit()

    def fake_post_span(**kw):
        attrs = kw.get('attributes') or {}
        span_id = kw.get('span_id')
        if not span_id:
            return True
        with db_module.get_connection() as conn:
            conn.execute(
                """INSERT OR REPLACE INTO session_spans
                   (trace_id, span_id, parent_id, name, kind,
                    start_time, end_time, duration_ms, attributes,
                    status_code, tool_use_id, turn_uuid)
                   VALUES (?, ?, ?, ?, 'internal', ?, ?, ?, ?, ?, ?, ?)""",
                (
                    kw['trace_id'],
                    span_id,
                    kw.get('parent_id'),
                    kw['name'],
                    kw.get('start_time'),
                    kw.get('end_time'),
                    kw.get('duration_ms'),
                    json.dumps(attrs, ensure_ascii=False),
                    kw.get('status_code', 'OK'),
                    attrs.get('tool_use_id'),
                    attrs.get('turn_uuid'),
                ),
            )
            conn.commit()
        return True

    monkeypatch.setattr(hook_plugin, 'post_span', fake_post_span)
    monkeypatch.setattr(hook_plugin, 'post_event', lambda *_args, **_kwargs: True)
    monkeypatch.setattr('lib.trace.repair._find_transcript', lambda _trace_id: str(transcript))

    result = repair_session_spans(trace_id)

    assert result['ok'] is True
    assert result['uuids_unlocked'] == 1
    assert result['spans_recovered'] == 1

    with db_module.get_connection() as conn:
        row = conn.execute(
            """SELECT span_id, name, status_code, tool_use_id, turn_uuid, attributes
               FROM session_spans
               WHERE trace_id = ? AND span_id = ?""",
            (trace_id, f'toolerr-{tool_use_id[:13]}'),
        ).fetchone()

    assert row is not None
    assert row['name'] == 'tool.Write'
    assert row['status_code'] == 'ERROR'
    assert row['tool_use_id'] == tool_use_id
    assert row['turn_uuid'] == turn_uuid
    attrs = json.loads(row['attributes'])
    assert attrs['reject_kind'] == 'tool_use_error'
    assert attrs['reject_reason'] == (
        'File has not been read yet. Read it first before writing to it.'
    )
