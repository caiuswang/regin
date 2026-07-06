"""Per-agent phase verdict + agent_id backfill (web/blueprints/trace/sessions.py).

`phase` (session rollup) and `agent_phase` ("main" + each subagent id) are a
SERVER verdict the /live card renders without re-deriving. These tests cover
the pure phase helpers, the DB-backed summary payload, and the agent_id
column backfill/ingest stamp.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta

import pytest

from web.blueprints.trace import sessions as S


# ── pure phase helpers ───────────────────────────────────────────

def _now():
    return datetime(2026, 4, 18, 13, 0, 0)


def test_main_working_when_activity_fresh():
    now = _now()
    act = {'last_ts': (now - timedelta(seconds=3)).isoformat()}
    assert S._main_phase(act, now, ended=False) == 'working'


def test_main_idle_when_quiet_past_working_window():
    now = _now()
    act = {'last_ts': (now - timedelta(seconds=60)).isoformat()}
    assert S._main_phase(act, now, ended=False) == 'idle'


def test_main_inactive_stale_past_threshold():
    now = _now()
    act = {'last_ts': (now - timedelta(seconds=1200)).isoformat()}
    assert S._main_phase(act, now, ended=False) == 'inactive-stale'


def test_main_ended_when_session_ended():
    now = _now()
    act = {'last_ts': (now - timedelta(seconds=3)).isoformat()}
    assert S._main_phase(act, now, ended=True) == 'ended'


def test_main_waiting_permission_beats_working_window():
    now = _now()
    ts = (now - timedelta(seconds=2)).isoformat()
    act = {'last_ts': ts, 'perm_ts': ts}
    assert S._main_phase(act, now, ended=False) == 'waiting-permission'


def test_stale_blocker_not_waiting_on_inactive_session():
    """A PENDING permission that is the newest span but on a long-silent
    session is demoted (merge.py) → the phase is inactive-stale, not
    waiting-permission."""
    now = _now()
    ts = (now - timedelta(seconds=1200)).isoformat()
    act = {'last_ts': ts, 'perm_ts': ts}
    assert S._main_phase(act, now, ended=False) == 'inactive-stale'


def test_rollup_waiting_permission_beats_working():
    assert S._phase_rollup(['working', 'idle', 'waiting-permission']) \
        == 'waiting-permission'


def test_rollup_running_subagent_surfaces_over_idle_main():
    # main idle + a running subagent → rollup 'working'.
    assert S._phase_rollup(['idle', 'working']) == 'working'


def test_rollup_ended_session_ignores_ghost_stale_subagent():
    """An ended session with a ghost unstopped (stale) subagent rolls up to
    'ended', not 'inactive-stale' — once the session itself ended nothing can
    be genuinely waiting, so the ghost must not mask the 'ended' verdict."""
    by_aid = {None: {'last_ts': None}}
    roster = [{'agent_id': 'a1', 'status': 'stale'}]
    phase, agent_phase = S._session_phase(by_aid, roster, ended=True)
    assert agent_phase['main'] == 'ended'
    assert agent_phase['a1'] == 'inactive-stale'
    assert phase == 'ended'


def test_phase_from_roster_mappings():
    assert S._phase_from_roster('finished', None) == 'ended'
    assert S._phase_from_roster('interrupted', None) == 'ended'
    assert S._phase_from_roster('stale', None) == 'inactive-stale'
    assert S._phase_from_roster('running', None) == 'working'
    assert S._phase_from_roster('waiting', 'permission') == 'waiting-permission'
    assert S._phase_from_roster('waiting', 'input') == 'waiting-input'


def test_waiting_kind_only_when_blocker_is_newest():
    now = _now()
    newest = now.isoformat()
    older = (now - timedelta(seconds=30)).isoformat()
    # ask is the newest span → input.
    assert S._waiting_kind({'last_ts': newest, 'ask_ts': newest}) == 'input'
    # a later resolved span (last_ts newer than the ask) → not waiting.
    assert S._waiting_kind({'last_ts': newest, 'ask_ts': older}) is None


# ── DB-backed: backfill + summary payload ────────────────────────

@pytest.fixture
def trace_db(tmp_path, monkeypatch):
    db_path = tmp_path / 'phase.db'
    import lib.orm.engine as db_module
    monkeypatch.setattr(db_module, 'DB_PATH', str(db_path))
    db_module.init_db()
    return db_path


def _seed_session(conn, trace_id, *, status='active', last_seen):
    conn.execute(
        "INSERT INTO sessions (trace_id, started_at, last_seen, status, is_test) "
        "VALUES (?, ?, ?, ?, 1)",
        (trace_id, '2026-01-01T00:00:00', last_seen, status))


def _seed_span(conn, trace_id, span_id, name, *, start, status='OK',
               attrs=None, agent_id_col=None, end=None):
    conn.execute(
        "INSERT INTO session_spans "
        "(trace_id, span_id, name, kind, start_time, end_time, status_code, "
        " attributes, agent_id) "
        "VALUES (?, ?, ?, 'internal', ?, ?, ?, ?, ?)",
        (trace_id, span_id, name, start, end, status,
         json.dumps(attrs or {}), agent_id_col))


def test_schema_has_agent_id_column_and_index(trace_db):
    conn = sqlite3.connect(str(trace_db))
    try:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(session_spans)")}
        assert 'agent_id' in cols
        idx = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index'")}
        assert 'idx_session_spans_trace_agent' in idx
    finally:
        conn.close()


def test_ingest_stamps_agent_id_column(trace_db):
    """A span whose attributes carry agent_id lands with the column set."""
    from lib.trace.trace_service.ingest import _insert_span_row
    conn = sqlite3.connect(str(trace_db))
    conn.row_factory = sqlite3.Row
    try:
        span = {'trace_id': 'tX', 'span_id': 's1', 'name': 'tool.Read',
                'start_time': '2026-01-01T00:00:00', 'status_code': 'OK'}
        _insert_span_row(conn, span, {'agent_id': 'agentA'})
        conn.commit()
        row = conn.execute(
            "SELECT agent_id FROM session_spans WHERE span_id='s1'").fetchone()
        assert row['agent_id'] == 'agentA'
    finally:
        conn.close()


def test_summary_payload_has_phase_keys(trace_db):
    now = datetime.now()
    conn = sqlite3.connect(str(trace_db))
    try:
        _seed_session(conn, 'tP', last_seen=(now - timedelta(seconds=2)).isoformat())
        _seed_span(conn, 'tP', 'p1', 'prompt', start='2026-01-01T00:00:00')
        _seed_span(conn, 'tP', 'r1', 'assistant_response',
                   start=(now - timedelta(seconds=2)).isoformat())
        conn.commit()
    finally:
        conn.close()
    summary = S._session_summary('tP')
    assert summary['agent_phase']['main'] == 'working'
    assert summary['phase'] == 'working'
    assert 'phase_config' not in summary   # dropped: client never read it


def test_summary_main_idle_while_subagent_streams(trace_db):
    """Main quiet past the working window while a subagent streams: main=idle,
    rollup surfaces the running agent."""
    now = datetime.now()
    conn = sqlite3.connect(str(trace_db))
    try:
        _seed_session(conn, 'tS', last_seen=now.isoformat())
        # main last activity 60s ago → idle.
        _seed_span(conn, 'tS', 'mresp', 'assistant_response',
                   start=(now - timedelta(seconds=60)).isoformat())
        # subagent start marker + fresh activity → roster 'running'.
        _seed_span(conn, 'tS', 'sa-start-a1', 'subagent.start',
                   start=(now - timedelta(seconds=50)).isoformat(),
                   attrs={'agent_id': 'a1'}, agent_id_col='a1')
        _seed_span(conn, 'tS', 'sa-tool', 'tool.Read',
                   start=(now - timedelta(seconds=2)).isoformat(),
                   attrs={'agent_id': 'a1'}, agent_id_col='a1')
        conn.commit()
    finally:
        conn.close()
    summary = S._session_summary('tS')
    assert summary['agent_phase']['main'] == 'idle'
    assert summary['agent_phase']['a1'] == 'working'
    assert summary['phase'] == 'working'


# ── Roster: implicit stop from a resolved launch (lost subagent.stop) ─

def _roster_by_id(trace_id):
    roster, _activity, _ended = S._roster_with_activity(trace_id)
    return {e['agent_id']: e for e in roster}


def test_roster_resolved_launch_demotes_running_to_finished(trace_db):
    """Start marker + NO subagent.stop + its own tool.Agent launch already
    resolved (OK, end_time set) → 'finished', duration from the launch's
    end_time. This is the lost-subagent.stop repro (a93463845805c1d29)."""
    now = datetime.now()
    conn = sqlite3.connect(str(trace_db))
    try:
        _seed_session(conn, 'tR1', last_seen=now.isoformat())
        launch_start = (now - timedelta(seconds=120)).isoformat()
        launch_end = (now - timedelta(seconds=90)).isoformat()
        start_ts = (now - timedelta(seconds=115)).isoformat()
        _seed_span(conn, 'tR1', 'launch1', 'tool.Agent',
                   start=launch_start, end=launch_end, status='OK',
                   attrs={'subagent_type': 'general-purpose'})
        _seed_span(conn, 'tR1', 'sa-start-a1', 'subagent.start',
                   start=start_ts,
                   attrs={'agent_id': 'a1', 'agent_type': 'general-purpose'},
                   agent_id_col='a1')
        conn.commit()
    finally:
        conn.close()
    entry = _roster_by_id('tR1')['a1']
    assert entry['status'] == 'finished'
    expected_ms = int((now - timedelta(seconds=90) -
                       (now - timedelta(seconds=115))).total_seconds() * 1000)
    assert entry['duration_ms'] == expected_ms


def test_roster_pending_launch_stays_running(trace_db):
    """Start marker + NO subagent.stop + its own tool.Agent launch still
    PENDING (subagent genuinely still running) → status stays 'running', no
    false demotion."""
    now = datetime.now()
    conn = sqlite3.connect(str(trace_db))
    try:
        _seed_session(conn, 'tR2', last_seen=now.isoformat())
        launch_start = (now - timedelta(seconds=30)).isoformat()
        start_ts = (now - timedelta(seconds=25)).isoformat()
        _seed_span(conn, 'tR2', 'launch2', 'tool.Agent',
                   start=launch_start, status='PENDING',
                   attrs={'subagent_type': 'general-purpose'})
        _seed_span(conn, 'tR2', 'sa-start-a2', 'subagent.start',
                   start=start_ts,
                   attrs={'agent_id': 'a2', 'agent_type': 'general-purpose'},
                   agent_id_col='a2')
        _seed_span(conn, 'tR2', 'sa-tool2', 'tool.Read',
                   start=(now - timedelta(seconds=2)).isoformat(),
                   attrs={'agent_id': 'a2'}, agent_id_col='a2')
        conn.commit()
    finally:
        conn.close()
    entry = _roster_by_id('tR2')['a2']
    assert entry['status'] == 'running'
    assert entry['duration_ms'] is None


def test_roster_real_stop_marker_unchanged_by_resolved_launch(trace_db):
    """An agent with a REAL subagent.stop is unchanged — same 'finished'
    status and its duration is still derived from the real stop marker, not
    from a resolved launch's end_time, even when one is present."""
    now = datetime.now()
    conn = sqlite3.connect(str(trace_db))
    try:
        _seed_session(conn, 'tR3', last_seen=now.isoformat())
        launch_start = (now - timedelta(seconds=200)).isoformat()
        launch_end = (now - timedelta(seconds=1)).isoformat()   # far off
        start_ts = (now - timedelta(seconds=100)).isoformat()
        stop_ts = (now - timedelta(seconds=40)).isoformat()
        _seed_span(conn, 'tR3', 'launch3', 'tool.Agent',
                   start=launch_start, end=launch_end, status='OK',
                   attrs={'subagent_type': 'general-purpose'})
        _seed_span(conn, 'tR3', 'sa-start-a3', 'subagent.start',
                   start=start_ts,
                   attrs={'agent_id': 'a3', 'agent_type': 'general-purpose'},
                   agent_id_col='a3')
        _seed_span(conn, 'tR3', 'sa-stop-a3', 'subagent.stop',
                   start=stop_ts,
                   attrs={'agent_id': 'a3', 'agent_type': 'general-purpose'},
                   agent_id_col='a3')
        conn.commit()
    finally:
        conn.close()
    entry = _roster_by_id('tR3')['a3']
    assert entry['status'] == 'finished'
    expected_ms = int((now - timedelta(seconds=40) -
                       (now - timedelta(seconds=100))).total_seconds() * 1000)
    assert entry['duration_ms'] == expected_ms


