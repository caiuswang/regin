"""The parked-decision feed behind the blocker banner (lib/agent_messages/blockers).

The banner's only leg that survives a reload. Each test here pins one rule the
assembly exists to hold — read state is irrelevant, liveness is recency not
`status`, options come off the span, and a span that resolved retires its card.
"""

from __future__ import annotations

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
        conn = _conn(tmp_db)
        try:
            conn.execute("UPDATE agent_messages SET created_at = ? WHERE id = ?",
                         (_stamp(minutes_ago), row['id']))
            conn.commit()
        finally:
            conn.close()
    return row


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


# ── The gate: what counts as waiting ─────────────────────────────

def test_a_live_parked_card_is_returned(tmp_db):
    _seed_session(tmp_db)
    _seed_card()
    rows = blockers.live_blockers()
    assert [r['trace_id'] for r in rows] == [_TRACE]


def test_a_read_card_is_still_waiting(tmp_db):
    """Reading is not answering. The banner outlives the acknowledgement."""
    _seed_session(tmp_db)
    card = _seed_card()
    store.mark_read([card['id']])
    assert len(blockers.live_blockers()) == 1


def test_mark_all_read_leaves_the_card_waiting(tmp_db):
    _seed_session(tmp_db)
    _seed_card()
    store.mark_all_read()
    assert len(blockers.live_blockers()) == 1


def test_a_dismissed_card_is_gone(tmp_db):
    _seed_session(tmp_db)
    _seed_card()
    store.dismiss_keyed(_TRACE, PERM_KEY)
    assert blockers.live_blockers() == []


def test_a_silent_session_drops_out_even_while_status_active(tmp_db):
    """`status` is exactly wrong for the sessions this matters for: an agent
    killed mid-prompt never writes its end event and stays 'active' forever."""
    _seed_session(tmp_db, status='active', minutes_ago=45)
    _seed_card(minutes_ago=45, tmp_db=tmp_db)
    assert blockers.live_blockers() == []


def test_an_old_card_on_a_session_with_no_row_drops_out(tmp_db):
    _seed_card(trace_id='sess-unknown', minutes_ago=45, tmp_db=tmp_db)
    assert blockers.live_blockers() == []


def test_a_just_written_card_is_live_before_its_session_row_catches_up(tmp_db):
    """The hook that wrote it was running seconds ago. Gating on the sessions
    table alone makes a genuine banner flash and vanish."""
    _seed_card(trace_id='sess-not-yet-listed')
    assert len(blockers.live_blockers()) == 1


def test_a_non_decision_message_is_not_a_blocker(tmp_db):
    _seed_session(tmp_db)
    store.record_message(trace_id=_TRACE, body='fyi', msg_type='warning',
                         dispatch_webhook=False)
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


def test_a_resolved_named_span_retires_its_card(tmp_db):
    """`events.resolve` frequently never delivers, so the span resolving is
    the strongest signal there is that the prompt was answered."""
    _seed_session(tmp_db)
    _seed_span(tmp_db, 'toolu_1', attrs=_ASK_ATTRS)
    _seed_card(span_id='toolu_1')
    assert len(blockers.live_blockers()) == 1
    _resolve_span(tmp_db, 'toolu_1')
    assert blockers.live_blockers() == []


def test_a_card_with_no_span_still_surfaces(tmp_db):
    """Claude Code omits tool_use_id pre-tool, so most hook-observed cards
    carry no span id at all. They must still raise the banner — read-only."""
    _seed_session(tmp_db)
    _seed_card(body='Use tool: Bash')
    row = blockers.live_blockers()[0]
    assert row['options'] == []
    assert row['answerable'] is None


