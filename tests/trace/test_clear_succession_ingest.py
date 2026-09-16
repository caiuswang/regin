"""The span ingest is the other half of the `/clear` succession push.

A `/clear` writes two facts from two hook processes: the predecessor's
`ended_reason = 'clear'`, committed HERE by the `session.end` span, and the
heir's `bridge_panes` row, written by SessionStart. Whichever lands second is
the one that can resolve a successor, so this site announces on every clear it
records and stays silent when the heir has not claimed the pane yet.
"""

from __future__ import annotations

import pytest

from lib.notifications import hub


@pytest.fixture
def frames(monkeypatch):
    captured = []
    monkeypatch.setattr(hub, "subscriber_count", lambda: 1)
    monkeypatch.setattr(
        hub, "broadcast_event",
        lambda name, payload: captured.append((name, payload)))
    return captured


@pytest.fixture
def bridge_on(monkeypatch):
    from lib.settings import settings
    monkeypatch.setattr(settings.agent_bridge, "enabled", True)


def _seed_pane(conn, trace_id, at):
    conn.execute(
        "INSERT INTO bridge_panes (trace_id, pane_id, tmux_server_pid, "
        "pane_pid, tmux_socket, reachable, cwd, created_at, updated_at) "
        "VALUES (?, '%7', 111, 222, NULL, 1, '/work', ?, ?)",
        (trace_id, at, at))


def _end_span(trace_id, reason):
    return {
        'trace_id': trace_id,
        'span_id': f'end-{trace_id}',
        'parent_id': None,
        'name': 'session.end',
        'start_time': '2026-08-01T10:30:00',
        'end_time': None,
        'duration_ms': 0,
        'attributes': {'reason': reason},
        'status_code': 'OK',
    }


def _successor_frames(frames):
    return [p for name, p in frames if name == hub.SUCCESSOR_EVENT]


def test_ingesting_the_clear_announces_the_waiting_heir(flask_client, tmp_db,
                                                        frames, bridge_on):
    """Registration-first ordering: the heir already holds the pane and is
    only waiting for this session to be marked cleared."""
    from lib.orm.engine import get_connection
    conn = get_connection()
    try:
        _seed_pane(conn, 'T-old', '2026-08-01 10:00:00')
        _seed_pane(conn, 'T-new', '2026-08-01 10:30:01')
        conn.execute(
            "INSERT INTO sessions (trace_id, status, started_at, last_seen) "
            "VALUES ('T-new', 'active', '2026-08-01 10:30:01', "
            "'2026-08-01 11:00:00')")
        conn.commit()
    finally:
        conn.close()

    assert flask_client.post('/api/session-spans',
                       json=_end_span('T-old', 'clear')).status_code == 200
    assert _successor_frames(frames) == [
        {'trace_id': 'T-old', 'successor_trace_id': 'T-new'}]


def test_a_clear_with_no_heir_yet_announces_nothing(flask_client, tmp_db, frames,
                                                    bridge_on):
    """Ingest-first ordering — the SessionStart that will claim the pane has
    not run. The registration side raises the frame instead."""
    from lib.orm.engine import get_connection
    conn = get_connection()
    try:
        _seed_pane(conn, 'T-old', '2026-08-01 10:00:00')
        conn.commit()
    finally:
        conn.close()

    assert flask_client.post('/api/session-spans',
                       json=_end_span('T-old', 'clear')).status_code == 200
    assert _successor_frames(frames) == []


def test_an_ordinary_session_end_is_not_a_succession(flask_client, tmp_db, frames,
                                                     bridge_on):
    """`session.end` fires for every ending; only `/clear` opens a heir."""
    from lib.orm.engine import get_connection
    conn = get_connection()
    try:
        _seed_pane(conn, 'T-old', '2026-08-01 10:00:00')
        _seed_pane(conn, 'T-new', '2026-08-01 10:30:01')
        conn.execute(
            "INSERT INTO sessions (trace_id, status, started_at, last_seen) "
            "VALUES ('T-new', 'active', '2026-08-01 10:30:01', "
            "'2026-08-01 11:00:00')")
        conn.commit()
    finally:
        conn.close()

    assert flask_client.post('/api/session-spans',
                       json=_end_span('T-old', 'logout')).status_code == 200
    assert _successor_frames(frames) == []


def test_an_announce_failure_never_fails_the_ingest(flask_client, tmp_db,
                                                    frames, bridge_on,
                                                    monkeypatch):
    """A hook is blocked on this POST; the span must still be accepted."""
    from lib.agent_bridge import succession
    monkeypatch.setattr(
        succession, "announce",
        lambda *_a: (_ for _ in ()).throw(RuntimeError("fan-out exploded")))
    resp = flask_client.post('/api/session-spans', json=_end_span('T-x', 'clear'))
    assert resp.status_code == 200
    assert resp.get_json()['ok'] is True
