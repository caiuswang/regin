"""The parked-decision feed behind the blocker banner (lib/agent_messages/blockers).

Since the derivation rewrite, presence comes from the parked state itself —
the SDK ask registry for sessions regin owns, PENDING decision spans for
hook-observed ones — and message rows only decorate (id, read state, title).
Each test pins one rule: a park is a card even with no row, a row is nothing
without a park, read state is irrelevant, liveness is recency, options come
off the span, and every retirement signal (resolved twin, same-named
completion, denial, newer main-loop prompt) takes exactly its own card down.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
from datetime import datetime, timedelta

import pytest

from lib.agent_messages import blockers, store
from lib.agent_messages.event_notify import PERM_KEY, PLAN_KEY

_TRACE = 'sess-parked'


@pytest.fixture(autouse=True)
def _reachable(monkeypatch):
    """Default the bridge to unreachable; the tests that care opt in."""
    monkeypatch.setattr(
        'web.blueprints.trace.sessions._bridge_reachability',
        lambda trace_id: {'bridge_reachable': False, 'bridge_pane': None,
                          'sdk_owned': False})


def _reach(monkeypatch, *, reachable=True, sdk=False):
    monkeypatch.setattr(
        'web.blueprints.trace.sessions._bridge_reachability',
        lambda trace_id: {'bridge_reachable': reachable, 'bridge_pane': 'p',
                          'sdk_owned': sdk})


def _stamp(minutes_ago):
    return (datetime.now() - timedelta(minutes=minutes_ago)).isoformat()


def _conn(tmp_db):
    return sqlite3.connect(str(tmp_db))


def _seed_session(tmp_db, trace_id=_TRACE, *, status='active', minutes_ago=1):
    conn = _conn(tmp_db)
    try:
        conn.execute(
            "INSERT INTO sessions (trace_id, started_at, last_seen, status) "
            "VALUES (?, ?, ?, ?)",
            (trace_id, _stamp(60), _stamp(minutes_ago), status))
        conn.commit()
    finally:
        conn.close()


def _seed_span(tmp_db, span_id, *, trace_id=_TRACE, name='permission.request',
               status='PENDING', attrs=None, start=None):
    conn = _conn(tmp_db)
    try:
        conn.execute(
            "INSERT INTO session_spans "
            "(trace_id, span_id, name, kind, start_time, status_code, attributes) "
            "VALUES (?, ?, ?, 'internal', ?, ?, ?)",
            (trace_id, span_id, name, start or _stamp(1), status,
             json.dumps(attrs or {})))
        conn.commit()
    finally:
        conn.close()


def _resolve_span(tmp_db, span_id, *, trace_id=_TRACE):
    """A resolving prompt UPDATES its row — session_spans is UNIQUE on
    (trace_id, span_id), so there is no second row to read past."""
    conn = _conn(tmp_db)
    try:
        conn.execute("UPDATE session_spans SET status_code = 'OK' "
                     " WHERE trace_id = ? AND span_id = ?", (trace_id, span_id))
        conn.commit()
    finally:
        conn.close()


def _seed_card(trace_id=_TRACE, *, key=PERM_KEY, span_id=None, body='pick one',
               minutes_ago=0, tmp_db=None):
    row = store.record_message(
        trace_id=trace_id, body=body, msg_type='blocker', msg_key=key,
        span_id=span_id, dispatch_webhook=False)
    if minutes_ago:
        _backdate(tmp_db, row['id'], minutes_ago=minutes_ago)
    return row


def _backdate(tmp_db, message_id, *, minutes_ago):
    """Move a card's whole raise history into the past — `updated_at` too,
    which `record_message` always stamps 'now' and the heal grace keys off."""
    conn = _conn(tmp_db)
    try:
        conn.execute(
            "UPDATE agent_messages SET created_at = ?, updated_at = ? "
            " WHERE id = ?", (_stamp(minutes_ago), _stamp(minutes_ago), message_id))
        conn.commit()
    finally:
        conn.close()


def _seed_sdk_link(tmp_db, trace_id, cli_session_id):
    conn = _conn(tmp_db)
    try:
        conn.execute(
            "INSERT INTO agent_runs (trace_id, status, cli_session_id) "
            "VALUES (?, 'running', ?)", (trace_id, cli_session_id))
        conn.commit()
    finally:
        conn.close()


_ASK_ATTRS = {
    'kind': 'question',
    'questions': [{
        'question': 'How far should Close/Delete go?',
        'header': 'Scope',
        'options': [
            {'label': 'Escape only, then warn', 'description': 'safest'},
            {'label': 'Escape, then type /exit'},
            {'label': 'Detect + report only'},
        ],
    }],
}

_TU_A = 'toolu_01AAAAAAAAAAAAAAAAAAAAAA'
_TU_B = 'toolu_01BBBBBBBBBBBBBBBBBBBBBB'


def _ask_attrs(*labels, tool_use_id=None):
    attrs = {'kind': 'question',
             'questions': [{'question': 'pick', 'options': [
                 {'label': lab} for lab in labels]}]}
    if tool_use_id:
        attrs['tool_use_id'] = tool_use_id
    return attrs


# ── The gate: what counts as waiting ─────────────────────────────

def test_a_park_with_its_card_is_returned(tmp_db):
    _seed_session(tmp_db)
    _seed_span(tmp_db, 'permreq-1', attrs=_ASK_ATTRS)
    _seed_card()
    rows = blockers.live_blockers()
    assert [r['trace_id'] for r in rows] == [_TRACE]


def test_a_park_with_no_card_row_still_surfaces(tmp_db):
    """Presence is the parked state, not the emit: a park whose notify never
    fired (events disabled, push down) still raises the banner. The card has
    no row id, so there is nothing to dismiss against — id is None."""
    _seed_session(tmp_db)
    _seed_span(tmp_db, 'permreq-1', attrs=_ASK_ATTRS)
    rows = blockers.live_blockers()
    assert len(rows) == 1
    assert rows[0]['id'] is None
    assert rows[0]['question'] == 'How far should Close/Delete go?'
    assert rows[0]['msg_key'] == PERM_KEY


def test_a_card_without_a_park_is_not_a_blocker(tmp_db):
    """The inverse of the rule above — and the exact shape of the dual-writer
    bug: a stray or duplicated row must not raise a banner nobody is parked
    behind."""
    _seed_session(tmp_db)
    _seed_card()
    assert blockers.live_blockers() == []


def test_a_read_card_is_still_waiting(tmp_db):
    """Reading is not answering. The banner outlives the acknowledgement."""
    _seed_session(tmp_db)
    _seed_span(tmp_db, 'permreq-1', attrs=_ASK_ATTRS)
    card = _seed_card()
    store.mark_read([card['id']])
    assert len(blockers.live_blockers()) == 1


def test_mark_all_read_leaves_the_card_waiting(tmp_db):
    _seed_session(tmp_db)
    _seed_span(tmp_db, 'permreq-1', attrs=_ASK_ATTRS)
    _seed_card()
    store.mark_all_read()
    assert len(blockers.live_blockers()) == 1


def test_a_dismissed_card_suppresses_its_own_park(tmp_db):
    _seed_session(tmp_db)
    _seed_span(tmp_db, 'permreq-1', attrs=_ask_attrs('X', tool_use_id=_TU_A))
    _seed_card(span_id=_TU_A)
    store.dismiss_keyed(_TRACE, PERM_KEY)
    assert blockers.live_blockers() == []


def test_an_old_dismissal_does_not_suppress_a_new_park(tmp_db):
    """"Never show again" is a promise about ONE decision. A new prompt under
    the same key must interrupt again."""
    _seed_session(tmp_db)
    old = _seed_card(span_id=_TU_A)
    store.dismiss_keyed(_TRACE, PERM_KEY)
    _backdate(tmp_db, old['id'], minutes_ago=8)
    _seed_span(tmp_db, 'permreq-2', attrs=_ask_attrs('Y', tool_use_id=_TU_B),
               start=_stamp(1))
    rows = blockers.live_blockers()
    assert len(rows) == 1
    assert rows[0]['span_id'] == _TU_B


def test_a_silent_session_drops_out_even_while_status_active(tmp_db):
    """`status` is exactly wrong for the sessions this matters for: an agent
    killed mid-prompt never writes its end event and stays 'active' forever."""
    _seed_session(tmp_db, status='active', minutes_ago=45)
    _seed_span(tmp_db, 'permreq-1', attrs=_ASK_ATTRS, start=_stamp(45))
    _seed_card(minutes_ago=45, tmp_db=tmp_db)
    assert blockers.live_blockers() == []


def test_a_non_decision_message_is_not_a_blocker(tmp_db):
    _seed_session(tmp_db)
    store.record_message(trace_id=_TRACE, body='fyi', msg_type='warning',
                         dispatch_webhook=False)
    assert blockers.live_blockers() == []


# ── One agent, one card ──────────────────────────────────────────

def test_the_newest_surviving_park_wins(tmp_db):
    """The terminal parks its main loop on one decision at a time; older
    PENDING placeholders in the same session are strays, not extra cards."""
    _seed_session(tmp_db)
    _seed_span(tmp_db, 'permreq-old', start=_stamp(9), attrs=_ask_attrs('Old'))
    _seed_span(tmp_db, 'permreq-new', start=_stamp(1),
               attrs=_ask_attrs('New-one', 'New-two'))
    rows = blockers.live_blockers()
    assert len(rows) == 1
    assert [o['label'] for o in rows[0]['options']] == ['New-one', 'New-two']


def test_an_sdk_child_session_is_not_derived_twice(tmp_db):
    """The regression that motivated the rewrite: an SDK run's CLI child has
    hook spans of its own, and deriving those next to the registry's ask
    showed one question as two decisions."""
    _seed_session(tmp_db, 'cli-child')
    _seed_sdk_link(tmp_db, 'sdk-run-1', 'cli-child')
    _seed_span(tmp_db, 'permreq-1', trace_id='cli-child', attrs=_ASK_ATTRS)
    assert blockers.live_blockers() == []


def test_a_supersede_stays_one_card(tmp_db):
    _seed_session(tmp_db)
    _seed_span(tmp_db, 'permreq-1', attrs={})
    _seed_card(body='first')
    _seed_card(body='second')
    rows = blockers.live_blockers()
    assert len(rows) == 1
    assert rows[0]['body'] == 'second'


def test_a_live_plan_card_is_a_second_decision(tmp_db):
    """A permission park and a plan awaiting review coexist in one session
    (approve the plan, and the first gated tool can park before anything
    completes). "Plan ready" has no span behind it — the row is its only
    record — so it derives from the row, and only for a live session."""
    _seed_session(tmp_db)
    _seed_span(tmp_db, 'permreq-1', attrs=_ASK_ATTRS)
    _seed_card(body='pick one')
    _seed_card(key=PLAN_KEY, body='the plan text')
    rows = blockers.live_blockers()
    assert sorted(r['msg_key'] for r in rows) == [PERM_KEY, PLAN_KEY]


def test_a_newer_prompt_retires_the_plan_card(tmp_db):
    _seed_session(tmp_db)
    row = _seed_card(key=PLAN_KEY, body='the plan text')
    _backdate(tmp_db, row['id'], minutes_ago=8)
    _seed_span(tmp_db, 'prompt-1', name='prompt', status='OK', start=_stamp(2))
    assert blockers.live_blockers() == []


# ── Span truth ───────────────────────────────────────────────────

def test_options_come_from_the_span_not_the_body(tmp_db):
    """The card's body is prose built for push channels. Re-parsing it for
    option labels invents data the bridge cannot select by index."""
    _seed_session(tmp_db)
    _seed_span(tmp_db, 'toolu_1', attrs=_ASK_ATTRS)
    _seed_card(span_id='toolu_1', body='• not-the-real-option')
    row = blockers.live_blockers()[0]
    assert [o['label'] for o in row['options']] == [
        'Escape only, then warn', 'Escape, then type /exit', 'Detect + report only']
    assert row['question'] == 'How far should Close/Delete go?'
    assert row['options'][0]['description'] == 'safest'


def test_option_index_is_the_spans_own_ordering(tmp_db):
    _seed_session(tmp_db)
    _seed_span(tmp_db, 'toolu_1', attrs=_ASK_ATTRS)
    _seed_card(span_id='toolu_1')
    row = blockers.live_blockers()[0]
    assert [o['index'] for o in row['options']] == [0, 1, 2]


def test_options_and_target_come_from_the_same_span(tmp_db, monkeypatch):
    """The old row-driven feed could bind a card to one span's options while
    the answer went to another call. Derived, the newest surviving park
    supplies both — a click can only answer the question it displayed."""
    from lib.trace.pending_spans import perm_pending_id
    _reach(monkeypatch)
    _seed_session(tmp_db)
    _seed_span(tmp_db, perm_pending_id(_TU_A), start=_stamp(9),
               attrs=_ask_attrs('A-one', 'A-two', tool_use_id=_TU_A))
    _seed_span(tmp_db, perm_pending_id(_TU_B), start=_stamp(1),
               attrs=_ask_attrs('B-one', 'B-two', tool_use_id=_TU_B))
    row = blockers.live_blockers()[0]
    assert [o['label'] for o in row['options']] == ['B-one', 'B-two']
    assert row['span_id'] == _TU_B


def test_a_park_without_a_tool_use_id_emits_no_span_id(tmp_db, monkeypatch):
    """`bridge-decide` forwards `span_id` as `tool_use_id` and the runner
    exact-matches it, so a placeholder id would guarantee 'no pending
    permission request' — None lets the runner resolve the oldest park."""
    _reach(monkeypatch, sdk=True)
    _seed_session(tmp_db)
    _seed_span(tmp_db, 'permreq-noid', attrs={'kind': 'plan'})
    row = blockers.live_blockers()[0]
    assert row['span_id'] is None
    assert row['answerable'] == 'decision'