def test_roster_parallel_same_type_pending_sibling_no_misattribution(trace_db):
    """Two concurrent same-type launches — launch1 RESOLVED (its agent
    finished), launch2 still PENDING (its agent running) — with the
    subagent.start markers arriving in INVERTED order vs the launch ids
    (the running agent boots NEAREST to launch1's timestamp). Proximity
    alone would bind the sole resolved launch1 to the running agent and
    demote it; the refuse-under-ambiguity rule must leave BOTH running (no
    resolved launch is applied while a same-type sibling is still PENDING),
    so the genuinely-running agent is never shown finished."""
    now = datetime.now()
    conn = sqlite3.connect(str(trace_db))
    try:
        _seed_session(conn, 'tR4', last_seen=now.isoformat())
        _seed_span(conn, 'tR4', 'launch1', 'tool.Agent',
                   start=(now - timedelta(seconds=120)).isoformat(),
                   end=(now - timedelta(seconds=30)).isoformat(), status='OK',
                   attrs={'subagent_type': 'general-purpose'})
        _seed_span(conn, 'tR4', 'launch2', 'tool.Agent',
                   start=(now - timedelta(seconds=119.9)).isoformat(),
                   status='PENDING',
                   attrs={'subagent_type': 'general-purpose'})
        # Running agent (a2) boots FIRST — nearest to launch1's ts.
        _seed_span(conn, 'tR4', 'sa-start-a2', 'subagent.start',
                   start=(now - timedelta(seconds=118)).isoformat(),
                   attrs={'agent_id': 'a2', 'agent_type': 'general-purpose'},
                   agent_id_col='a2')
        _seed_span(conn, 'tR4', 'sa-start-a1', 'subagent.start',
                   start=(now - timedelta(seconds=116)).isoformat(),
                   attrs={'agent_id': 'a1', 'agent_type': 'general-purpose'},
                   agent_id_col='a1')
        _seed_span(conn, 'tR4', 'sa-tool-a2', 'tool.Read',
                   start=(now - timedelta(seconds=1)).isoformat(),
                   attrs={'agent_id': 'a2'}, agent_id_col='a2')
        conn.commit()
    finally:
        conn.close()
    r = _roster_by_id('tR4')
    # The genuinely-running agent is never demoted.
    assert r['a2']['status'] == 'running'
    assert r['a2']['duration_ms'] is None
    # And under ambiguity the finished sibling is NOT force-demoted either —
    # it stays running (until a real stop or stale), the safe trade-off.
    assert r['a1']['status'] == 'running'


