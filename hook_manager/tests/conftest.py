"""pytest fixtures for hook_manager tests.

The sys.path wiring that previously lived here is no longer needed —
`pip install -e .` registers the project packages under the venv.
"""

import pytest


@pytest.fixture(autouse=True)
def _isolate_turn_trace_state(tmp_path, monkeypatch):
    """Point the turn_trace seen-uuid cache at a per-test tmp dir so
    state from earlier tests can't suppress span emission in later
    tests that reuse the same session_id."""
    monkeypatch.setenv('REGIN_TURN_TRACE_STATE_DIR', str(tmp_path / 'turn_trace_state'))


@pytest.fixture(autouse=True)
def _mark_test_spans(monkeypatch):
    """Stamp REGIN_TRACE_TEST=1 so any span a test builds carries
    `is_test=True`.

    Tests under tests/fixtures/ share a hardcoded session_id; without this
    stamp, every replay leaked into the user-visible sessions list under
    that id (see trace_id `45ffacd9-d020-48fc-82ce-1b020ac54716`).

    The env var propagates to subprocesses too (test_replay spawns
    `python -m hook_manager`), so subprocess-replayed handlers stamp their
    spans as well.

    This fixture no longer records trace_ids for an end-of-suite cleanup
    pass. That pass existed to close live `sessions` rows the suite had
    created by letting ingest POSTs through — the root `conftest.py` now
    severs the transport, so no such rows are created and there is nothing
    to close.
    """
    monkeypatch.setenv('REGIN_TRACE_TEST', '1')