# ── Retirement signals ───────────────────────────────────────────

def test_a_resolved_named_span_retires_its_card(tmp_db):
    _seed_session(tmp_db)
    _seed_span(tmp_db, 'toolu_1', attrs=_ASK_ATTRS)
    _seed_card(span_id='toolu_1')
    assert len(blockers.live_blockers()) == 1
    _resolve_span(tmp_db, 'toolu_1')
    assert blockers.live_blockers() == []


def test_a_resolved_tool_call_retires_its_card(tmp_db):
    """The resolved twin of a permission gate is the tool's OWN span, which no
    decision-span name matches — it has to be found by tool_use_id."""
    from lib.trace.pending_spans import perm_pending_id
    _seed_session(tmp_db)
    _seed_span(tmp_db, perm_pending_id(_TU_A),
               attrs=_ask_attrs('X', tool_use_id=_TU_A))
    assert len(blockers.live_blockers()) == 1
    _seed_span(tmp_db, 'real-span', name='tool.Bash', status='OK',
               attrs={'tool_use_id': _TU_A})
    assert blockers.live_blockers() == []


def test_a_completed_same_named_tool_retires_an_idless_park(tmp_db):
    """A random-id `permission.request` has no key for ingest to resolve it
    by, so the same-named tool completing after the park is the signal that
    the gate was approved."""
    _seed_session(tmp_db)
    _seed_span(tmp_db, 'randomid-1', start=_stamp(5),
               attrs={'tool_name': 'Bash'})
    assert len(blockers.live_blockers()) == 1
    _seed_span(tmp_db, 'bash-1', name='tool.Bash', status='OK',
               start=_stamp(1))
    assert blockers.live_blockers() == []


