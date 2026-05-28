"""pytest fixtures for hook_manager tests.

The sys.path wiring that previously lived here is no longer needed —
`pip install -e .` registers the project packages under the venv.
"""

import json
from pathlib import Path

import pytest


_FIXTURE_TRACE_IDS: set[str] = set()


def _wrap_post_event_for_recording(monkeypatch_target):
    """Wrap `lib.hook_plugin.post_event` so every trace_id posted during a
    test gets recorded in `_FIXTURE_TRACE_IDS` for end-of-session cleanup."""
    from lib import hook_plugin
    original = hook_plugin.post_event

    def _recording(endpoint, data, agent_type=None):
        rows = data if isinstance(data, list) else [data]
        for row in rows:
            tid = isinstance(row, dict) and row.get('trace_id')
            if isinstance(tid, str) and tid:
                _FIXTURE_TRACE_IDS.add(tid)
        return original(endpoint, data, agent_type)

    monkeypatch_target.setattr(hook_plugin, 'post_event', _recording)


def _scan_fixture_session_ids() -> set[str]:
    """Subprocess replays don't share Python state, so the wrapper above
    won't see their trace_ids. Anonymized fixtures all share the
    `test-fixture-session-` prefix and a small handful of literals
    (`s1`, `codex-session-1`, etc.) — read each fixture once at session
    start and seed the cleanup set."""
    out: set[str] = set()
    fixture_dir = Path(__file__).parent / 'fixtures'
    if not fixture_dir.is_dir():
        return out
    for path in fixture_dir.glob('*.json'):
        try:
            payload = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        sid = payload.get('session_id') if isinstance(payload, dict) else None
        if isinstance(sid, str) and sid:
            out.add(sid)
    return out


@pytest.fixture(autouse=True)
def _isolate_turn_trace_state(tmp_path, monkeypatch):
    """Point the turn_trace seen-uuid cache at a per-test tmp dir so
    state from earlier tests can't suppress span emission in later
    tests that reuse the same session_id."""
    monkeypatch.setenv('REGIN_TURN_TRACE_STATE_DIR', str(tmp_path / 'turn_trace_state'))


@pytest.fixture(autouse=True)
def _mark_test_spans(monkeypatch):
    """Stamp REGIN_TRACE_TEST=1 so any spans emitted during a test land
    in the DB tagged `is_test=1`, and record every trace_id seen so
    `_end_test_sessions_at_teardown` can close them at suite end.

    Tests under tests/fixtures/ share a hardcoded session_id; without
    this stamp, every replay leaked into the user-visible sessions list
    under that id (see trace_id `45ffacd9-d020-48fc-82ce-1b020ac54716`).
    We intentionally do NOT block the POST itself — some tests rely on
    spans actually landing in the DB so they can be inspected. The
    sessions list filters `is_test=0` by default, so tagged rows stay
    out of the user's normal view but remain available with
    `?include_tests=true`.

    The env var propagates to subprocesses too (test_replay spawns
    `python -m hook_manager`), so subprocess-replayed handlers also
    stamp their spans.
    """
    monkeypatch.setenv('REGIN_TRACE_TEST', '1')
    _wrap_post_event_for_recording(monkeypatch)


@pytest.fixture(scope='session', autouse=True)
def _end_test_sessions_at_teardown():
    """At the end of the test suite, emit a `session.end` span for every
    trace_id seen during the run so the corresponding sessions row
    flips from 'active' to 'ended' rather than lingering as an active
    test session forever.

    Two sources feed `_FIXTURE_TRACE_IDS`:
      * In-process tests: the recording wrapper in `_mark_test_spans`
        captures every trace_id passed to `post_event`.
      * Subprocess tests (`test_subprocess_replay_matches_in_process`
        spawns `python -m hook_manager`): we can't see their writes
        directly, so we pre-load the well-known fixture session_ids by
        scanning the fixtures directory.
    """
    _FIXTURE_TRACE_IDS.update(_scan_fixture_session_ids())
    yield
    if not _FIXTURE_TRACE_IDS:
        return
    import os
    from lib import hook_plugin
    # Per-test monkeypatch reverts before this session-scoped teardown
    # runs, so REGIN_TRACE_TEST is no longer set. Re-set it directly so
    # the closing session.end spans also land tagged is_test=1 —
    # otherwise the teardown itself creates fresh is_test=0 sessions.
    os.environ['REGIN_TRACE_TEST'] = '1'
    try:
        for tid in sorted(_FIXTURE_TRACE_IDS):
            try:
                hook_plugin.post_span(
                    trace_id=tid,
                    name='session.end',
                    attributes={'reason': 'test_teardown'},
                )
            except Exception:
                # Best-effort cleanup — never fail the suite over teardown.
                pass
    finally:
        os.environ.pop('REGIN_TRACE_TEST', None)
