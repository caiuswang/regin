"""`/clear` gives a run's SDK stream a new trace; each child reads only its own.

A regin-launched run is traced twice — the runner's SDK stream and the child
`claude`'s hooks — and `trace_group` unions the pair. `/clear` retires the child
and opens another under the same runner, so one run drives several
conversations. While every one of them landed on the run's single `sdk-<hex>`
trace, and that trace unions with `agent_runs.cli_session_id` (pinned to the
FIRST child forever), the first conversation absorbed every later one and each
heir lost its live stream entirely.
"""

from __future__ import annotations

import sqlite3

from lib.trace.alias import sdk_span_trace_id, trace_group

RUN = 'sdk-deadbeef'
FIRST = '11111111-2222-3333-4444-555555555555'
HEIR = '99999999-8888-7777-6666-555555555555'
SEGMENT = f'{RUN}:{HEIR}'


def _db(*children: str) -> sqlite3.Connection:
    conn = sqlite3.connect(':memory:')
    conn.row_factory = sqlite3.Row
    conn.execute("""CREATE TABLE agent_runs (
        trace_id TEXT, cli_session_id TEXT, status TEXT, cwd TEXT)""")
    conn.execute("""CREATE TABLE agent_run_sessions (
        trace_id TEXT, cli_session_id TEXT UNIQUE)""")
    if children:
        conn.execute("INSERT INTO agent_runs VALUES (?,?,'running','/w')",
                     (RUN, children[0]))
    for child in children:
        conn.execute("INSERT INTO agent_run_sessions VALUES (?,?)",
                     (RUN, child))
    return conn


def test_the_heir_resolves_to_its_own_segment_not_to_nothing():
    """The reported bug's other half: before the segment existed the heir was
    not in any group, so the new conversation showed no live SDK stream."""
    assert trace_group(_db(FIRST, HEIR), HEIR) == [HEIR, SEGMENT]


def test_the_first_child_no_longer_absorbs_the_heirs_stream():
    """The reported bug: `/clear`, then prompt — and the prompt was captured by
    the session that was cleared."""
    group = trace_group(_db(FIRST, HEIR), FIRST)

    assert group == [FIRST, RUN]
    assert SEGMENT not in group


def test_the_runs_own_id_still_opens_the_first_conversation():
    """`/live` links carry the run's id; it stays a legitimate entry point."""
    assert trace_group(_db(FIRST, HEIR), RUN) == [FIRST, RUN]


def test_a_segment_id_is_a_legitimate_entry_point():
    assert trace_group(_db(FIRST, HEIR), SEGMENT) == [HEIR, SEGMENT]


def test_an_unbacked_segment_id_unions_nothing():
    """Segment ids arrive off a URL. One naming a child this run never drove
    must read as its own empty session, not pull a stranger's spans in."""
    conn = _db(FIRST, HEIR)

    assert trace_group(conn, f'{RUN}:{FIRST[::-1]}') == [f'{RUN}:{FIRST[::-1]}']


def test_a_run_that_never_cleared_is_grouped_exactly_as_before():
    """The 99% shape must not move."""
    conn = _db(FIRST)

    assert trace_group(conn, FIRST) == [FIRST, RUN]
    assert trace_group(conn, RUN) == [FIRST, RUN]


def test_a_plain_session_is_still_its_own_group():
    assert trace_group(_db(), 'plain-session') == ['plain-session']


def test_a_pre_alias_group_db_keeps_its_old_answer():
    """Booting the server does not migrate. A DB predating the alias-group
    table still resolves the run and its first child."""
    conn = sqlite3.connect(':memory:')
    conn.row_factory = sqlite3.Row
    conn.execute("""CREATE TABLE agent_runs (
        trace_id TEXT, cli_session_id TEXT)""")
    conn.execute("INSERT INTO agent_runs VALUES (?,?)", (RUN, FIRST))

    assert trace_group(conn, FIRST) == [FIRST, RUN]
    assert trace_group(conn, HEIR) == [HEIR]


def test_segment_id_names_both_halves():
    assert sdk_span_trace_id(RUN, HEIR) == SEGMENT


# ── the session list ─────────────────────────────────────────


def _sessions(*trace_ids: str) -> None:
    from lib.orm.engine import SessionLocal
    from lib.orm.models.trace import Session as SessionModel

    with SessionLocal() as session:
        for trace_id in trace_ids:
            session.add(SessionModel(trace_id=trace_id,
                                     started_at='2026-01-01T00:00:00',
                                     last_seen='2026-01-01T00:00:00'))
        session.commit()


def test_a_segment_is_hidden_from_the_session_list(tmp_db):
    """A segment is the non-canonical half of a merged session, exactly as the
    run's own id is. Listing it would show one conversation twice."""
    from lib.agent_sdk import store
    from lib.orm.engine import SessionLocal
    from lib.trace.alias import aliased_run_ids

    store.upsert_run(RUN, status='running')
    store.set_cli_session(RUN, FIRST)
    store.set_cli_session(RUN, HEIR)
    _sessions(FIRST, HEIR, RUN, SEGMENT)

    with SessionLocal() as session:
        hidden = aliased_run_ids(session)

    assert set(hidden) == {RUN, SEGMENT}


def test_a_segment_whose_child_wrote_no_trace_stays_visible(tmp_db):
    """Same rule the run's own id follows: hiding a row whose counterpart does
    not exist would drop the conversation out of the list entirely."""
    from lib.agent_sdk import store
    from lib.orm.engine import SessionLocal
    from lib.trace.alias import aliased_run_ids

    store.upsert_run(RUN, status='running')
    store.set_cli_session(RUN, FIRST)
    store.set_cli_session(RUN, HEIR)
    _sessions(FIRST, RUN, SEGMENT)

    with SessionLocal() as session:
        hidden = aliased_run_ids(session)

    assert set(hidden) == {RUN}