def test_a_denial_retires_an_idless_park(tmp_db):
    """A denial never runs the tool, so the completed-twin signal cannot
    fire — the `permission.denied` span is the record that it was decided."""
    _seed_session(tmp_db)
    _seed_span(tmp_db, 'randomid-1', start=_stamp(5),
               attrs={'tool_name': 'Bash'})
    _seed_span(tmp_db, 'denied-1', name='permission.denied', status='ERROR',
               start=_stamp(1), attrs={'tool_name': 'Bash'})
    assert blockers.live_blockers() == []


def test_a_same_named_completion_spares_a_park_that_names_its_call(tmp_db):
    """Name-matching is strictly the fallback for id-less parks. A park that
    carries a tool_use_id has an exact resolution signal, and a parallel
    subagent's same-named `tool.Bash` completing must not stand in for it —
    that false-retire permanently dismissed a live park via the heal."""
    _seed_session(tmp_db)
    _seed_span(tmp_db, 'permreq-1', start=_stamp(5),
               attrs={'tool_name': 'Bash', 'tool_use_id': _TU_A})
    _seed_span(tmp_db, 'bash-other', name='tool.Bash', status='OK',
               start=_stamp(1), attrs={'tool_use_id': _TU_B})
    assert len(blockers.live_blockers()) == 1