def test_roster_all_resolved_same_type_both_finished(trace_db):
    """Two concurrent same-type launches BOTH resolved (no PENDING sibling) →
    the mapping is unambiguous, so both open agents are demoted to 'finished',
    each with a duration from a resolved launch's end_time."""
    now = datetime.now()
    conn = sqlite3.connect(str(trace_db))
    try:
        _seed_session(conn, 'tR5', last_seen=now.isoformat())
        _seed_span(conn, 'tR5', 'launch1', 'tool.Agent',
                   start=(now - timedelta(seconds=120)).isoformat(),
                   end=(now - timedelta(seconds=40)).isoformat(), status='OK',
                   attrs={'subagent_type': 'general-purpose'})
        _seed_span(conn, 'tR5', 'launch2', 'tool.Agent',
                   start=(now - timedelta(seconds=119)).isoformat(),
                   end=(now - timedelta(seconds=30)).isoformat(), status='OK',
                   attrs={'subagent_type': 'general-purpose'})
        _seed_span(conn, 'tR5', 'sa-start-a1', 'subagent.start',
                   start=(now - timedelta(seconds=118)).isoformat(),
                   attrs={'agent_id': 'a1', 'agent_type': 'general-purpose'},
                   agent_id_col='a1')
        _seed_span(conn, 'tR5', 'sa-start-a2', 'subagent.start',
                   start=(now - timedelta(seconds=116)).isoformat(),
                   attrs={'agent_id': 'a2', 'agent_type': 'general-purpose'},
                   agent_id_col='a2')
        conn.commit()
    finally:
        conn.close()
    r = _roster_by_id('tR5')
    assert r['a1']['status'] == 'finished'
    assert r['a2']['status'] == 'finished'
    assert r['a1']['duration_ms'] is not None
    assert r['a2']['duration_ms'] is not None


