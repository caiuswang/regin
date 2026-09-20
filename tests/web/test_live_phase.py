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


def test_blocker_stays_waiting_past_the_stale_window():
    """A PENDING permission that is the newest span keeps 'waiting-permission'
    however long it waits: the agent cannot emit until a human answers, so the
    silence measures the wait, not the session's health. Ageing it out is what
    made a blocking session read 'quiet' in the switcher and the rail."""
    now = _now()
    ts = (now - timedelta(seconds=1200)).isoformat()
    assert S._main_phase({'last_ts': ts, 'perm_ts': ts}, now,
                         ended=False) == 'waiting-permission'
    assert S._main_phase({'last_ts': ts, 'ask_ts': ts}, now,
                         ended=False) == 'waiting-input'


def test_ended_session_demotes_even_a_newest_blocker():
    """`ended` is the only thing that clears a blocker: once the session is
    over nothing can still be waiting on the human."""
    now = _now()
    ts = (now - timedelta(seconds=1200)).isoformat()
    assert S._main_phase({'last_ts': ts, 'perm_ts': ts}, now,
                         ended=True) == 'ended'


def test_silence_without_a_blocker_still_stales():
    """The age gate is intact for the un-blocked case — only a blocker is
    exempt from it."""
    now = _now()
    ts = (now - timedelta(seconds=1200)).isoformat()
    older = (now - timedelta(seconds=1300)).isoformat()
    assert S._main_phase({'last_ts': ts}, now, ended=False) == 'inactive-stale'
    # a resolved span AFTER the permission means the gate was answered
    assert S._main_phase({'last_ts': ts, 'perm_ts': older}, now,
                         ended=False) == 'inactive-stale'


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


def test_a_retrying_runner_outranks_a_subagent_still_working():
    """A subagent's roster row says `running` right through an outage — it is
    the parent's turn that is stalled, and the roster has no way to know. The
    rollup must not let that vote paint the session green."""
    roster = [{'agent_id': 'a1', 'status': 'running'}]
    by_aid = {None: {'last_ts': datetime.now().isoformat()}}

    phase, agent_phase = S._session_phase(by_aid, roster, ended=False,
                                          main_override='retrying')

    assert agent_phase['main'] == 'retrying'
    assert agent_phase['a1'] == 'working'
    assert phase == 'retrying'


def test_a_parked_subagent_still_outranks_a_retry():
    """A retry clears itself; a question does not. The one a human can act on
    stays the one the rollup surfaces."""
    roster = [{'agent_id': 'a1', 'status': 'waiting'}]
    by_aid = {None: {'last_ts': datetime.now().isoformat()}}

    phase, _ = S._session_phase(by_aid, roster, ended=False,
                                main_override='retrying')

    # `waiting-input` rather than `-permission` only because this roster row
    # carries no activity to read a kind off; either one outranks the retry,
    # which is what this pins.
    assert phase == 'waiting-input'


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


def test_roster_untyped_pending_launch_blocks_demotion(trace_db):
    """A running agent whose own launch names no `subagent_type` — Kimi omits
    it whenever the Agent call didn't pass one — must not be demoted by an
    EARLIER launch of a known type that has since resolved. The untyped
    PENDING launch is exactly the running agent; typing the ambiguity check on
    `subagent_type` alone made it invisible, so the resolved launch's implicit
    stop landed on the live agent."""
    now = datetime.now()
    conn = sqlite3.connect(str(trace_db))
    try:
        _seed_session(conn, 'tR5', last_seen=now.isoformat())
        _seed_span(conn, 'tR5', 'launch1', 'tool.Agent',
                   start=(now - timedelta(seconds=300)).isoformat(),
                   end=(now - timedelta(seconds=200)).isoformat(), status='OK',
                   attrs={'subagent_type': 'coder'})
        _seed_span(conn, 'tR5', 'launch2', 'tool.Agent',
                   start=(now - timedelta(seconds=100)).isoformat(),
                   status='PENDING', attrs={'description': 'no type given'})
        _seed_span(conn, 'tR5', 'sa-start-b1', 'subagent.start',
                   start=(now - timedelta(seconds=99)).isoformat(),
                   attrs={'agent_id': 'b1', 'agent_type': 'coder'},
                   agent_id_col='b1')
        _seed_span(conn, 'tR5', 'sa-tool-b1', 'tool.Read',
                   start=(now - timedelta(seconds=2)).isoformat(),
                   attrs={'agent_id': 'b1'}, agent_id_col='b1')
        conn.commit()
    finally:
        conn.close()
    entry = _roster_by_id('tR5')['b1']
    assert entry['status'] == 'running'
    assert entry['duration_ms'] is None


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