def test_an_unnamed_card_falls_back_to_the_newest_pending_span(tmp_db):
    _seed_session(tmp_db)
    _seed_span(tmp_db, 'old', start=_stamp(9), attrs={'kind': 'question'})
    _seed_span(tmp_db, 'new', start=_stamp(1), attrs=_ASK_ATTRS)
    _seed_card()
    row = blockers.live_blockers()[0]
    # The span supplies the options; the card's own (absent) id is still what
    # is emitted, so `bridge-decide` is never handed a placeholder.
    assert len(row['options']) == 3
    assert row['span_id'] is None


# ── Who may answer from the banner ───────────────────────────────

def test_a_reachable_single_select_ask_is_answerable(tmp_db, monkeypatch):
    _reach(monkeypatch)
    _seed_session(tmp_db)
    _seed_span(tmp_db, 'toolu_1', attrs=_ASK_ATTRS)
    _seed_card(span_id='toolu_1')
    assert blockers.live_blockers()[0]['answerable'] == 'question'


def test_an_unreachable_ask_is_read_only(tmp_db):
    _seed_session(tmp_db)
    _seed_span(tmp_db, 'toolu_1', attrs=_ASK_ATTRS)
    _seed_card(span_id='toolu_1')
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
    _seed_card(span_id='toolu_1')
    assert blockers.live_blockers()[0]['answerable'] is None


def test_a_plan_on_an_owned_session_is_decidable(tmp_db, monkeypatch):
    _reach(monkeypatch, sdk=True)
    _seed_session(tmp_db)
    _seed_span(tmp_db, 'toolu_1', attrs={'kind': 'plan'})
    _seed_card(key=PLAN_KEY, span_id='toolu_1')
    row = blockers.live_blockers()[0]
    assert row['answerable'] == 'decision'
    assert row['kind'] == 'plan'


def test_a_plan_on_a_tmux_session_is_not_decidable(tmp_db, monkeypatch):
    """There is no channel to carry a typed allow/deny into someone else's
    terminal, and guessing keystrokes for one is what must not happen."""
    _reach(monkeypatch, reachable=True, sdk=False)
    _seed_session(tmp_db)
    _seed_span(tmp_db, 'toolu_1', attrs={'kind': 'plan'})
    _seed_card(key=PLAN_KEY, span_id='toolu_1')
    assert blockers.live_blockers()[0]['answerable'] is None


# ── Several at once ──────────────────────────────────────────────

def test_every_parked_session_is_returned_oldest_first(tmp_db):
    for i in range(4):
        _seed_session(tmp_db, f'sess-{i}')
        _seed_card(f'sess-{i}')
    rows = blockers.live_blockers()
    assert [r['trace_id'] for r in rows] == [f'sess-{i}' for i in range(4)]


def test_one_card_per_session_and_key(tmp_db):
    """A re-prompt supersedes its card rather than stacking a second one."""
    _seed_session(tmp_db)
    _seed_card(body='first')
    _seed_card(body='second')
    rows = blockers.live_blockers()
    assert len(rows) == 1
    assert rows[0]['body'] == 'second'


# ── Wire contract ────────────────────────────────────────────────

def test_endpoint_returns_the_feed(tmp_db, flask_client):
    _seed_session(tmp_db)
    _seed_card()
    data = flask_client.get('/api/agent-messages/blockers').get_json()
    assert [b['trace_id'] for b in data['blockers']] == [_TRACE]


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


# ── Binding a card to the RIGHT parked call ──────────────────────

# `notify_permission_request` stamps the card with the full tool_use_id, but
# the parked span is a placeholder keyed on a truncated copy of it. Equality
# never matched, so every card fell through to "newest pending span".
_TU_A = 'toolu_01AAAAAAAAAAAAAAAAAAAAAA'
_TU_B = 'toolu_01BBBBBBBBBBBBBBBBBBBBBB'


def _ask_attrs(*labels):
    return {'kind': 'question',
            'questions': [{'question': 'pick', 'options': [
                {'label': lab} for lab in labels]}]}