def test_a_subagent_completion_does_not_retire_an_idless_park(tmp_db):
    """Only the main loop is serialized around a park — a parallel subagent
    keeps running tools, and its completions prove nothing."""
    _seed_session(tmp_db)
    _seed_span(tmp_db, 'randomid-1', start=_stamp(5),
               attrs={'tool_name': 'Bash'})
    _seed_span(tmp_db, 'bash-sa', name='tool.Bash', status='OK',
               start=_stamp(1), attrs={'agent_id': 'sa-1'})
    assert len(blockers.live_blockers()) == 1


def test_an_sdk_card_carries_its_child_alias(tmp_db, _sdk_ask):
    """The sessions list shows the CLI child's row (the sdk- row is
    alias-hidden), so the awaiting-decision highlight matches through the
    card's alias ids."""
    _seed_sdk_link(tmp_db, 'sdk-run-1', 'cli-child')
    row = blockers.live_blockers()[0]
    assert row['alias_trace_ids'] == ['cli-child']


def test_an_earlier_completion_does_not_retire_a_newer_park(tmp_db):
    _seed_session(tmp_db)
    _seed_span(tmp_db, 'bash-0', name='tool.Bash', status='OK', start=_stamp(9))
    _seed_span(tmp_db, 'randomid-1', start=_stamp(1),
               attrs={'tool_name': 'Bash'})
    assert len(blockers.live_blockers()) == 1