def test_roster_workflow_state_done_marker_is_finished(trace_db):
    """A workflow-run trace records completion as `state: done` on the start
    marker itself (no subagent.stop is ever ingested) — the roster must read
    that as finished, not stale, even on an ended session."""
    conn = sqlite3.connect(str(trace_db))
    try:
        _seed_session(conn, 'tWF1', status='ended',
                      last_seen='2026-01-01T01:00:00')
        _seed_span(conn, 'tWF1', 'wfagent-a9', 'subagent.start',
                   start='2026-01-01T00:10:00',
                   attrs={'agent_id': 'a9', 'label': 'survey:tags',
                          'agent_type': 'workflow-subagent', 'state': 'done'},
                   agent_id_col='a9')
        conn.commit()
    finally:
        conn.close()
    entry = _roster_by_id('tWF1')['a9']
    assert entry['status'] == 'finished'
    assert entry['description'] == 'survey:tags'


# ── terminal-screen override ─────────────────────────────────────
# The span heuristic starves during any hook-silent stretch (extended
# thinking, one long tool call, a streamed response): activity ages past
# WORKING_WINDOW_SEC and the session reads `idle` while the real terminal
# plainly shows it working. `screen` is that terminal, parsed.

from lib.agent_bridge.screen_state import ScreenState  # noqa: E402


# `_session_phase` reads the real clock (unlike `_main_phase`, which takes
# `now`), so these ages are anchored to it rather than to the fixed `_now()`.
def _quiet_activity(seconds: int, **extra):
    base = datetime.now()
    act = {'last_ts': (base - timedelta(seconds=seconds)).isoformat()}
    act.update({k: (base - timedelta(seconds=v)).isoformat()
                for k, v in extra.items()})
    return {None: act}


def test_screen_promotes_a_hook_silent_session_back_to_working():
    by_aid = _quiet_activity(120)
    assert S._main_phase(by_aid[None], datetime.now(), ended=False) == 'idle'
    phase, agent_phase = S._session_phase(
        by_aid, [], ended=False, screen=ScreenState(busy=True, verb='Thinking'))
    assert phase == 'working'
    assert agent_phase['main'] == 'working'


def test_a_quiet_screen_never_contradicts_a_span_that_says_working():
    """`busy=False` also covers "status line not recognised", so it must not
    outrank a span-derived working — that would reinstate the false-idle bug
    with more authority than the heuristic it overrode."""
    by_aid = _quiet_activity(3)
    assert S._main_phase(by_aid[None], datetime.now(), ended=False) == 'working'
    phase, _ = S._session_phase(by_aid, [], ended=False,
                                screen=ScreenState(busy=False))
    assert phase == 'working'


def test_a_quiet_screen_clears_the_likely_crashed_verdict():
    """The one demotion a quiet screen earns: a readable claude pane proves
    the session is alive, which is all `inactive-stale` was claiming."""
    by_aid = _quiet_activity(1200)
    assert S._main_phase(by_aid[None], datetime.now(),
                         ended=False) == 'inactive-stale'
    phase, _ = S._session_phase(by_aid, [], ended=False,
                                screen=ScreenState(busy=False))
    assert phase == 'idle'


def test_no_screen_verdict_leaves_the_span_phase_untouched():
    by_aid = _quiet_activity(120)
    phase, _ = S._session_phase(by_aid, [], ended=False, screen=None)
    assert phase == 'idle'


def test_screen_never_resurrects_an_ended_session():
    """A settled pane can still show the last frame of a finished turn."""
    by_aid = _quiet_activity(3)
    phase, agent_phase = S._session_phase(
        by_aid, [], ended=True, screen=ScreenState(busy=True, verb='Thinking'))
    assert phase == 'ended'
    assert agent_phase['main'] == 'ended'


def test_screen_never_overrides_a_blocked_verdict():
    """waiting-* comes from a structured hook payload; a spinner glyph read
    off the terminal must not outrank it."""
    by_aid = _quiet_activity(5, perm_ts=2)
    phase, _ = S._session_phase(by_aid, [], ended=False,
                                screen=ScreenState(busy=False))
    assert phase == 'waiting-permission'


def test_run_owner_verdict_still_outranks_the_screen():
    by_aid = _quiet_activity(120)
    phase, _ = S._session_phase(by_aid, [], ended=False,
                                main_override='starting',
                                screen=ScreenState(busy=True))
    assert phase == 'starting'


# ── the SDK tier ─────────────────────────────────────────────────
# A run regin launched is spawned with the bridge off, so it never registers
# a pane and no screen exists to read: its NOW zone said "status unavailable"
# and a long tool call decayed to `inactive-stale`. The runner in this process
# is the source instead, and which source answers is decided once, from the
# `agent_runs` row written at launch.

