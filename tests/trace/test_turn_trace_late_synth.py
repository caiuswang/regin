"""Regression: a tool deny / interrupt that lands AFTER the turn's text
first posted must still emit its synth span.

A turn's assistant text is written before the user acts on a blocking tool
(permission prompt, or an interrupt). If the turn were marked `seen` on the
first scan — before the deny/interrupt status arrived — the next scan would
skip the whole turn and `_emit_deny_and_error_spans` would never run, so the
denied/cancelled call would have no span at all. Observed in the wild as a
rejected `git branch` Bash call vanishing from the trace.

Fix: a turn is cached only once every tool call reaches a terminal state
(`is_error` set, or `interrupted`). Until then it stays unseen and is
reprocessed (all posts are idempotent).
"""

from __future__ import annotations

from hook_manager.handlers.turn_trace.cache import _load_seen
from hook_manager.handlers.turn_trace.span_posters import _post_live_turn_data
from lib import hook_plugin
from lib.trace.transcript_models import TurnUsage

_UUID = 'b3bea7aa-f558-4516-a9b3-f459e31c7f63'
_HARD_DENY = (
    "The user doesn't want to proceed with this tool use. The tool use was "
    "rejected (eg. if it was a file edit, the new_string was NOT written)."
)


def _turn(tool_calls):
    return TurnUsage(
        model='claude-opus-4-8',
        input_tokens=2, output_tokens=40,
        cache_read_tokens=10, cache_creation_tokens=0,
        uuid=_UUID, timestamp='2026-06-19T16:39:50.000Z',
        text="I'm on master, so I'll branch first.",
        tool_calls=tuple(tool_calls),
    )


def _run(trace_id, turn, recorder, monkeypatch):
    monkeypatch.setattr(hook_plugin, 'post_span', recorder)
    monkeypatch.setattr(hook_plugin, 'post_event', lambda *a, **k: True)
    _post_live_turn_data(
        trace_id, [turn], 'claude-opus-4-8',
        capture_text=True, seen=_load_seen(trace_id), max_text_bytes=50_000,
    )


def test_deny_landing_after_first_scan_still_synthesizes(tmp_path, monkeypatch):
    monkeypatch.setenv('REGIN_TURN_TRACE_STATE_DIR', str(tmp_path / 'state'))
    tid = 'trace-late-deny'
    posted: list[dict] = []

    def rec(**kw):
        posted.append(kw)
        return True

    # Scan 1: the Bash tool_use is still pending (awaiting permission) —
    # is_error is None. The turn must NOT be cached, or scan 2 is locked out.
    pending = {'id': 'toolu_014eseW2Tu', 'name': 'Bash',
               'is_error': None, 'server_side': False}
    _run(tid, _turn([pending]), rec, monkeypatch)
    assert _UUID not in _load_seen(tid), \
        'a turn with a still-pending tool must stay unseen'

    # Scan 2: the user denied; the tool_result now carries is_error=True +
    # the deny sentinel. The deny synth must fire even though the turn's
    # text already posted on scan 1.
    posted.clear()
    denied = {'id': 'toolu_014eseW2Tu', 'name': 'Bash', 'is_error': True,
              'server_side': False, 'result_text': _HARD_DENY,
              'tool_input': {'command': 'git checkout -b feat/subtabs'}}
    _run(tid, _turn([denied]), rec, monkeypatch)

    deny = [p for p in posted if str(p.get('span_id', '')).startswith('tooldeny-')]
    assert deny, 'deny synth span must be posted on the later scan'
    assert deny[0]['attributes'].get('denied') is True
    assert _UUID in _load_seen(tid), 'a resolved turn should now be cached'


def test_text_interrupt_synthesizes_flagged_span(tmp_path, monkeypatch):
    monkeypatch.setenv('REGIN_TURN_TRACE_STATE_DIR', str(tmp_path / 'state'))
    tid = 'trace-interrupt'
    posted: list[dict] = []

    def rec(**kw):
        posted.append(kw)
        return True

    # A bare-text interrupt (no tool_result): transcript_usage flags the
    # call `interrupted=True` with is_error still None. The synth must emit a
    # flagged `toolintr-` span carrying the cancel flag and command preview.
    call = {'id': 'toolu_014eseW2Tu', 'name': 'Bash', 'is_error': None,
            'server_side': False, 'interrupted': True,
            'tool_input': {'command': 'git checkout -b feat/subtabs'}}
    _run(tid, _turn([call]), rec, monkeypatch)

    intr = [p for p in posted if str(p.get('span_id', '')).startswith('toolintr-')]
    assert intr, 'interrupt synth span must be posted'
    a = intr[0]['attributes']
    assert a.get('interrupted') is True and a.get('is_interrupt') is True
    assert a.get('command_preview') == 'git checkout -b feat/subtabs'
    assert intr[0]['name'] == 'tool.Bash'
    assert _UUID in _load_seen(tid), \
        'an interrupted turn is terminal, so it should be cached'