def test_a_newer_main_prompt_retires_a_park(tmp_db):
    """The terminal does not accept a prompt while a permission menu holds
    it, so a newer main-loop prompt proves the park was decided."""
    _seed_session(tmp_db)
    _seed_span(tmp_db, 'randomid-1', start=_stamp(10), attrs={})
    _seed_span(tmp_db, 'prompt-1', name='prompt', status='OK', start=_stamp(2))
    assert blockers.live_blockers() == []


def test_an_older_prompt_leaves_the_park_waiting(tmp_db):
    _seed_session(tmp_db)
    _seed_span(tmp_db, 'prompt-1', name='prompt', status='OK', start=_stamp(30))
    _seed_span(tmp_db, 'randomid-1', start=_stamp(1), attrs={})
    assert len(blockers.live_blockers()) == 1


def test_a_subagent_prompt_does_not_retire_a_park(tmp_db):
    """Subagent launch prompts share `name='prompt'` but a parallel subagent
    can start while the main agent is parked — only a main-loop prompt is
    proof of an answer."""
    _seed_session(tmp_db)
    _seed_span(tmp_db, 'randomid-1', start=_stamp(10), attrs={})
    _seed_span(tmp_db, 'prompt-sa-agent1', name='prompt', status='OK',
               start=_stamp(2))
    assert len(blockers.live_blockers()) == 1


# ── The SDK leg ──────────────────────────────────────────────────