from lib import agent_sdk as _agent_sdk  # noqa: E402
from lib.agent_sdk import registry as _registry, store as _sdk_store  # noqa: E402
from lib.settings import settings  # noqa: E402


class _Loop:
    def is_running(self):
        return True

    def is_closed(self):
        return False


class _OwnedRunner:
    """Just the surface the live-state read touches."""

    def __init__(self, state):
        self._state = state
        self.loop = _Loop()

    def live_state(self):
        return self._state


@pytest.fixture
def owned_run(monkeypatch):
    """Register a run this process owns, and refuse every tmux probe.

    The refusal is the point of the tier split: an SDK child provably has no
    pane, so a probe for one can only spend a subprocess to learn nothing.
    """
    from lib.agent_bridge import delivery

    def _no_probe(*args, **kwargs):
        raise AssertionError("an SDK session must never probe tmux")

    monkeypatch.setattr(delivery, "live_state", _no_probe)
    monkeypatch.setattr(delivery, "capture_screen", _no_probe)
    monkeypatch.setattr(_sdk_store, "find_run", lambda sid: None)
    registered: list[str] = []

    def _own(trace_id, state):
        registered.append(trace_id)
        _registry.register_run(trace_id, _OwnedRunner(state))

    yield _own
    for trace_id in registered:
        _registry.unregister_run(trace_id)


def _sdk_session(trace_db, trace_id, *, quiet_sec):
    """A session whose last span is `quiet_sec` old — the hook-silent stretch
    the span heuristic misreads."""
    now = datetime.now()
    conn = sqlite3.connect(str(trace_db))
    try:
        _seed_session(conn, trace_id, last_seen=now.isoformat())
        _seed_span(conn, trace_id, 'r1', 'assistant_response',
                   start=(now - timedelta(seconds=quiet_sec)).isoformat())
        conn.commit()
    finally:
        conn.close()


def test_a_long_tool_call_with_no_spans_still_reads_working(trace_db,
                                                            owned_run):
    """The exact case that failed: no span for twenty minutes while one tool
    runs, which the recency rule can only read as a crashed session."""
    _sdk_session(trace_db, 'sdkA', quiet_sec=1200)
    owned_run('sdkA', ScreenState(busy=True, verb='Working', elapsed_sec=1200,
                                  tool='Bash'))

    summary = S._session_summary('sdkA')

    assert summary['live_screen']['busy'] is True
    assert summary['live_screen']['tool'] == 'Bash'
    assert summary['phase'] == 'working'
    assert summary['agent_phase']['main'] == 'working'


def test_between_turns_the_run_reports_quiet_rather_than_unavailable(trace_db,
                                                                     owned_run):
    """`busy=False` is a verdict the runner can give and a screen cannot, so
    the card reads idle instead of "inactive"."""
    _sdk_session(trace_db, 'sdkB', quiet_sec=1200)
    owned_run('sdkB', ScreenState(busy=False))

    summary = S._session_summary('sdkB')

    assert summary['live_screen']['busy'] is False
    assert summary['phase'] == 'idle'


def test_a_run_this_process_does_not_own_has_no_status_and_no_probe(
        trace_db, monkeypatch):
    """Tier without a runner: null, never a fabricated `busy: false` — and
    still no tmux, because the child never had a pane to read."""
    from lib.agent_bridge import delivery

    def _no_probe(*args, **kwargs):
        raise AssertionError("an SDK session must never probe tmux")

    monkeypatch.setattr(delivery, "live_state", _no_probe)
    monkeypatch.setattr(_sdk_store, "find_run",
                        lambda sid: {"trace_id": 'sdkC', "status": "running"})
    _sdk_session(trace_db, 'sdkC', quiet_sec=5)

    assert S._session_summary('sdkC')['live_screen'] is None


def test_sdk_status_is_not_gated_on_the_bridge_being_enabled(trace_db,
                                                             owned_run,
                                                             monkeypatch):
    """A run's status comes from its runner, not from the bridge, so an
    operator who never turned the bridge on still gets it."""
    monkeypatch.setattr(settings.agent_bridge, "enabled", False)
    _sdk_session(trace_db, 'sdkD', quiet_sec=2)
    owned_run('sdkD', ScreenState(busy=True, verb='Working'))

    assert S._session_summary('sdkD')['live_screen']['busy'] is True


