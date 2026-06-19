"""Regression: the seen-uuid cache must not advance past a failed
`turn_usage` post.

`post_event` is best-effort and returns False (never raises) on a
transient ingest outage. If `_post_live_turn_data` marked the scan's
turns seen anyway, those turns' `turn_usage` rows would be lost forever —
the next scan would skip them. For a silent tool-only turn (no
assistant_response / thinking span) the usage row is its ONLY DB
footprint, so the whole turn vanishes. Observed in the wild as sporadic
single-turn gaps in `turn_usage` (e.g. a `send_to_user` tool turn whose
neighbours were captured fine).
"""

from __future__ import annotations

from hook_manager.handlers.turn_trace.cache import _load_seen
from hook_manager.handlers.turn_trace.span_posters import _post_live_turn_data
from lib import hook_plugin
from lib.trace.transcript_models import TurnUsage

_UUID = 'a86093cf-8a86-419d-a545-7adc6568d7a4'


def _turn(**over):
    base = dict(
        model='claude-opus-4-8',
        input_tokens=2, output_tokens=444,
        cache_read_tokens=175493, cache_creation_tokens=1333,
        uuid=_UUID, timestamp='2026-06-15T04:44:47.998Z',
    )
    base.update(over)
    return TurnUsage(**base)


def _run(trace_id, turn, *, usage_ok, monkeypatch):
    monkeypatch.setattr(hook_plugin, 'post_span', lambda **kw: True)
    monkeypatch.setattr(hook_plugin, 'post_event',
                        lambda *a, **k: usage_ok)
    _post_live_turn_data(
        trace_id, [turn], 'claude-opus-4-8',
        capture_text=True, seen=set(), max_text_bytes=50_000,
    )


def test_failed_usage_post_leaves_turn_unseen(tmp_path, monkeypatch):
    monkeypatch.setenv('REGIN_TURN_TRACE_STATE_DIR', str(tmp_path / 'state'))
    # A tool-only turn: no text, no thinking — its usage row is its only
    # durable footprint, so a lost post = a lost turn.
    turn = _turn(text=None, tool_calls=(
        {'id': 'toolu_x', 'name': 'mcp__send-to-user__send_to_user',
         'is_error': None, 'server_side': False},
    ))
    _run('trace-fail', turn, usage_ok=False, monkeypatch=monkeypatch)
    assert _UUID not in _load_seen('trace-fail'), \
        'turn must stay unseen so the next scan retries it'


def test_successful_usage_post_marks_turn_seen(tmp_path, monkeypatch):
    monkeypatch.setenv('REGIN_TURN_TRACE_STATE_DIR', str(tmp_path / 'state'))
    turn = _turn(text='all done')
    _run('trace-ok', turn, usage_ok=True, monkeypatch=monkeypatch)
    assert _UUID in _load_seen('trace-ok'), \
        'a landed usage post should advance the seen cache'