@pytest.fixture()
def _sdk_ask(monkeypatch):
    """One parked ask in the in-process registry, torn down with the test."""
    from lib.agent_sdk import registry
    loop = asyncio.new_event_loop()
    ask = registry.PendingAsk(
        trace_id='sdk-run-1', tool_use_id=_TU_A,
        tool_input={'questions': _ASK_ATTRS['questions']},
        future=loop.create_future(), loop=loop, kind='question',
        tool_name='AskUserQuestion', parked_at=_stamp(2))
    monkeypatch.setattr(registry, '_asks', {1: ask})
    yield ask
    loop.close()


def test_a_registry_ask_is_a_card(tmp_db, _sdk_ask):
    rows = blockers.live_blockers()
    assert len(rows) == 1
    row = rows[0]
    assert row['trace_id'] == 'sdk-run-1'
    assert row['sdk_owned'] is True
    assert row['answerable'] == 'question'
    assert row['span_id'] == _TU_A
    assert [o['label'] for o in row['options']] == [
        'Escape only, then warn', 'Escape, then type /exit', 'Detect + report only']


def test_a_registry_ask_and_its_child_spans_are_one_card(tmp_db, _sdk_ask):
    """Both writers observe the same prompt; the feed must not."""
    _seed_session(tmp_db, 'cli-child')
    _seed_sdk_link(tmp_db, 'sdk-run-1', 'cli-child')
    _seed_span(tmp_db, 'permreq-1', trace_id='cli-child', attrs=_ASK_ATTRS)
    rows = blockers.live_blockers()
    assert [r['trace_id'] for r in rows] == ['sdk-run-1']


def test_a_resolved_registry_ask_is_no_card(tmp_db, monkeypatch):
    from lib.agent_sdk import registry
    monkeypatch.setattr(registry, '_asks', {})
    assert blockers.live_blockers() == []


# ── Ordering + cap ───────────────────────────────────────────────

def test_every_parked_session_is_returned_oldest_first(tmp_db):
    for i in range(4):
        _seed_session(tmp_db, f'sess-{i}')
        _seed_span(tmp_db, 'permreq-1', trace_id=f'sess-{i}',
                   start=_stamp(9 - i), attrs={})
    rows = blockers.live_blockers()
    assert [r['trace_id'] for r in rows] == [f'sess-{i}' for i in range(4)]


def test_the_cap_bounds_the_feed(tmp_db):
    for i in range(8):
        _seed_session(tmp_db, f'sess-{i}')
        _seed_span(tmp_db, 'permreq-1', trace_id=f'sess-{i}',
                   start=_stamp(9 - i), attrs={})
    assert len(blockers.live_blockers(limit=5)) == 5


def test_stale_rows_cannot_crowd_out_a_real_park(tmp_db):
    """Rows do not decide presence, so a backlog of undismissed cards — 139
    in the live DB when this was row-driven — cannot displace the one
    genuinely parked session."""
    for i in range(30):
        _seed_card(f'dead-{i:02d}', minutes_ago=45, tmp_db=tmp_db)
    _seed_session(tmp_db, 'sess-alive')
    _seed_span(tmp_db, 'permreq-1', trace_id='sess-alive', attrs={})
    rows = blockers.live_blockers(limit=5)
    assert [r['trace_id'] for r in rows] == ['sess-alive']


# ── Who may answer from the banner ───────────────────────────────

def test_a_reachable_single_select_ask_is_answerable(tmp_db, monkeypatch):
    _reach(monkeypatch)
    _seed_session(tmp_db)
    _seed_span(tmp_db, 'toolu_1', attrs=_ASK_ATTRS)
    assert blockers.live_blockers()[0]['answerable'] == 'question'


def test_an_unreachable_ask_is_read_only(tmp_db):
    _seed_session(tmp_db)
    _seed_span(tmp_db, 'toolu_1', attrs=_ASK_ATTRS)
    row = blockers.live_blockers()[0]
    assert row['answerable'] is None
    assert len(row['options']) == 3  # shown as context, just not clickable


