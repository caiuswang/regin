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


def test_throttle_gate_sees_subagent_freshness(tmp_path, monkeypatch):
    """`_should_skip_rescan` must NOT skip when a subagent transcript changed
    even though the MAIN file is static — otherwise a subagent streaming while
    the main agent sits blocked on an Agent tool stales up to 10s."""
    main = tmp_path / 'sess.jsonl'
    main.write_text('{}\n')
    subdir = tmp_path / 'sess' / 'subagents'
    subdir.mkdir(parents=True)
    sub = subdir / 'agent-abc123.jsonl'
    sub.write_text('{}\n')
    monkeypatch.setattr(lr, '_find_main_transcript', lambda tid: str(main))
    lr._rescan_gate.clear()

    # Record the gate at the current max mtime, well within the interval.
    lr._record_rescan_gate('t-thr', str(main))
    assert lr._should_skip_rescan('t-thr', str(main)) is True   # nothing changed

    # Main stays static; only the subagent transcript advances.
    future = 9_999_999_999
    os.utime(sub, (future, future))
    assert lr._should_skip_rescan('t-thr', str(main)) is False  # subagent fresh
    lr._rescan_gate.clear()


class _ForeignProvider:
    """A non-Claude-shaped session (Kimi): one event-sourced transcript per
    agent, the subagents in sibling directories rather than under the main
    file."""

    transcript_format = 'wire'

    def __init__(self, subagents):
        self.subagents = [str(p) for p in subagents]
        self.reconciled: list[bool] = []

    def subagent_transcript_paths(self, _main_path):
        return self.subagents

    def reconcile_subagents(self, _session_id, *, live=False):
        self.reconciled.append(live)


def test_foreign_rescan_renests_subagents_while_they_run(tmp_path, monkeypatch):
    """Kimi fires a subagent's tool hooks under the PARENT session, so until the
    reconciler stamps `agent_id` those calls read as the main agent's and the
    subagent is absent from the roster. Hanging that off the lifecycle hooks
    alone landed it only after the subagent had finished — the poll has to
    re-nest a RUNNING one, and must say so (`live=True`) so the reconciler
    doesn't record an end for it."""
    main = tmp_path / 'main' / 'wire.jsonl'
    main.parent.mkdir(parents=True)
    main.write_text('{}\n')
    sub = tmp_path / 'agent-0' / 'wire.jsonl'
    sub.parent.mkdir(parents=True)
    sub.write_text('{}\n')
    provider = _ForeignProvider([sub])

    monkeypatch.setattr(lr, '_find_main_transcript', lambda tid: str(main))
    monkeypatch.setattr(lr, '_session_provider', lambda tid: provider)
    monkeypatch.setattr(
        'hook_manager.handlers.turn_trace.entry._ingest_transcript_usage',
        lambda *a, **k: None,
    )
    lr._last_mtime.clear()
    lr._rescan_gate.clear()
    lr._running.clear()

    lr._do_rescan('t-foreign')
    assert provider.reconciled == [True]

    lr._do_rescan('t-foreign')          # nothing changed → no repeat reconcile
    assert provider.reconciled == [True]

    future = 9_999_999_999
    os.utime(sub, (future, future))
    lr._do_rescan('t-foreign')          # the subagent wrote again → re-nest
    assert provider.reconciled == [True, True]
    lr._last_mtime.clear()
    lr._rescan_gate.clear()


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


def test_trigger_rescan_resolves_an_sdk_run_to_its_child_session(monkeypatch):
    """A regin-launched run is viewable under its own `sdk-…` id, but the
    transcript on disk is named for the CHILD session — so an unresolved
    `sdk-…` finds no file, the rescan silently no-ops, and assistant text
    (which has no hook) never lands for as long as the operator watches that
    URL. The spans the rescan posts must be keyed on the child too, or the
    child's transcript is ingested under the run's id.
    """
    lr._running.clear()
    monkeypatch.setattr(lr, 'canonical_trace_id',
                        lambda tid: 'child-abc' if tid == 'sdk-abc' else tid)
    monkeypatch.setattr(lr, '_find_main_transcript', lambda tid: None)
    started = []

    class _FakeThread:
        def __init__(self, target, args, daemon):
            self._trace = args[0]

        def start(self):
            started.append(self._trace)

    monkeypatch.setattr(lr.threading, 'Thread', _FakeThread)
    lr.trigger_rescan('sdk-abc')
    lr.trigger_rescan('plain-session')

    assert started == ['child-abc', 'plain-session']
    lr._running.clear()


def test_canonical_trace_id_degrades_to_the_id_it_was_given(monkeypatch):
    """A rescan must never raise: an unreachable or pre-migration DB leaves
    the caller with the single-trace behaviour, not an exception on a poll."""
    from lib.trace import alias

    def _boom():
        raise RuntimeError('db gone')

    monkeypatch.setattr('lib.orm.engine.get_connection', _boom)
    assert alias.canonical_trace_id('sdk-abc') == 'sdk-abc'
    assert alias.canonical_trace_id('') == ''


def test_an_ordinary_session_never_pays_for_the_alias_lookup(monkeypatch):
    """The resolution runs on the request thread AHEAD of the rescan throttle,
    so asking unconditionally would put a DB connect+query on every poll of
    every viewer of every session — exactly what that throttle exists to
    avoid. Only regin mints the `sdk-` prefix, and every other id (including
    the child's) is already canonical."""
    lr._running.clear()
    asked = []
    monkeypatch.setattr(lr, 'canonical_trace_id',
                        lambda tid: asked.append(tid) or tid)
    monkeypatch.setattr(lr, '_find_main_transcript', lambda tid: None)
    monkeypatch.setattr(lr.threading, 'Thread',
                        lambda **k: type('T', (), {'start': lambda self: None})())

    lr.trigger_rescan('77777777-8888-9999-aaaa-bbbbbbbbbbbb')
    lr._running.clear()
    lr.trigger_rescan('sdk-abc')
    lr._running.clear()

    assert asked == ['sdk-abc']