def test_roster_denied_launch_not_treated_as_resolved(trace_db):
    """A denied `tool.Agent` launch recorded status OK with an end_time must
    NOT count as a clean completion for implicit-stop — its type is ambiguous
    (unresolved), so a same-type open agent is left running, not demoted."""
    now = datetime.now()
    conn = sqlite3.connect(str(trace_db))
    try:
        _seed_session(conn, 'tR6', last_seen=now.isoformat())
        _seed_span(conn, 'tR6', 'launch1', 'tool.Agent',
                   start=(now - timedelta(seconds=120)).isoformat(),
                   end=(now - timedelta(seconds=30)).isoformat(), status='OK',
                   attrs={'subagent_type': 'general-purpose', 'denied': True})
        _seed_span(conn, 'tR6', 'sa-start-a1', 'subagent.start',
                   start=(now - timedelta(seconds=118)).isoformat(),
                   attrs={'agent_id': 'a1', 'agent_type': 'general-purpose'},
                   agent_id_col='a1')
        _seed_span(conn, 'tR6', 'sa-tool-a1', 'tool.Read',
                   start=(now - timedelta(seconds=1)).isoformat(),
                   attrs={'agent_id': 'a1'}, agent_id_col='a1')
        conn.commit()
    finally:
        conn.close()
    assert _roster_by_id('tR6')['a1']['status'] == 'running'