def test_a_multi_select_ask_is_read_only(tmp_db, monkeypatch):
    """A multi-select TUI needs per-option toggles the bridge cannot drive
    blindly — the same rule LiveQaSheet applies."""
    _reach(monkeypatch)
    _seed_session(tmp_db)
    attrs = json.loads(json.dumps(_ASK_ATTRS))
    attrs['questions'][0]['multiSelect'] = True
    _seed_span(tmp_db, 'toolu_1', attrs=attrs)
    assert blockers.live_blockers()[0]['answerable'] is None


def test_a_plan_on_an_owned_session_is_decidable(tmp_db, monkeypatch):
    _reach(monkeypatch, sdk=True)
    _seed_session(tmp_db)
    _seed_span(tmp_db, 'toolu_1', attrs={'kind': 'plan'})
    row = blockers.live_blockers()[0]
    assert row['answerable'] == 'decision'
    assert row['kind'] == 'plan'
    assert row['msg_key'] == PLAN_KEY


def test_a_plan_on_a_tmux_session_is_not_decidable(tmp_db, monkeypatch):
    """There is no channel to carry a typed allow/deny into someone else's
    terminal, and guessing keystrokes for one is what must not happen."""
    _reach(monkeypatch, reachable=True, sdk=False)
    _seed_session(tmp_db)
    _seed_span(tmp_db, 'toolu_1', attrs={'kind': 'plan'})
    assert blockers.live_blockers()[0]['answerable'] is None


def test_a_kindless_park_is_never_decidable(tmp_db, monkeypatch):
    """`kind` defaults to 'tool' for display when the attrs carry none.
    Gating allow/deny on that default offered a decision the parked call —
    an ask needing an option index — cannot accept."""
    _reach(monkeypatch, sdk=True)
    _seed_session(tmp_db)
    _seed_span(tmp_db, 'randomid-1', attrs={})
    assert blockers.live_blockers()[0]['answerable'] is None


# ── Hygiene: orphaned rows are retired, with a grace window ──────

def test_an_orphaned_card_is_healed_not_just_hidden(tmp_db):
    """Filtering leaves the row unread in the badge and the banner alive in
    any already-open tab; the read must retire the row for good."""
    _seed_session(tmp_db)
    row = _seed_card(minutes_ago=30, tmp_db=tmp_db)
    assert blockers.live_blockers() == []
    healed = store.get_message(row['id'])
    assert healed['dismissed_at'] is not None
    assert healed['read_at'] is not None
    assert store.unread_count() == 0


def test_a_fresh_orphan_is_left_alone(tmp_db):
    """The row is written moments before its park registers (emit precedes
    the span write), so a read landing in that gap must not reap a card
    whose park is about to appear."""
    _seed_session(tmp_db)
    row = _seed_card()
    assert blockers.live_blockers() == []
    assert store.get_message(row['id'])['dismissed_at'] is None


def test_a_claimed_card_is_not_healed(tmp_db):
    _seed_session(tmp_db)
    _seed_span(tmp_db, 'permreq-1', attrs=_ASK_ATTRS)
    row = _seed_card(minutes_ago=30, tmp_db=tmp_db)
    assert len(blockers.live_blockers()) == 1
    assert store.get_message(row['id'])['dismissed_at'] is None


# ── Wire contract ────────────────────────────────────────────────

def test_endpoint_returns_the_feed(tmp_db, flask_client):
    _seed_session(tmp_db)
    _seed_span(tmp_db, 'permreq-1', attrs=_ASK_ATTRS)
    _seed_card()
    data = flask_client.get('/api/agent-messages/blockers').get_json()
    assert [b['trace_id'] for b in data['blockers']] == [_TRACE]


def test_a_seeded_test_card_surfaces_for_the_harness(tmp_db):
    """Playwright seeds `is_test` rows with no spans behind them; the
    include_tests leg keeps that path working without letting test rows
    reach the real feed."""
    _seed_session(tmp_db)
    store.record_message(trace_id=_TRACE, body='q?', msg_type='blocker',
                        msg_key=PERM_KEY, is_test=True, dispatch_webhook=False)
    assert blockers.live_blockers() == []
    rows = blockers.live_blockers(include_tests=True)
    assert len(rows) == 1
    assert rows[0]['is_test'] is True


