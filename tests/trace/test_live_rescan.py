"""Live transcript rescan orchestration (lib/trace/live_rescan.py).

The actual scan/emit is exercised elsewhere (turn_trace + subagent tests);
here we pin the orchestration: it scans the main transcript + every subagent
transcript, gates re-reads on mtime, and dedupes concurrent triggers.
"""

from __future__ import annotations

import os

import lib.trace.live_rescan as lr


def test_do_rescan_scans_main_and_subagents_then_mtime_gates(tmp_path, monkeypatch):
    main = tmp_path / 'sess.jsonl'
    main.write_text('{}\n')
    subdir = tmp_path / 'sess' / 'subagents'
    subdir.mkdir(parents=True)
    (subdir / 'agent-abc123.jsonl').write_text('{}\n')

    monkeypatch.setattr(lr, '_find_main_transcript', lambda tid: str(main))
    calls = {'main': 0, 'sub': []}

    def _fake_resumable(tid, path, state, **k):
        calls['main'] += 1
        return state  # returned value is stored back into _scan_states

    monkeypatch.setattr(
        'hook_manager.handlers.turn_trace.entry.ingest_transcript_usage_resumable',
        _fake_resumable,
    )
    def _fake_sub_resumable(tid, p, aid, state, **k):
        calls['sub'].append(aid)
        return state

    monkeypatch.setattr(
        'hook_manager.handlers.subagent_lifecycle.emit_subagent_responses_resumable',
        _fake_sub_resumable,
    )
    monkeypatch.setattr(
        'hook_manager.handlers.turn_trace.cache._load_seen', lambda tid: set(),
    )
    lr._last_mtime.clear()
    lr._scan_states.clear()
    lr._sub_scan_states.clear()
    lr._running.clear()

    lr._do_rescan('t1')
    assert calls['main'] == 1
    assert calls['sub'] == ['abc123']          # agent_id parsed from filename

    lr._do_rescan('t1')                          # nothing changed -> mtime gate skips
    assert calls['main'] == 1
    assert calls['sub'] == ['abc123']

    future = 9_999_999_999
    os.utime(main, (future, future))
    os.utime(subdir / 'agent-abc123.jsonl', (future, future))
    lr._do_rescan('t1')                          # mtimes bumped -> rescans
    assert calls['main'] == 2
    assert calls['sub'] == ['abc123', 'abc123']
    lr._last_mtime.clear()
    lr._scan_states.clear()
    lr._sub_scan_states.clear()


def test_bound_tracked_evicts_least_recently_rescanned():
    lr._scan_states.clear()
    lr._sub_scan_states.clear()
    for i in range(lr._MAX_TRACKED + 5):
        lr._scan_states[f't{i}'] = object()
        lr._sub_scan_states[f't{i}'] = {}
    lr._bound_tracked()
    assert len(lr._scan_states) == lr._MAX_TRACKED
    assert len(lr._sub_scan_states) == lr._MAX_TRACKED
    # the 5 oldest were dropped; the newest survive
    assert 't0' not in lr._scan_states
    assert f't{lr._MAX_TRACKED + 4}' in lr._scan_states
    lr._scan_states.clear()
    lr._sub_scan_states.clear()


def test_do_rescan_real_path_posts_spans(tmp_path, monkeypatch):
    """Un-mocked: `_do_rescan` runs the real resumable ingest through to
    post_span. `_do_rescan` swallows exceptions, so a wiring break (bad kwarg)
    would silently stop live updates — this fails loudly instead."""
    import json
    main = tmp_path / 'sess.jsonl'
    with open(main, 'w') as f:
        f.write(json.dumps({"type": "user", "uuid": "p0", "parentUuid": None,
                            "timestamp": "2026-05-20T10:00:00Z",
                            "message": {"content": "hi"}}) + "\n")
        f.write(json.dumps({"type": "assistant", "uuid": "a0", "parentUuid": "p0",
                            "timestamp": "2026-05-20T10:00:05Z",
                            "message": {"id": "m0", "model": "claude-opus-4-7",
                                        "usage": {"input_tokens": 10, "output_tokens": 5,
                                                  "cache_read_input_tokens": 0,
                                                  "cache_creation_input_tokens": 0},
                                        "content": [{"type": "text", "text": "yo"}]}}) + "\n")
    monkeypatch.setenv("REGIN_TURN_TRACE_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setattr(lr, '_find_main_transcript', lambda tid: str(main))
    posted = []
    monkeypatch.setattr('lib.hook_plugin.post_span',
                        lambda *a, **k: posted.append(k.get('span_id')) or True)
    monkeypatch.setattr('lib.hook_plugin.post_event', lambda *a, **k: True)
    lr._last_mtime.clear()
    lr._scan_states.clear()
    lr._sub_scan_states.clear()
    lr._running.clear()

    lr._do_rescan('trace-real')
    assert any(s and s.startswith('resp-') for s in posted), posted
    assert 'trace-real' in lr._scan_states   # state persisted for the next poll
    lr._scan_states.clear()
    lr._sub_scan_states.clear()


def test_do_rescan_noop_when_no_transcript(monkeypatch):
    monkeypatch.setattr(lr, '_find_main_transcript', lambda tid: None)
    # Must not raise even though nothing exists for the trace.
    lr._do_rescan('missing-trace')


def test_trigger_rescan_dedupes_per_trace(monkeypatch):
    lr._running.clear()
    started = []

    class _FakeThread:
        def __init__(self, target, args, daemon):
            self._trace = args[0]

        def start(self):
            started.append(self._trace)  # never runs target -> stays "running"

    monkeypatch.setattr(lr.threading, 'Thread', _FakeThread)
    lr.trigger_rescan('tA')
    lr.trigger_rescan('tA')   # already in-flight -> skipped
    lr.trigger_rescan('tB')
    assert started == ['tA', 'tB']
    lr._running.clear()


def test_trigger_rescan_ignores_empty_trace(monkeypatch):
    started = []
    monkeypatch.setattr(
        lr.threading, 'Thread',
        lambda **k: type('T', (), {'start': lambda self: started.append(1)})(),
    )
    lr.trigger_rescan('')
    assert started == []


def test_do_rescan_selfheals_ghost_markers_only_when_flagged(tmp_path, monkeypatch):
    """The rescan reconstructs lost subagent markers, gated on the cheap
    ghost check — a clean trace must skip the reconstruction entirely."""
    main = tmp_path / 'sess.jsonl'
    main.write_text('{}\n')
    monkeypatch.setattr(lr, '_find_main_transcript', lambda tid: str(main))
    monkeypatch.setattr(
        'hook_manager.handlers.turn_trace.entry.ingest_transcript_usage_resumable',
        lambda tid, path, state, **k: state,
    )
    monkeypatch.setattr(
        'hook_manager.handlers.turn_trace.cache._load_seen', lambda tid: set(),
    )
    ghost = {'value': False}
    calls = {'reconstruct': 0}
    monkeypatch.setattr(
        'lib.trace.repair.has_ghost_agents', lambda tid: ghost['value'])
    monkeypatch.setattr(
        'lib.trace.repair.reconstruct_subagent_markers',
        lambda tid: calls.__setitem__('reconstruct', calls['reconstruct'] + 1))
    lr._last_mtime.clear()
    lr._scan_states.clear()
    lr._sub_scan_states.clear()
    lr._running.clear()

    lr._do_rescan('t-heal')
    assert calls['reconstruct'] == 0            # clean trace: skipped

    ghost['value'] = True
    lr._do_rescan('t-heal')
    assert calls['reconstruct'] == 1            # ghost detected: healed once