def test_a_terminal_session_is_still_read_off_its_screen(trace_db,
                                                         monkeypatch):
    """The other tier, unchanged: no run row, so the screen answers."""
    asked: list = []

    def _fake(trace_id, accept=None):
        asked.append((trace_id, accept))
        return ScreenState(busy=True, verb='Thinking')

    from lib.agent_bridge import delivery
    monkeypatch.setattr(delivery, "live_state", _fake)
    monkeypatch.setattr(_sdk_store, "find_run", lambda sid: None)
    _sdk_session(trace_db, 'tmuxA', quiet_sec=1200)

    summary = S._session_summary('tmuxA')

    assert asked == [('tmuxA', {'tmuxA'})]
    assert summary['live_screen']['verb'] == 'Thinking'
    assert summary['phase'] == 'working'


# ── the picker's sweep ───────────────────────────────────────────

def test_screen_states_lists_the_runs_child_session_id(flask_client,
                                                       monkeypatch):
    """The dot lights on the row the list actually shows — the child's id, the
    same key the tmux sweep uses."""
    from lib.agent_bridge import delivery
    from lib.orm.engine import get_connection

    conn = get_connection()
    try:
        _seed_session(conn, 'child-1', last_seen=datetime.now().isoformat())
        conn.commit()
    finally:
        conn.close()
    monkeypatch.setattr(settings.agent_bridge, "enabled", False)
    monkeypatch.setattr(delivery, "screen_states", dict)
    monkeypatch.setattr(_agent_sdk, "run_live_states",
                        lambda: {'child-1': ScreenState(busy=True,
                                                        verb='Working',
                                                        elapsed_sec=12,
                                                        tool='Bash')})

    body = flask_client.get('/api/bridge/screen-states').get_json()

    assert body['states'] == {
        'child-1': {'busy': True, 'verb': 'Working', 'elapsed_sec': 12}}


def test_a_failing_tmux_sweep_still_reports_the_runs_regin_owns(flask_client,
                                                                monkeypatch):
    from lib.agent_bridge import delivery
    from lib.orm.engine import get_connection

    conn = get_connection()
    try:
        _seed_session(conn, 'child-2', last_seen=datetime.now().isoformat())
        conn.commit()
    finally:
        conn.close()

    def _boom():
        raise RuntimeError("tmux is gone")

    monkeypatch.setattr(delivery, "screen_states", _boom)
    monkeypatch.setattr(_agent_sdk, "run_live_states",
                        lambda: {'child-2': ScreenState(busy=False)})

    body = flask_client.get('/api/bridge/screen-states').get_json()

    assert body['states']['child-2']['busy'] is False


def test_a_failing_run_sweep_never_500s_the_picker(flask_client, monkeypatch):
    from lib.agent_bridge import delivery

    def _boom():
        raise RuntimeError("registry is wedged")

    monkeypatch.setattr(delivery, "screen_states", dict)
    monkeypatch.setattr(_agent_sdk, "run_live_states", _boom)

    resp = flask_client.get('/api/bridge/screen-states')

    assert resp.status_code == 200
    assert resp.get_json() == {"states": {}}


def test_a_ghost_subagent_never_outranks_an_idle_runner():
    """A run regin OWNS rolls up to its runner's verdict, not the roster's.

    Subagents execute inside a turn, so a runner reporting `idle` means none
    is running and a roster still claiming one is a lost `subagent.stop`.
    Before this, that ghost rolled the session up to `working` and the
    switcher put a parked session above the ones actually moving.
    """
    by_aid = _quiet_activity(1200)
    roster = [{'agent_id': 'ag-1', 'status': 'running'}]
    phase, agent_phase = S._session_phase(
        by_aid, roster, ended=False, main_override='idle')
    assert phase == 'idle'
    # The roster is still the only source for the subagent's OWN entry.
    assert agent_phase['ag-1'] == 'working'
    assert agent_phase['main'] == 'idle'


def test_a_ghost_subagent_still_rolls_up_without_a_runner():
    """The demotion is scoped to runs regin owns. A session it merely traces
    has no better source than the roster, so the rollup is unchanged."""
    by_aid = _quiet_activity(1200)
    roster = [{'agent_id': 'ag-1', 'status': 'running'}]
    phase, _ = S._session_phase(by_aid, roster, ended=False)
    assert phase == 'working'



def test_a_parked_subagent_still_outranks_an_idle_runner():
    """The ghost demotion drops only `working` votes. A park outlives the turn
    that opened it, and surfacing it is the rollup's whole job."""
    by_aid = _quiet_activity(1200)
    roster = [{'agent_id': 'ag-1', 'status': 'waiting'}]
    phase, agent_phase = S._session_phase(
        by_aid, roster, ended=False, main_override='idle')
    assert phase == 'waiting-input'
    assert agent_phase['main'] == 'idle'