def test_a_card_binds_to_its_own_pending_placeholder(tmp_db, monkeypatch):
    """Two prompts parked at once — issue 5's own case. Binding the card to
    the NEWEST pending span would hand `bridge-answer` an option_index into
    the wrong question, delivering a wrong answer rather than a wrong label."""
    from lib.trace.pending_spans import perm_pending_id
    _reach(monkeypatch)
    _seed_session(tmp_db)
    _seed_span(tmp_db, perm_pending_id(_TU_A), start=_stamp(9),
               attrs=_ask_attrs('A-one', 'A-two'))
    _seed_span(tmp_db, perm_pending_id(_TU_B), start=_stamp(1),
               attrs=_ask_attrs('B-one', 'B-two'))
    _seed_card(span_id=_TU_A)
    row = blockers.live_blockers()[0]
    assert [o['label'] for o in row['options']] == ['A-one', 'A-two']


def test_a_card_whose_span_is_missing_stays_read_only(tmp_db, monkeypatch):
    """Read-only is the safe failure: guessing which parked prompt a card
    belongs to is how a click answers the wrong question."""
    _reach(monkeypatch)
    _seed_session(tmp_db)
    _seed_span(tmp_db, 'permreq-toolu_01ZZZZZ', attrs=_ask_attrs('Not mine'))
    _seed_card(span_id=_TU_A, body='Q?\n• From the body')
    row = blockers.live_blockers()[0]
    assert row['answerable'] is None
    assert [o['label'] for o in row['options']] == ['From the body']


def test_a_resolved_tool_call_retires_its_card(tmp_db):
    """The resolved twin of a permission gate is the tool's OWN span, which no
    decision-span name matches — it has to be found by tool_use_id."""
    _seed_session(tmp_db)
    _seed_span(tmp_db, 'permreq-toolu_01AAAAA', attrs=_ask_attrs('X'))
    _seed_card(span_id=_TU_A)
    assert len(blockers.live_blockers()) == 1
    _seed_span(tmp_db, 'real-span', name='tool.Bash', status='OK',
               attrs={'tool_use_id': _TU_A})
    assert blockers.live_blockers() == []


def test_a_spanless_card_is_never_decidable(tmp_db, monkeypatch):
    """`kind` defaults to 'tool' for display when no span was found. Gating
    allow/deny on that default offered a decision the parked call — an ask
    needing an option index — cannot accept."""
    _reach(monkeypatch, sdk=True)
    _seed_session(tmp_db)
    _seed_card(span_id=_TU_A, body='Q?\n• Red\n• Blue')
    row = blockers.live_blockers()[0]
    assert row['answerable'] is None


def test_the_cap_applies_after_filtering(tmp_db):
    """Capping the READ instead would let stale-but-undismissed cards crowd a
    genuinely parked session out of the feed entirely."""
    for i in range(30):
        _seed_card(f'dead-{i:02d}', minutes_ago=45, tmp_db=tmp_db)
    _seed_session(tmp_db, 'sess-alive')
    _seed_card('sess-alive')
    rows = blockers.live_blockers(limit=5)
    assert [r['trace_id'] for r in rows] == ['sess-alive']


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


def test_a_card_never_emits_a_placeholder_span_id(tmp_db, monkeypatch):
    """`bridge-decide` forwards `span_id` as `tool_use_id` and the runner
    exact-matches it against the parked call, so a placeholder id guarantees
    'no pending permission request'. Emitting nothing lets it resolve the
    oldest park, which is right for a card that never carried an id."""
    from lib.trace.pending_spans import perm_pending_id
    _reach(monkeypatch, sdk=True)
    _seed_session(tmp_db)
    _seed_span(tmp_db, perm_pending_id(_TU_A), attrs={'kind': 'plan'})
    _seed_card(key=PLAN_KEY)  # notify_plan_ready never stamps a span_id
    row = blockers.live_blockers()[0]
    assert row['span_id'] is None
    assert row['answerable'] == 'decision'


# ── Self-heal: a prompt answered outside this UI retires its card ─