def test_permission_body_carries_no_markdown_emphasis(tmp_db):
    """`_1 option(s)…_` reached the operator as literal underscores: the body
    is rendered verbatim by the banner, not only by markdown push channels."""
    from lib.agent_messages.event_notify import _format_permission
    _, body = _format_permission({'tool_name': 'ExitPlanMode',
                                  'requested_permission': 'Use tool: ExitPlanMode',
                                  'option_count': 1})
    assert '_' not in body


def test_decision_keys_match_the_client(tmp_db):
    """`DECISION_KEYS` is mirrored in frontend/src/constants/inboxTypes.js;
    a key added on one side only silently stops raising the banner."""
    import pathlib
    import re
    from lib.agent_messages.event_notify import DECISION_KEYS
    src = pathlib.Path('frontend/src/constants/inboxTypes.js').read_text()
    declared = re.search(r'DECISION_KEYS = new Set\(\[([^\]]*)\]\)', src)
    client = set(re.findall(r"'([^']+)'", declared.group(1)))
    assert client == set(DECISION_KEYS)


# ── Emphasis stripping must not corrupt paths ────────────────────

@pytest.mark.parametrize('body,expected', [
    ('The agent needs approval to run **Bash**.',
     'The agent needs approval to run Bash.'),
    ('_1 option(s) — approve or deny in your session._',
     '1 option(s) — approve or deny in your session.'),
    # A permission body quotes the command being approved: mangling these
    # shows the operator a different file than the one they are allowing.
    ('Edit lib/__init__.py', 'Edit lib/__init__.py'),
    ('Edit __init__.py and __all__', 'Edit __init__.py and __all__'),
    ('a ** b ** c', 'a ** b ** c'),
    ('Run: rm *.py; check a_b_c and 3 * 4', 'Run: rm *.py; check a_b_c and 3 * 4'),
])
def test_emphasis_stripping_spares_paths_and_globs(tmp_db, body, expected):
    from lib.agent_messages.blockers import _question_text
    assert _question_text(body) == expected


# ── Single-writer emit + alias-aware resolve ─────────────────────

def test_hook_emit_is_muted_for_an_sdk_child_session(tmp_db, monkeypatch):
    """The runner is an owned agent's one decision-card writer; the child's
    own hooks observing the same prompt must not file a twin — the measured
    dual-writer bug behind the derivation rewrite."""
    from lib.agent_messages import event_notify
    monkeypatch.setattr('lib.agent_messages.events.is_enabled', lambda k: True)
    _seed_sdk_link(tmp_db, 'sdk-run-1', 'cli-child')
    assert event_notify.notify_permission_request(
        trace_id='cli-child', attrs={'tool_name': 'Bash'}) is False
    assert event_notify.notify_permission_request(
        trace_id='sess-plain', attrs={'tool_name': 'Bash'}) is True


def test_resolve_retires_both_halves_of_an_aliased_pair(tmp_db, monkeypatch):
    from lib.agent_messages import events
    _seed_sdk_link(tmp_db, 'sdk-run-1', 'cli-child')
    _seed_card('sdk-run-1')
    _seed_card('cli-child')
    events.resolve('sdk-run-1', PERM_KEY)
    assert store.live_keyed_message('sdk-run-1', PERM_KEY) is None
    assert store.live_keyed_message('cli-child', PERM_KEY) is None


def test_resolve_from_the_cli_half_reaches_the_sdk_card(tmp_db, monkeypatch):
    """The child's hooks resolve under the CLI id; the runner's card lives
    under the sdk- id. Either entry point must retire both."""
    from lib.agent_messages import events
    _seed_sdk_link(tmp_db, 'sdk-run-1', 'cli-child')
    _seed_card('sdk-run-1')
    events.resolve('cli-child', PERM_KEY)
    assert store.live_keyed_message('sdk-run-1', PERM_KEY) is None