def _backdate(tmp_db, message_id, *, minutes_ago):
    """Move a card's whole raise history into the past — `updated_at` too,
    which `record_message` always stamps 'now'."""
    conn = _conn(tmp_db)
    try:
        conn.execute(
            "UPDATE agent_messages SET created_at = ?, updated_at = ? "
            " WHERE id = ?", (_stamp(minutes_ago), _stamp(minutes_ago), message_id))
        conn.commit()
    finally:
        conn.close()


def test_an_answered_card_is_dismissed_not_just_filtered(tmp_db):
    """Filtering leaves the row unread in the badge and the banner alive in
    any already-open tab; the read must retire the row for good."""
    _seed_session(tmp_db)
    _seed_span(tmp_db, 'permreq-tu_a', attrs={'tool_use_id': 'tu_a'})
    row = _seed_card(span_id='tu_a')
    assert len(blockers.live_blockers()) == 1
    _resolve_span(tmp_db, 'permreq-tu_a')
    assert blockers.live_blockers() == []
    healed = store.get_message(row['id'])
    assert healed['dismissed_at'] is not None
    assert healed['read_at'] is not None
    assert store.unread_count() == 0


def test_a_prompt_after_a_spanless_card_retires_it(tmp_db):
    """No span to consult, but the user prompted after the raise — nothing
    stays parked across a prompt, so the card was answered in the terminal."""
    _seed_session(tmp_db)
    row = _seed_card()
    _backdate(tmp_db, row['id'], minutes_ago=30)
    _seed_span(tmp_db, 'prompt-1', name='prompt', status='OK', start=_stamp(5))
    assert blockers.live_blockers() == []
    assert store.get_message(row['id'])['dismissed_at'] is not None


def test_a_prompt_before_the_card_leaves_it_waiting(tmp_db):
    _seed_session(tmp_db)
    _seed_span(tmp_db, 'prompt-1', name='prompt', status='OK', start=_stamp(30))
    _seed_card()
    assert len(blockers.live_blockers()) == 1


def test_a_reraised_card_survives_the_prompt_between_raises(tmp_db):
    """A superseded card keeps its original created_at, so a prompt between
    the first raise and the re-raise must not read as an answer to the live
    question — `updated_at` anchors the check."""
    _seed_session(tmp_db)
    row = _seed_card(body='first ask')
    _backdate(tmp_db, row['id'], minutes_ago=30)
    _seed_span(tmp_db, 'prompt-1', name='prompt', status='OK', start=_stamp(10))
    _seed_card(body='second ask')  # supersede: version bumps, created_at stays
    rows = blockers.live_blockers()
    assert len(rows) == 1
    assert rows[0]['body'] == 'second ask'


def test_a_parked_named_span_outweighs_a_newer_prompt(tmp_db):
    """Fail closed: while the card's own span is PENDING the agent IS parked,
    whatever else the session has been doing."""
    _seed_session(tmp_db)
    _seed_span(tmp_db, 'permreq-tu_a', attrs={'tool_use_id': 'tu_a'})
    row = _seed_card(span_id='tu_a')
    _backdate(tmp_db, row['id'], minutes_ago=30)
    _seed_span(tmp_db, 'prompt-1', name='prompt', status='OK', start=_stamp(5))
    assert len(blockers.live_blockers()) == 1


def test_a_subagent_prompt_does_not_retire_a_card(tmp_db):
    """Subagent launch prompts share `name='prompt'` but a parallel subagent
    can start while the main agent is parked — only a main-loop prompt is
    proof of an answer."""
    _seed_session(tmp_db)
    row = _seed_card()
    _backdate(tmp_db, row['id'], minutes_ago=30)
    _seed_span(tmp_db, 'prompt-sa-agent1', name='prompt', status='OK',
               start=_stamp(5))
    assert len(blockers.live_blockers()) == 1
    assert store.get_message(row['id'])['dismissed_at'] is None
