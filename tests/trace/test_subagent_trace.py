"""A subagent addressed as its own trace (`sa-<agent_id>`), resolved at serve
time out of the parent trace it is stored in.

Guards the three ways this goes wrong: the child leaks the parent's rows (or
loses its own), the agent scoping survives the re-key and leaves the child with
no anchoring prompt, and the mechanism leaks into ordinary single-trace reads.
"""

from __future__ import annotations

import json
import re
import sqlite3

from lib.trace.alias import ORIGIN_KEY, trace_group
from lib.trace.projection import (
    _fetch_spans_for_group,
    _fetch_subagent_spans,
    _normalize_agent_ids,
)
from lib.trace.subagent_trace import (
    is_subagent_trace_id,
    resolve_subagent_trace,
    subagent_trace_id,
)

PARENT = 'parent-trace-0001'
AGENT = 'agentX'
OTHER = 'agentY'
SA = f'sa-{AGENT}'
SDK_TRACE = 'sdk-0001'
LAUNCH = 'toolu_launchX'
LAUNCH_OTHER = 'toolu_launchY'


def _insert_span(db_path, span_id, name, start_time, *, agent_id=None,
                 parent_id=None, attrs=None, trace_id=PARENT, end_time=None,
                 input_tokens=None, output_tokens=None, cost_usd=None):
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute(
            "INSERT INTO session_spans (trace_id, span_id, name, start_time, "
            "parent_id, status_code, agent_id, attributes, end_time, "
            "input_tokens, output_tokens, cost_usd) "
            "VALUES (?, ?, ?, ?, ?, 'OK', ?, ?, ?, ?, ?, ?)",
            (trace_id, span_id, name, start_time, parent_id, agent_id,
             json.dumps(attrs or {}), end_time, input_tokens, output_tokens,
             cost_usd),
        )
        conn.commit()
    finally:
        conn.close()


def _seed_parent_with_two_agents(db_path):
    _insert_span(db_path, 'prompt-main', 'prompt', '2026-04-18T12:00:00',
                 attrs={'text': 'the user goal'})
    _insert_span(db_path, f'subagent.start-{AGENT}', 'subagent.start',
                 '2026-04-18T12:00:01', agent_id=AGENT,
                 attrs={'agent_id': AGENT})
    _insert_span(db_path, f'prompt-sa-{AGENT}', 'prompt',
                 '2026-04-18T12:00:02', agent_id=AGENT,
                 attrs={'text': 'go do the subtask', 'agent_id': AGENT})
    _insert_span(db_path, 'toolA', 'tool.Read', '2026-04-18T12:00:03',
                 agent_id=AGENT, attrs={'tool_name': 'Read', 'agent_id': AGENT})
    _insert_span(db_path, 'toolB', 'tool.Edit', '2026-04-18T12:00:04',
                 agent_id=AGENT, attrs={'tool_name': 'Edit', 'agent_id': AGENT})
    _insert_span(db_path, 'toolC', 'tool.Grep', '2026-04-18T12:00:05',
                 agent_id=OTHER, attrs={'tool_name': 'Grep', 'agent_id': OTHER})


def _conn():
    from lib.orm.engine import get_connection
    return get_connection()


# ── id helpers ───────────────────────────────────────────────────────

def test_subagent_trace_id_round_trips():
    assert subagent_trace_id(AGENT) == SA
    assert is_subagent_trace_id(subagent_trace_id(AGENT))


def test_is_subagent_trace_id_safe_on_non_subagent_ids():
    """Callers hold ids straight off a request path or a DB row."""
    assert not is_subagent_trace_id(None)
    assert not is_subagent_trace_id('')
    assert not is_subagent_trace_id('11111111-2222-3333-4444-555555555555')


# ── resolution ───────────────────────────────────────────────────────

def test_resolve_returns_parent_and_agent(tmp_db):
    _seed_parent_with_two_agents(tmp_db)
    conn = _conn()
    try:
        assert resolve_subagent_trace(conn, SA) == (PARENT, AGENT)
    finally:
        conn.close()


def test_resolve_is_none_for_an_ordinary_trace_id(tmp_db):
    _seed_parent_with_two_agents(tmp_db)
    conn = _conn()
    try:
        assert resolve_subagent_trace(conn, PARENT) is None
        assert resolve_subagent_trace(conn, None) is None
    finally:
        conn.close()


def test_resolve_is_none_for_an_unknown_agent(tmp_db):
    _seed_parent_with_two_agents(tmp_db)
    conn = _conn()
    try:
        assert resolve_subagent_trace(conn, 'sa-nobody') is None
    finally:
        conn.close()


def test_resolve_degrades_to_none_without_the_agent_column():
    """A DB predating the promoted column must read as "not a subagent"
    rather than 500."""
    conn = sqlite3.connect(':memory:')
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE session_spans (id INTEGER PRIMARY KEY, "
                 "trace_id TEXT)")
    assert resolve_subagent_trace(conn, SA) is None


# ── fetch normalization ──────────────────────────────────────────────

def test_fetch_for_subagent_id_omits_the_agents_own_markers(tmp_db):
    _seed_parent_with_two_agents(tmp_db)
    conn = _conn()
    try:
        rows = _fetch_spans_for_group(conn, SA)
    finally:
        conn.close()

    assert [r['span_id'] for r in rows] == [
        f'prompt-sa-{AGENT}', 'toolA', 'toolB',
    ]
    for r in rows:
        assert r['trace_id'] == SA
        assert 'agent_id' not in r
        assert 'agent_id' not in r['attributes']
        assert r[ORIGIN_KEY] == 'hook'


def test_fetch_for_subagent_id_honours_the_window(tmp_db):
    _seed_parent_with_two_agents(tmp_db)
    conn = _conn()
    try:
        rows = _fetch_spans_for_group(
            conn, SA, window_start='2026-04-18T12:00:02',
            window_end='2026-04-18T12:00:04')
    finally:
        conn.close()
    assert [r['span_id'] for r in rows] == [f'prompt-sa-{AGENT}', 'toolA']


def test_fetch_for_an_ordinary_trace_id_is_unchanged(tmp_db):
    """The 99% case: every row of the parent, agent scoping intact, no
    `agent_id` key added to the payload."""
    _seed_parent_with_two_agents(tmp_db)
    conn = _conn()
    try:
        rows = _fetch_spans_for_group(conn, [PARENT])
    finally:
        conn.close()

    assert [r['span_id'] for r in rows] == [
        'prompt-main', f'subagent.start-{AGENT}', f'prompt-sa-{AGENT}',
        'toolA', 'toolB', 'toolC',
    ]
    by_id = {r['span_id']: r for r in rows}
    for r in rows:
        assert r['trace_id'] == PARENT
        assert r[ORIGIN_KEY] == 'hook'
        assert 'agent_id' not in r
    assert by_id['toolA']['attributes']['agent_id'] == AGENT
    assert by_id['toolC']['attributes']['agent_id'] == OTHER
    assert by_id['prompt-main']['attributes'] == {'text': 'the user goal'}


# ── end-to-end projection ────────────────────────────────────────────

def _flatten(tree):
    out: dict[str, str | None] = {}

    def walk(nodes):
        for n in nodes:
            out[n['data']['span_id']] = n['data'].get('parent_id')
            walk(n.get('children', []))

    walk(tree)
    return out


def test_projecting_a_subagent_id_roots_on_its_launch_prompt(tmp_db):
    """The child renders as an ordinary conversation: its launch prompt anchors
    the tree (no longer suppressed as agent-scoped) and its two tools hang
    beneath it. The parent's main prompt and the other agent's work stay out,
    and so does the agent's own `subagent.start` — that marker is the PARENT's
    card for this agent, not work the agent did."""
    from lib.trace.trace_service import fetch_session_projection
    _seed_parent_with_two_agents(tmp_db)

    widened, tree = fetch_session_projection(SA)
    placed = _flatten(tree)

    assert set(placed) == {f'prompt-sa-{AGENT}', 'toolA', 'toolB'}
    assert 'prompt-main' not in placed
    assert 'toolC' not in placed
    roots = [n['data']['span_id'] for n in tree]
    assert f'prompt-sa-{AGENT}' in roots
    assert placed[f'prompt-sa-{AGENT}'] is None
    assert placed['toolA'] == f'prompt-sa-{AGENT}'
    assert placed['toolB'] == f'prompt-sa-{AGENT}'
    assert all(s['trace_id'] == SA for s in widened)


def test_projecting_the_parent_keeps_every_span_by_default(tmp_db):
    """The narrowing is a display choice, opted into per call: a reader that
    analyses the session (the grader's evidence index) still gets every row
    the agents produced."""
    from lib.trace.trace_service import fetch_session_projection
    _seed_parent_with_two_agents(tmp_db)

    _widened, tree = fetch_session_projection(PARENT)
    placed = _flatten(tree)

    assert {'prompt-main', 'toolA', 'toolB', 'toolC'} <= set(placed)
    assert placed['toolA'] == f'subagent.start-{AGENT}'


def test_projecting_the_parent_keeps_the_marker_not_the_agents_work(tmp_db):
    """The parent's side of the split: its own turn plus the announcement of
    each agent, whose spans are the child trace's to render."""
    from lib.trace.trace_service import fetch_session_projection
    _seed_parent_with_two_agents(tmp_db)

    _widened, tree = fetch_session_projection(PARENT, drop_delegated=True)
    placed = _flatten(tree)

    assert 'prompt-main' in placed
    assert placed[f'subagent.start-{AGENT}'] == 'prompt-main'
    assert 'toolA' not in placed
    assert 'toolB' not in placed


def test_an_agent_with_no_marker_keeps_rendering_inline(tmp_db):
    """`toolC`'s agent emitted no marker, so nothing in this trace could stand
    for it or link to its child — 128 of the 3,533 agents in the local store,
    11,956 spans. Hiding it would lose the work outright."""
    from lib.trace.trace_service import fetch_session_projection
    _seed_parent_with_two_agents(tmp_db)

    _widened, tree = fetch_session_projection(PARENT, drop_delegated=True)
    placed = _flatten(tree)

    assert 'toolC' in placed


# ── anchor precedence for a promptless child ─────────────────────────

def _seed_promptless_agent(db_path, *, with_start=True):
    """The common stored shape: an agent whose launch prompt was never
    recorded, so only its `subagent.start` marker names the run."""
    _insert_span(db_path, 'prompt-main', 'prompt', '2026-04-18T12:00:00',
                 attrs={'text': 'the user goal'})
    if with_start:
        _insert_span(db_path, f'subagent.start-{AGENT}', 'subagent.start',
                     '2026-04-18T12:00:01', agent_id=AGENT,
                     attrs={'agent_id': AGENT})
    _insert_span(db_path, 'toolA', 'tool.Bash', '2026-04-18T12:00:03',
                 agent_id=AGENT, attrs={'tool_name': 'Bash', 'agent_id': AGENT})
    _insert_span(db_path, 'toolB', 'tool.Bash', '2026-04-18T12:00:04',
                 agent_id=AGENT, attrs={'tool_name': 'Bash', 'agent_id': AGENT})
    _insert_span(db_path, 'resp1', 'assistant_response', '2026-04-18T12:00:05',
                 agent_id=AGENT, attrs={'agent_id': AGENT})


def test_promptless_child_roots_on_a_turn_derived_from_its_start(tmp_db):
    """No `prompt-sa-…` was ever written for 75% of stored agents. Without an
    anchor every span stays a root and the child renders as a flat pile, so the
    agent's own start marker is re-read as the turn the run opens on — the same
    row and id, but a task prompt here rather than a card announcing the agent
    to itself."""
    from lib.trace.trace_service import fetch_session_projection
    _seed_promptless_agent(tmp_db)

    widened, tree = fetch_session_projection(SA)
    placed = _flatten(tree)

    assert [n['data']['span_id'] for n in tree] == [f'subagent.start-{AGENT}']
    assert [n['data']['name'] for n in tree] == ['prompt']
    assert set(placed) == {
        f'subagent.start-{AGENT}', 'toolA', 'toolB', 'resp1',
    }
    for span_id in ('toolA', 'toolB', 'resp1'):
        assert placed[span_id] == f'subagent.start-{AGENT}'
    assert not [s for s in widened if s['name'].startswith('subagent.')]


def test_the_derived_root_carries_the_agents_launch_prompt(tmp_db):
    """What the turn says comes off the marker itself, one rung longer than the
    header's `description or agent_type`: the launch prompt where the marker
    kept it."""
    from lib.trace.trace_service import fetch_session_projection
    _insert_span(tmp_db, f'subagent.start-{AGENT}', 'subagent.start',
                 '2026-04-18T12:00:01', agent_id=AGENT,
                 attrs={'agent_id': AGENT, 'agent_type': 'Explore',
                        'prompt': 'find the caller', 'description': 'trace it'})
    _insert_span(tmp_db, 'toolA', 'tool.Bash', '2026-04-18T12:00:03',
                 agent_id=AGENT, attrs={'agent_id': AGENT})

    widened, _tree = fetch_session_projection(SA)
    root = [s for s in widened if s['span_id'] == f'subagent.start-{AGENT}'][0]

    assert root['name'] == 'prompt'
    assert root['attributes']['text'] == 'find the caller'
    assert root['attributes']['agent_type'] == 'Explore'


def test_a_markerless_description_still_anchors_the_child(tmp_db):
    """A marker carrying no text at all still holds the tree: a turn that says
    nothing beats a flat pile of rows."""
    from lib.trace.trace_service import fetch_session_projection
    _seed_promptless_agent(tmp_db)

    widened, tree = fetch_session_projection(SA)
    root = [s for s in widened if s['span_id'] == f'subagent.start-{AGENT}'][0]

    assert len(tree) == 1
    assert 'text' not in root['attributes']


def test_launch_prompt_outranks_the_derived_root(tmp_db):
    """Precedence guard: where a real launch prompt exists it anchors the tree
    and no turn is derived from the marker — which is dropped either way."""
    from lib.trace.trace_service import fetch_session_projection
    _seed_parent_with_two_agents(tmp_db)

    widened, tree = fetch_session_projection(SA)
    placed = _flatten(tree)

    assert f'subagent.start-{AGENT}' not in placed
    assert [n['data']['span_id'] for n in tree] == [f'prompt-sa-{AGENT}']
    assert placed[f'prompt-sa-{AGENT}'] is None
    assert placed['toolA'] == f'prompt-sa-{AGENT}'
    assert placed['toolB'] == f'prompt-sa-{AGENT}'
    assert not [s for s in widened
                if (s['attributes'] or {}).get('agent_root')]


def test_child_with_neither_anchor_stays_flat(tmp_db):
    """Genuinely anchorless data (320 of 3,519 agents): no anchor is
    synthesized, and the read does not raise."""
    from lib.trace.trace_service import fetch_session_projection
    _seed_promptless_agent(tmp_db, with_start=False)

    _widened, tree = fetch_session_projection(SA)
    placed = _flatten(tree)

    assert sorted(placed) == ['resp1', 'toolA', 'toolB']
    assert all(parent is None for parent in placed.values())


def test_parent_trace_never_grafts_orphans_under_a_subagent_start(tmp_db):
    """Non-regression: in the parent a `subagent.start` is one marker among
    many agents', so a main-loop orphan must not nest inside it."""
    from lib.trace.trace_service import fetch_session_projection
    _insert_span(tmp_db, f'subagent.start-{AGENT}', 'subagent.start',
                 '2026-04-18T12:00:01', agent_id=AGENT,
                 attrs={'agent_id': AGENT})
    _insert_span(tmp_db, 'mainloop-orphan', 'rule.check',
                 '2026-04-18T12:00:02')

    _widened, tree = fetch_session_projection(PARENT)
    placed = _flatten(tree)

    assert placed['mainloop-orphan'] is None
    assert placed[f'subagent.start-{AGENT}'] is None


# ── alias interaction ────────────────────────────────────────────────

def test_subagent_id_is_never_widened_into_an_alias_group(tmp_db):
    conn = _conn()
    try:
        assert trace_group(conn, 'sa-xyz') == ['sa-xyz']
    finally:
        conn.close()


# ── the two writers' agent_id namespaces ─────────────────────────────

def _link_alias(db_path, run_trace=SDK_TRACE, child=PARENT):
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute("INSERT INTO agent_runs (trace_id, status, cli_session_id) "
                     "VALUES (?, 'running', ?)", (run_trace, child))
        conn.commit()
    finally:
        conn.close()


def _seed_both_writers(db_path, *, marker=True, launch=LAUNCH):
    """A regin-launched run as both writers record it: the hook stamps the
    roster `agent_id` in the child's trace, the SDK runner stamps the launching
    `tool.Agent` call's id in its own."""
    _insert_span(db_path, 'prompt-main', 'prompt', '2026-04-18T12:00:00',
                 attrs={'text': 'the user goal'})
    if marker:
        attrs = {'agent_id': AGENT}
        if launch:
            attrs['launch_tool_use_id'] = launch
        _insert_span(db_path, f'subagent.start-{AGENT}', 'subagent.start',
                     '2026-04-18T12:00:01', agent_id=AGENT, attrs=attrs)
    _insert_span(db_path, 'hook-tool', 'tool.Read', '2026-04-18T12:00:02',
                 agent_id=AGENT, attrs={'tool_name': 'Read', 'agent_id': AGENT})
    _insert_span(db_path, 'sdk-tool', 'tool.Bash', '2026-04-18T12:00:03',
                 agent_id=LAUNCH, trace_id=SDK_TRACE,
                 attrs={'tool_name': 'Bash', 'agent_id': LAUNCH})
    _insert_span(db_path, 'sdk-resp', 'assistant_response',
                 '2026-04-18T12:00:04', agent_id=LAUNCH, trace_id=SDK_TRACE,
                 attrs={'agent_id': LAUNCH})


def _agent_ids(rows):
    return {r['span_id']: r['attributes'].get('agent_id') for r in rows}


def test_group_fetch_resolves_sdk_agent_ids_to_the_roster_id(tmp_db):
    """The runner's `parent_tool_use_id` namespace is joined back to the roster
    one through the marker, so the subagent's SDK-written work stops reading as
    the main agent's."""
    _seed_both_writers(tmp_db)
    conn = _conn()
    try:
        rows = _fetch_spans_for_group(conn, [PARENT, SDK_TRACE])
    finally:
        conn.close()

    assert _agent_ids(rows) == {
        'prompt-main': None,
        f'subagent.start-{AGENT}': AGENT,
        'hook-tool': AGENT,
        'sdk-tool': AGENT,
        'sdk-resp': AGENT,
    }


def test_normalization_rewrites_the_column_and_the_attributes_together(tmp_db):
    """The promoted column and the JSON must never disagree: a reader keying off
    either one has to see the same agent."""
    marker = {'span_id': 'm', 'name': 'subagent.start', 'agent_id': AGENT,
              'attributes': {'agent_id': AGENT, 'launch_tool_use_id': LAUNCH}}
    sdk_row = {'span_id': 'sdk-tool', 'name': 'tool.Bash', 'agent_id': LAUNCH,
               'attributes': {'agent_id': LAUNCH}}

    _normalize_agent_ids([marker, sdk_row])

    assert sdk_row['agent_id'] == AGENT
    assert sdk_row['attributes']['agent_id'] == AGENT


def test_sdk_rows_are_untouched_without_a_launch_link(tmp_db):
    """Graceful degradation: the link only exists where the subagent's meta file
    did, and a row that resolves to nothing reads exactly as it did before."""
    _seed_both_writers(tmp_db, launch=None)
    conn = _conn()
    try:
        with_marker = _fetch_spans_for_group(conn, [PARENT, SDK_TRACE])
    finally:
        conn.close()
    assert _agent_ids(with_marker)['sdk-tool'] == LAUNCH

    conn = _conn()
    try:
        conn.execute("DELETE FROM session_spans WHERE name = 'subagent.start'")
        conn.commit()
        without_marker = _fetch_spans_for_group(conn, [PARENT, SDK_TRACE])
    finally:
        conn.close()
    assert _agent_ids(without_marker)['sdk-tool'] == LAUNCH
    assert _agent_ids(without_marker)['sdk-resp'] == LAUNCH


def test_a_row_already_carrying_the_roster_id_is_not_rewritten(tmp_db):
    _seed_both_writers(tmp_db)
    conn = _conn()
    try:
        rows = _fetch_spans_for_group(conn, [PARENT, SDK_TRACE])
    finally:
        conn.close()

    by_id = {r['span_id']: r for r in rows}
    assert by_id['hook-tool']['attributes'] == {'tool_name': 'Read',
                                                'agent_id': AGENT}
    assert by_id['prompt-main']['attributes'] == {'text': 'the user goal'}


def test_subagent_fetch_returns_both_writers_halves(tmp_db):
    """The child's own view: selecting the roster id alone would show the hook's
    half of its work and silently drop the SDK's."""
    _seed_both_writers(tmp_db)
    _link_alias(tmp_db)
    conn = _conn()
    try:
        rows = _fetch_subagent_spans(conn, SA, None, None)
    finally:
        conn.close()

    assert [r['span_id'] for r in rows] == [
        f'subagent.start-{AGENT}', 'hook-tool', 'sdk-tool', 'sdk-resp',
    ]
    for r in rows:
        assert r['trace_id'] == SA
        assert 'agent_id' not in r
        assert 'agent_id' not in r['attributes']
    origins = {r['span_id']: r[ORIGIN_KEY] for r in rows}
    assert origins['hook-tool'] == 'hook'
    assert origins['sdk-tool'] == 'sdk'


def test_ordinary_trace_without_an_sdk_half_is_byte_identical(tmp_db):
    """Non-regression: with no marker link in the window the read is the same
    list of dicts it produced before normalization existed. A subagent marker
    is the one exception — it carries the serve-time rollup of its agent's
    spans — so it is compared on the stored keys alone."""
    _seed_parent_with_two_agents(tmp_db)
    conn = _conn()
    try:
        rows = _fetch_spans_for_group(conn, [PARENT])
        raw = conn.execute(
            "SELECT span_id, agent_id, attributes FROM session_spans "
            "WHERE trace_id = ? ORDER BY start_time ASC, id ASC",
            (PARENT,)).fetchall()
    finally:
        conn.close()

    assert [r['span_id'] for r in rows] == [r['span_id'] for r in raw]
    for row, stored in zip(rows, raw, strict=True):
        expected = json.loads(stored['attributes'])
        served = {k: v for k, v in row['attributes'].items()
                  if not k.startswith('agent_') or k in expected}
        assert served == expected
        assert 'agent_id' not in row


def test_two_agents_launch_links_do_not_cross_contaminate(tmp_db):
    _seed_both_writers(tmp_db)
    _insert_span(tmp_db, f'subagent.start-{OTHER}', 'subagent.start',
                 '2026-04-18T12:00:05', agent_id=OTHER,
                 attrs={'agent_id': OTHER, 'launch_tool_use_id': LAUNCH_OTHER})
    _insert_span(tmp_db, 'sdk-other-tool', 'tool.Grep', '2026-04-18T12:00:06',
                 agent_id=LAUNCH_OTHER, trace_id=SDK_TRACE,
                 attrs={'tool_name': 'Grep', 'agent_id': LAUNCH_OTHER})
    conn = _conn()
    try:
        rows = _fetch_spans_for_group(conn, [PARENT, SDK_TRACE])
    finally:
        conn.close()

    resolved = _agent_ids(rows)
    assert resolved['sdk-tool'] == AGENT
    assert resolved['sdk-resp'] == AGENT
    assert resolved['sdk-other-tool'] == OTHER


# ── per-agent rollup on the markers ──────────────────────────────────

def _marker_attrs(rows, span_id):
    return {r['span_id']: r for r in rows}[span_id]['attributes']


def _seed_agent_with_both_markers(db_path, *, result=None):
    """The ordinary shape: a start, three owned spans (two of them tools), and
    a stop. `result` is what the agent reported back on that stop."""
    _insert_span(db_path, 'prompt-main', 'prompt', '2026-04-18T12:00:00',
                 attrs={'text': 'the user goal'})
    _insert_span(db_path, f'subagent.start-{AGENT}', 'subagent.start',
                 '2026-04-18T12:00:01', agent_id=AGENT,
                 attrs={'agent_id': AGENT})
    _insert_span(db_path, 'toolA', 'tool.Read', '2026-04-18T12:00:02',
                 end_time='2026-04-18T12:00:03', agent_id=AGENT,
                 input_tokens=10, output_tokens=20, cost_usd=0.01,
                 attrs={'tool_name': 'Read', 'agent_id': AGENT})
    _insert_span(db_path, 'toolB', 'tool.Edit', '2026-04-18T12:00:04',
                 end_time='2026-04-18T12:00:04', agent_id=AGENT,
                 input_tokens=5, output_tokens=7, cost_usd=0.02,
                 attrs={'tool_name': 'Edit', 'agent_id': AGENT})
    _insert_span(db_path, 'resp1', 'assistant_response', '2026-04-18T12:00:05',
                 end_time='2026-04-18T12:00:08', agent_id=AGENT,
                 attrs={'agent_id': AGENT})
    _insert_span(db_path, f'subagent.stop-{AGENT}', 'subagent.stop',
                 '2026-04-18T12:00:09', agent_id=AGENT,
                 attrs={'agent_id': AGENT,
                        **({'result_preview': result} if result else {})})


def _stamp_stop_usage(db_path, span_id, *, cost, in_tok, out_tok):
    """What `claude_subagents._stamp_stop_marker` writes once it has totalled
    the subagent's own transcript."""
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute("UPDATE session_spans SET cost_usd = ?, input_tokens = ?, "
                     "output_tokens = ? WHERE span_id = ?",
                     (cost, in_tok, out_tok, span_id))
        conn.commit()
    finally:
        conn.close()


def test_both_writers_copies_of_one_call_are_counted_once(tmp_db):
    """The fetch stamps the markers BEFORE `merge_spans` runs, and on a
    regin-launched run both writers record the same call under different
    span_ids — only the cross-source pairing can tell they are one. Counted
    there, an agent's card claimed 45 spans and 32 tools for the 29 and 16 its
    own trace served, against a roster that said 29."""
    from lib.trace.trace_service import fetch_session_projection
    _link_alias(tmp_db)
    _insert_span(tmp_db, 'prompt-main', 'prompt', '2026-04-18T12:00:00',
                 attrs={'text': 'the user goal'})
    _insert_span(tmp_db, f'subagent.start-{AGENT}', 'subagent.start',
                 '2026-04-18T12:00:01', agent_id=AGENT,
                 attrs={'agent_id': AGENT, 'launch_tool_use_id': LAUNCH})
    # One Bash call, recorded by each writer under its own span_id. Both
    # carry the tool_use_id that names the call.
    _insert_span(tmp_db, 'hook-bash', 'tool.Bash', '2026-04-18T12:00:02',
                 end_time='2026-04-18T12:00:03', agent_id=AGENT,
                 attrs={'tool_name': 'Bash', 'agent_id': AGENT,
                        'tool_use_id': 'toolu_shared'})
    _insert_span(tmp_db, 'sdk-bash', 'tool.Bash', '2026-04-18T12:00:02',
                 end_time='2026-04-18T12:00:03', agent_id=LAUNCH,
                 trace_id=SDK_TRACE,
                 attrs={'tool_name': 'Bash', 'agent_id': LAUNCH,
                        'tool_use_id': 'toolu_shared'})
    _insert_span(tmp_db, f'subagent.stop-{AGENT}', 'subagent.stop',
                 '2026-04-18T12:00:09', agent_id=AGENT, attrs={'agent_id': AGENT})

    widened, _ = fetch_session_projection(PARENT, drop_delegated=True)
    attrs = _marker_attrs(widened, f'subagent.start-{AGENT}')
    assert attrs['agent_span_count'] == 1
    assert attrs['agent_tool_count'] == 1


def test_the_first_marker_carries_the_agents_rollup(tmp_db):
    """The summary lands on the agent's earliest marker: counts exclude the
    markers themselves. This agent's `subagent.stop` was never stamped with the
    transcript totals, so usage falls back to summing the agent's own spans."""
    _seed_agent_with_both_markers(tmp_db)
    conn = _conn()
    try:
        rows = _fetch_spans_for_group(conn, [PARENT])
    finally:
        conn.close()

    attrs = _marker_attrs(rows, f'subagent.start-{AGENT}')
    assert attrs['agent_span_count'] == 3
    assert attrs['agent_tool_count'] == 2
    assert attrs['agent_input_tokens'] == 15
    assert attrs['agent_output_tokens'] == 27
    assert attrs['agent_cost_usd'] == 0.03
    # Lifetime: start marker 12:00:01 → stop marker 12:00:09. The span
    # extent (6000) stops at the last row recorded and disagreed on screen
    # with the roster and the agent's own pane.
    assert attrs['agent_duration_ms'] == 8000
    assert attrs['agent_child_trace_id'] == SA


def test_a_later_marker_carries_the_link_but_no_totals(tmp_db):
    """Every whole-agent figure is on one marker, so nothing can be counted
    twice; the later marker keeps the address a card links to."""
    _seed_agent_with_both_markers(tmp_db)
    conn = _conn()
    try:
        rows = _fetch_spans_for_group(conn, [PARENT])
    finally:
        conn.close()

    attrs = _marker_attrs(rows, f'subagent.stop-{AGENT}')
    assert attrs['agent_child_trace_id'] == SA
    for key in ('agent_cost_usd', 'agent_input_tokens', 'agent_output_tokens',
                'agent_span_count', 'agent_tool_count', 'agent_duration_ms'):
        assert key not in attrs


def test_the_start_marker_carries_the_answer_the_stop_reported(tmp_db):
    """An ordinary Task subagent writes its result to `subagent.stop`, and the
    conversation card is rendered off `subagent.start` — until it is carried
    across, every such agent's answer is invisible in the feed."""
    _seed_agent_with_both_markers(tmp_db, result='found three bugs')
    rows = _parent_rows()
    assert _marker_attrs(
        rows, f'subagent.start-{AGENT}')['agent_result_preview'] == 'found three bugs'


def test_an_agent_that_reported_nothing_carries_no_answer(tmp_db):
    """Absent, not empty: the card omits its result panel entirely rather than
    rendering a blank one over an agent that is still running or said nothing."""
    _seed_agent_with_both_markers(tmp_db)
    assert 'agent_result_preview' not in _marker_attrs(
        _parent_rows(), f'subagent.start-{AGENT}')


def _answer_turn(db_path, text, *, span_id='resp2', at='2026-04-18T12:00:08'):
    """The agent's final assistant turn — what its stop marker reports a cut
    of. Ingested last, so it is the newest turn of the newest run."""
    _insert_span(db_path, span_id, 'assistant_response', at,
                 end_time=at, agent_id=AGENT,
                 attrs={'agent_id': AGENT, 'text': text})


def test_the_answer_is_the_final_message_not_its_flattened_cut(tmp_db):
    """`subagent.stop` reports a whitespace-flattened 200-char cut
    (`subagent_lifecycle._result_preview`), so a markdown report reaches the
    card as one line of source it cannot render. The turn itself is stored
    beside it, newlines intact."""
    _seed_agent_with_both_markers(tmp_db, result='## Verdict **1.** shipped')
    _answer_turn(tmp_db, '## Verdict\n\n**1.** shipped')
    assert _marker_attrs(_parent_rows(), f'subagent.start-{AGENT}')[
        'agent_result_preview'] == '## Verdict\n\n**1.** shipped'


def test_an_answer_past_the_cap_is_truncated(tmp_db):
    """The marker's copy is a reading copy — the message is served whole under
    the agent's own trace, and every copy rides the window payload twice."""
    from lib.trace.projection import _AGENT_RESULT_MAX_BYTES
    _seed_agent_with_both_markers(tmp_db, result='a long report')
    _answer_turn(tmp_db, 'x' * (_AGENT_RESULT_MAX_BYTES * 2))
    answer = _marker_attrs(_parent_rows(),
                           f'subagent.start-{AGENT}')['agent_result_preview']
    assert answer.endswith('…')
    assert len(answer.encode('utf-8')) <= _AGENT_RESULT_MAX_BYTES + 3


def _capped_answer(tmp_db, text):
    """The reading copy of `text` the start card carries, past the byte cap."""
    _seed_agent_with_both_markers(tmp_db, result='a long report')
    _answer_turn(tmp_db, text)
    return _marker_attrs(_parent_rows(),
                         f'subagent.start-{AGENT}')['agent_result_preview']


def _fence_lines(text):
    return sum(1 for line in text.split('\n')
               if re.match(r'^ {0,3}(?:```|~~~)', line))


def test_a_capped_answer_is_cut_at_a_line_boundary_not_mid_token(tmp_db):
    """CAI-219: the cap is a BYTE offset, so it landed inside an inline-code
    span and the card rendered `` `isTurnOpenByDefault(tur… `` — a backtick
    opened and never closed, with every construct after it bleeding into the
    rest of the report. Cut back to the last whole line instead."""
    from lib.trace.projection import _AGENT_RESULT_MAX_BYTES as cap
    head = 'x' * (cap - 24) + '\n'
    text = head + '- `isTurnOpenByDefault(turnId)` — a default, not a lock.\n'
    answer = _capped_answer(tmp_db, text)

    body = answer.rstrip('…').rstrip()
    assert text.startswith(body)
    assert body.count('`') % 2 == 0


def test_a_capped_answer_never_ends_inside_a_code_fence(tmp_db):
    """Same cut, worse blast radius: a fence left open swallows everything
    after it, so the whole tail of the card renders as source."""
    from lib.trace.projection import _AGENT_RESULT_MAX_BYTES as cap
    head = 'x' * (cap - 20) + '\n'
    text = head + '```python\nprint("the answer")\n```\ndone\n'
    answer = _capped_answer(tmp_db, text)

    body = answer.rstrip('…').rstrip()
    assert text.startswith(body)
    assert _fence_lines(body) % 2 == 0


def test_a_capped_answer_with_no_line_boundary_keeps_whole_words(tmp_db):
    """One over-long line has no block boundary to retreat to. A word boundary
    is the last thing left that does not split a token in half."""
    text = 'word ' * 5000
    answer = _capped_answer(tmp_db, text)

    body = answer.rstrip('…').rstrip()
    assert text.startswith(body)
    assert body.split()[-1] == 'word'


def test_a_running_agent_carries_no_answer_from_its_latest_turn(tmp_db):
    """Mid-run the newest assistant turn is a step, not an answer — nothing is
    claimed until a stop marker reports one."""
    _seed_agent_with_both_markers(tmp_db)
    _answer_turn(tmp_db, 'Now the composable — reading it next.')
    assert 'agent_result_preview' not in _marker_attrs(
        _parent_rows(), f'subagent.start-{AGENT}')


def test_the_stop_that_reported_the_answer_names_the_card_showing_it(tmp_db):
    """One answer, one card. The answer is rendered on the start card, and the
    stop row that reported it printed the same text again one line below —
    flattened source under a rendered copy of itself. The stop names the card
    that carries it so the feed can drop the duplicate row."""
    _seed_agent_with_both_markers(tmp_db, result='found three bugs')
    assert _marker_attrs(_parent_rows(), f'subagent.stop-{AGENT}')[
        'agent_result_carried_by'] == f'subagent.start-{AGENT}'


def test_a_stop_whose_answer_reached_no_card_is_not_named(tmp_db):
    """Conditional on the carry really happening: a window holding a stop
    without its start shows that answer nowhere else, and dropping the row
    would lose it."""
    from lib.trace.projection import _attach_agent_rollups
    window = [{'span_id': f'subagent.stop-{AGENT}', 'name': 'subagent.stop',
               'id': 1, 'start_time': '2026-04-18T12:00:09',
               'attributes': {'agent_id': AGENT,
                              'result_preview': 'found three bugs'}}]
    _attach_agent_rollups(window)
    assert 'agent_result_carried_by' not in window[0]['attributes']


def test_a_stop_that_reported_nothing_is_not_named(tmp_db):
    """Nothing was carried, so there is no duplicate to drop."""
    _seed_agent_with_both_markers(tmp_db)
    assert 'agent_result_carried_by' not in _marker_attrs(
        _parent_rows(), f'subagent.stop-{AGENT}')


# ── segments of a resumed agent ──────────────────────────────────────

def _seed_resumed_agent(db_path, *, with_turns=False):
    """A `SendMessage` continuation: the same agent starts and stops twice.
    Two spans in the first run, one in the second. `with_turns` adds what each
    run was asked (an agent-scoped prompt per turn) and what it answered."""
    _insert_span(db_path, 'prompt-main', 'prompt', '2026-04-18T12:00:00',
                 attrs={'text': 'the user goal'})
    _insert_span(db_path, f'subagent.start-{AGENT}-1', 'subagent.start',
                 '2026-04-18T12:00:01', agent_id=AGENT, attrs={'agent_id': AGENT})
    if with_turns:
        _insert_span(db_path, f'prompt-sa-{AGENT}', 'prompt',
                     '2026-04-18T12:00:01', agent_id=AGENT,
                     attrs={'agent_id': AGENT, 'text': 'verify the build'})
    _insert_span(db_path, 'toolA', 'tool.Read', '2026-04-18T12:00:02',
                 end_time='2026-04-18T12:00:03', agent_id=AGENT,
                 attrs={'tool_name': 'Read', 'agent_id': AGENT})
    _insert_span(db_path, 'resp1', 'assistant_response', '2026-04-18T12:00:04',
                 end_time='2026-04-18T12:00:05', agent_id=AGENT,
                 attrs={'agent_id': AGENT,
                        **({'text': '## Run one\n\nfound three'} if with_turns else {})})
    _insert_span(db_path, f'subagent.stop-{AGENT}-1', 'subagent.stop',
                 '2026-04-18T12:00:06', agent_id=AGENT,
                 attrs={'agent_id': AGENT,
                        **({'result_preview': 'found three'} if with_turns else {})})
    _insert_span(db_path, f'subagent.start-{AGENT}-2', 'subagent.start',
                 '2026-04-18T12:00:20', agent_id=AGENT, attrs={'agent_id': AGENT})
    if with_turns:
        _insert_span(db_path, f'prompt-sa-{AGENT}-u-second', 'prompt',
                     '2026-04-18T12:00:20', agent_id=AGENT,
                     attrs={'agent_id': AGENT,
                            'text': 'the coordinator says: re-verify'})
    _insert_span(db_path, 'toolB', 'tool.Edit', '2026-04-18T12:00:21',
                 end_time='2026-04-18T12:00:23', agent_id=AGENT,
                 attrs={'tool_name': 'Edit', 'agent_id': AGENT})
    _insert_span(db_path, f'subagent.stop-{AGENT}-2', 'subagent.stop',
                 '2026-04-18T12:00:24', agent_id=AGENT,
                 attrs={'agent_id': AGENT,
                        **({'result_preview': 'all fixed'} if with_turns else {})})


def _parent_rows():
    conn = _conn()
    try:
        return _fetch_spans_for_group(conn, [PARENT])
    finally:
        conn.close()


_SEGMENT_KEYS = ('agent_segment_index', 'agent_segment_count',
                 'agent_segment_span_count', 'agent_segment_tool_count',
                 'agent_segment_duration_ms')


def test_an_agent_that_ran_once_gets_no_segment_keys(tmp_db):
    """Nothing to tell apart, so nothing is stamped — a single-segment
    payload is what it was before segments existed."""
    _seed_agent_with_both_markers(tmp_db)
    attrs = _marker_attrs(_parent_rows(), f'subagent.start-{AGENT}')
    for key in _SEGMENT_KEYS:
        assert key not in attrs


def test_each_run_of_a_resumed_agent_names_its_position(tmp_db):
    """The card's only discriminator: every per-agent source (the roster
    entry, the child trace, the folded launch) is keyed on the agent alone,
    so without this the two runs render identically."""
    _seed_resumed_agent(tmp_db)
    rows = _parent_rows()
    first = _marker_attrs(rows, f'subagent.start-{AGENT}-1')
    second = _marker_attrs(rows, f'subagent.start-{AGENT}-2')
    assert (first['agent_segment_index'], first['agent_segment_count']) == (0, 2)
    assert (second['agent_segment_index'], second['agent_segment_count']) == (1, 2)


def test_a_runs_shape_covers_only_its_own_segment(tmp_db):
    """Ownership is per segment — the latest start ingested at or before the
    span — the same rule `_reparent_subagents` nests on."""
    _seed_resumed_agent(tmp_db)
    rows = _parent_rows()
    first = _marker_attrs(rows, f'subagent.start-{AGENT}-1')
    second = _marker_attrs(rows, f'subagent.start-{AGENT}-2')
    assert (first['agent_segment_span_count'], first['agent_segment_tool_count']) == (2, 1)
    assert (second['agent_segment_span_count'], second['agent_segment_tool_count']) == (1, 1)
    # Each run's own lifetime, start marker to its stop marker.
    assert first['agent_segment_duration_ms'] == 5000
    assert second['agent_segment_duration_ms'] == 4000


def test_the_segments_sum_to_the_whole_agent(tmp_db):
    """Per-segment figures are safe to repeat across markers precisely because
    they add up to the agent instead of restating it — the trap the
    whole-agent totals avoid by living on the first marker alone."""
    _seed_resumed_agent(tmp_db)
    rows = _parent_rows()
    starts = [r for r in rows if r['name'] == 'subagent.start']
    total = sum(r['attributes'].get('agent_segment_span_count', 0) for r in starts)
    assert total == _marker_attrs(rows, f'subagent.start-{AGENT}-1')['agent_span_count']


def test_a_resumed_run_still_carries_no_whole_agent_total(tmp_db):
    """Segments must not re-open the double-count the one-marker rule closed."""
    _seed_resumed_agent(tmp_db)
    attrs = _marker_attrs(_parent_rows(), f'subagent.start-{AGENT}-2')
    for key in ('agent_span_count', 'agent_tool_count', 'agent_duration_ms',
                'agent_cost_usd', 'agent_input_tokens', 'agent_output_tokens'):
        assert key not in attrs


def test_each_run_carries_the_turn_it_was_asked(tmp_db):
    """The launch prompt describes the FIRST run only; a resumed one is asked
    by a `SendMessage` turn that lands in its own segment."""
    _seed_resumed_agent(tmp_db, with_turns=True)
    rows = _parent_rows()
    first = _marker_attrs(rows, f'subagent.start-{AGENT}-1')
    second = _marker_attrs(rows, f'subagent.start-{AGENT}-2')
    assert first['agent_segment_prompt'] == 'verify the build'
    assert second['agent_segment_prompt'] == 'the coordinator says: re-verify'


def test_each_run_carries_the_result_it_reported(tmp_db):
    """Every working period ends in its own stop marker; the card used to read
    the result off the START marker, where there has never been one.

    Run 1 answered in an assistant turn of its own, so its card gets that turn
    verbatim; run 2 has none in the window and falls back to what its stop
    reported."""
    _seed_resumed_agent(tmp_db, with_turns=True)
    rows = _parent_rows()
    assert _marker_attrs(
        rows, f'subagent.start-{AGENT}-1')['agent_segment_result'] == (
            '## Run one\n\nfound three')
    assert _marker_attrs(
        rows, f'subagent.start-{AGENT}-2')['agent_segment_result'] == 'all fixed'


def test_each_run_of_a_resumed_agent_names_its_own_stop(tmp_db):
    """Per-run, like the answers themselves: run 1's stop points at run 1's
    card, so neither row is dropped on the strength of the other's carry."""
    _seed_resumed_agent(tmp_db, with_turns=True)
    rows = _parent_rows()
    assert _marker_attrs(rows, f'subagent.stop-{AGENT}-1')[
        'agent_result_carried_by'] == f'subagent.start-{AGENT}-1'
    assert _marker_attrs(rows, f'subagent.stop-{AGENT}-2')[
        'agent_result_carried_by'] == f'subagent.start-{AGENT}-2'


def test_a_resumed_agent_carries_no_whole_agent_answer(tmp_db):
    """Each run answered separately, so the per-run key owns the text; one
    whole-agent result repeated across the cards would attribute the LAST
    run's answer to every earlier one."""
    _seed_resumed_agent(tmp_db, with_turns=True)
    rows = _parent_rows()
    for suffix in (1, 2):
        assert 'agent_result_preview' not in _marker_attrs(
            rows, f'subagent.start-{AGENT}-{suffix}')


def test_a_window_holding_one_run_never_borrows_an_earlier_runs_answer(tmp_db):
    """`len(starts)` counts the LOADED window, so a page that holds a resumed
    agent's SECOND start beside its FIRST run's stop reads as single-run — and
    would hand run 2's card the answer run 1 gave. Missing information is
    recoverable; wrong information is not."""
    from lib.trace.projection import _attach_agent_rollups
    _seed_resumed_agent(tmp_db, with_turns=True)
    conn = sqlite3.connect(str(tmp_db))
    try:
        ids = dict(conn.execute(
            "SELECT span_id, id FROM session_spans WHERE span_id IN (?, ?)",
            (f'subagent.stop-{AGENT}-1', f'subagent.start-{AGENT}-2')).fetchall())
    finally:
        conn.close()
    window = [
        {'span_id': f'subagent.stop-{AGENT}-1', 'name': 'subagent.stop',
         'id': ids[f'subagent.stop-{AGENT}-1'],
         'attributes': {'agent_id': AGENT, 'result_preview': 'found three'}},
        {'span_id': f'subagent.start-{AGENT}-2', 'name': 'subagent.start',
         'id': ids[f'subagent.start-{AGENT}-2'], 'attributes': {'agent_id': AGENT}},
    ]
    _attach_agent_rollups(window)
    assert 'agent_result_preview' not in window[1]['attributes']


def test_a_window_holding_one_run_never_quotes_an_earlier_runs_turn(tmp_db):
    """The id-contiguous page of the guard above, one step further: it also
    holds run 1's assistant turn. The turn is the agent's newest before the
    stop, so an unbounded read would print run 1's answer on run 2's card."""
    from lib.trace.projection import _attach_agent_rollups
    _seed_resumed_agent(tmp_db, with_turns=True)
    conn = sqlite3.connect(str(tmp_db))
    try:
        ids = dict(conn.execute(
            "SELECT span_id, id FROM session_spans WHERE span_id IN (?, ?, ?)",
            ('resp1', f'subagent.start-{AGENT}-2',
             f'subagent.stop-{AGENT}-2')).fetchall())
    finally:
        conn.close()
    window = [
        {'span_id': 'resp1', 'name': 'assistant_response', 'id': ids['resp1'],
         'start_time': '2026-04-18T12:00:04',
         'attributes': {'agent_id': AGENT, 'text': '## Run one\n\nfound three'}},
        {'span_id': f'subagent.start-{AGENT}-2', 'name': 'subagent.start',
         'id': ids[f'subagent.start-{AGENT}-2'],
         'attributes': {'agent_id': AGENT}},
        {'span_id': f'subagent.stop-{AGENT}-2', 'name': 'subagent.stop',
         'id': ids[f'subagent.stop-{AGENT}-2'],
         'attributes': {'agent_id': AGENT, 'result_preview': 'all fixed'}},
    ]
    _attach_agent_rollups(window)
    assert window[1]['attributes']['agent_result_preview'] == 'all fixed'


def test_a_pending_turn_is_not_quoted_as_the_answer(tmp_db):
    """A PENDING row is a placeholder beside its resolved twin; alone it holds
    a partial turn, and a closed card would quote half an answer."""
    _seed_agent_with_both_markers(tmp_db, result='the reported answer')
    _insert_span(tmp_db, 'resp2', 'assistant_response', '2026-04-18T12:00:08',
                 agent_id=AGENT, attrs={'agent_id': AGENT, 'text': 'half a th'})
    conn = sqlite3.connect(str(tmp_db))
    try:
        conn.execute("UPDATE session_spans SET status_code = 'PENDING' "
                     "WHERE span_id = 'resp2'")
        conn.commit()
    finally:
        conn.close()
    assert _marker_attrs(_parent_rows(), f'subagent.start-{AGENT}')[
        'agent_result_preview'] == 'the reported answer'


def test_a_run_that_said_nothing_carries_no_prompt_or_result(tmp_db):
    _seed_resumed_agent(tmp_db)
    attrs = _marker_attrs(_parent_rows(), f'subagent.start-{AGENT}-2')
    assert 'agent_segment_prompt' not in attrs
    assert 'agent_segment_result' not in attrs


def test_a_run_with_nothing_finished_yet_reports_no_shape(tmp_db):
    """The state a resumed agent is in for its first seconds. `0 spans` on the
    card would say the run did nothing rather than that it is still going, so
    the figures are omitted and the position is all that is stamped."""
    _seed_resumed_agent(tmp_db)
    conn = sqlite3.connect(str(tmp_db))
    try:
        conn.execute("UPDATE session_spans SET status_code = 'PENDING' "
                     "WHERE span_id = 'toolB'")
        conn.commit()
    finally:
        conn.close()
    attrs = _marker_attrs(_parent_rows(), f'subagent.start-{AGENT}-2')
    assert attrs['agent_segment_index'] == 1
    for key in ('agent_segment_span_count', 'agent_segment_tool_count',
                'agent_segment_duration_ms'):
        assert key not in attrs


def test_the_runs_are_numbered_in_reading_order_not_ingest_order(tmp_db):
    """A marker a repair pass synthesized carries a late DB id with an early
    timestamp. Numbering by id there labels the first card on screen `run 2 of
    2` and hands the live roster line to the older run — the mis-attribution
    the segment split exists to remove."""
    _seed_resumed_agent(tmp_db)
    conn = sqlite3.connect(str(tmp_db))
    try:
        # The chronologically FIRST start, re-ingested last.
        conn.execute("UPDATE session_spans SET id = 9999 WHERE span_id = ?",
                     (f'subagent.start-{AGENT}-1',))
        conn.commit()
    finally:
        conn.close()
    rows = _parent_rows()
    first = _marker_attrs(rows, f'subagent.start-{AGENT}-1')
    second = _marker_attrs(rows, f'subagent.start-{AGENT}-2')
    assert first['agent_segment_index'] == 0
    assert second['agent_segment_index'] == 1


def test_a_stop_marker_names_no_segment(tmp_db):
    """Only a start opens a run, so only a start can say which one it is."""
    _seed_resumed_agent(tmp_db)
    attrs = _marker_attrs(_parent_rows(), f'subagent.stop-{AGENT}-2')
    for key in _SEGMENT_KEYS:
        assert key not in attrs


def test_the_stop_markers_stamped_usage_outranks_the_span_sum(tmp_db):
    """The agent's real spend lives on its stop marker (`claude_subagents.
    _stamp_stop_marker` totals the subagent's transcript there, and the bill
    reads that row). Its spans hold fragments — 27 output tokens against the
    marker's 12,926 — so summing them reports a $3.51 agent as nearly free."""
    _seed_agent_with_both_markers(tmp_db)
    _stamp_stop_usage(tmp_db, f'subagent.stop-{AGENT}',
                      cost=3.5121615, in_tok=898, out_tok=12926)
    conn = _conn()
    try:
        rows = _fetch_spans_for_group(conn, [PARENT])
    finally:
        conn.close()

    attrs = _marker_attrs(rows, f'subagent.start-{AGENT}')
    assert attrs['agent_cost_usd'] == 3.5121615
    assert attrs['agent_input_tokens'] == 898
    assert attrs['agent_output_tokens'] == 12926
    assert attrs['agent_span_count'] == 3
    assert attrs['agent_tool_count'] == 2
    # Lifetime: start marker 12:00:01 → stop marker 12:00:09. The span
    # extent (6000) stops at the last row recorded and disagreed on screen
    # with the roster and the agent's own pane.
    assert attrs['agent_duration_ms'] == 8000
    assert 'agent_cost_usd' not in _marker_attrs(rows, f'subagent.stop-{AGENT}')


def test_a_stamped_zero_cost_still_outranks_the_span_sum(tmp_db):
    """A marker stamped 0.0 is a measured answer, not a missing one."""
    _seed_agent_with_both_markers(tmp_db)
    _stamp_stop_usage(tmp_db, f'subagent.stop-{AGENT}',
                      cost=0.0, in_tok=0, out_tok=0)
    conn = _conn()
    try:
        rows = _fetch_spans_for_group(conn, [PARENT])
    finally:
        conn.close()

    attrs = _marker_attrs(rows, f'subagent.start-{AGENT}')
    assert attrs['agent_cost_usd'] == 0.0
    assert attrs['agent_output_tokens'] == 0


def test_summing_every_marker_equals_the_stored_bill(tmp_db):
    """The invariant a consumer gets for free: half the agents in the store are
    resumed and carry several markers, so a per-marker card summed over the
    trace must still total the stored spend — not a multiple of it."""
    for i in range(3):
        _insert_span(tmp_db, f'start-{i}', 'subagent.start',
                     f'2026-04-18T12:0{i}:00', agent_id=AGENT,
                     attrs={'agent_id': AGENT})
        _insert_span(tmp_db, f'seg-tool-{i}', 'tool.Read',
                     f'2026-04-18T12:0{i}:01', agent_id=AGENT,
                     attrs={'agent_id': AGENT})
    for i in range(2):
        _insert_span(tmp_db, f'stop-{i}', 'subagent.stop',
                     f'2026-04-18T12:0{i}:30', agent_id=AGENT,
                     attrs={'agent_id': AGENT})
        _stamp_stop_usage(tmp_db, f'stop-{i}', cost=1.25, in_tok=10, out_tok=100)
    conn = _conn()
    try:
        rows = _fetch_spans_for_group(conn, [PARENT])
        stored = conn.execute(
            "SELECT SUM(cost_usd) s FROM session_spans "
            "WHERE trace_id = ? AND name = 'subagent.stop'",
            (PARENT,)).fetchone()['s']
    finally:
        conn.close()

    markers = [r for r in rows
               if r['name'] in ('subagent.start', 'subagent.stop')]
    assert len(markers) == 5
    served = sum(r['attributes'].get('agent_cost_usd', 0) for r in markers)
    assert served == stored == 2.5
    assert all(r['attributes']['agent_child_trace_id'] == SA for r in markers)
    assert sum(r['attributes'].get('agent_span_count', 0) for r in markers) == 3


def test_the_childs_own_markers_never_reach_its_own_read(tmp_db):
    """Both of them: a start and a stop describing THIS agent are the parent's
    account of it, and served here they render the agent inside itself."""
    _seed_agent_with_both_markers(tmp_db)
    conn = _conn()
    try:
        rows = _fetch_spans_for_group(conn, SA)
    finally:
        conn.close()

    assert [r['span_id'] for r in rows] == [
        f'subagent.start-{AGENT}', 'toolA', 'toolB', 'resp1',
    ]
    assert not [r for r in rows if r['name'] == 'subagent.stop']
    root = {r['span_id']: r for r in rows}[f'subagent.start-{AGENT}']
    assert root['name'] == 'prompt'
    assert 'agent_span_count' not in root['attributes']


def test_an_existing_child_trace_id_is_not_overwritten(tmp_db):
    _insert_span(tmp_db, f'subagent.start-{AGENT}', 'subagent.start',
                 '2026-04-18T12:00:01', agent_id=AGENT,
                 attrs={'agent_id': AGENT, 'child_trace_id': 'sa-stamped'})
    _insert_span(tmp_db, 'toolA', 'tool.Read', '2026-04-18T12:00:02',
                 agent_id=AGENT, attrs={'agent_id': AGENT})
    conn = _conn()
    try:
        rows = _fetch_spans_for_group(conn, [PARENT])
    finally:
        conn.close()

    attrs = _marker_attrs(rows, f'subagent.start-{AGENT}')
    assert attrs['child_trace_id'] == 'sa-stamped'
    assert attrs['agent_child_trace_id'] == 'sa-stamped'


def test_workflow_shaped_agent_rolls_up_without_a_stop_or_a_cost(tmp_db):
    """1,555 workflow agents have no `subagent.stop` at all and not one cost
    row, so the summary has to come off the spans — and must not invent a cost
    the store never recorded."""
    _insert_span(tmp_db, f'subagent.start-{AGENT}', 'subagent.start',
                 '2026-04-18T12:00:01', agent_id=AGENT,
                 attrs={'agent_id': AGENT})
    _insert_span(tmp_db, 'wf-tool', 'tool.Bash', '2026-04-18T12:00:02',
                 end_time='2026-04-18T12:00:02', agent_id=AGENT,
                 output_tokens=120, attrs={'agent_id': AGENT})
    _insert_span(tmp_db, 'wf-resp', 'assistant_response', '2026-04-18T12:00:03',
                 end_time='2026-04-18T12:00:04', agent_id=AGENT,
                 output_tokens=80, attrs={'agent_id': AGENT})
    conn = _conn()
    try:
        rows = _fetch_spans_for_group(conn, [PARENT])
    finally:
        conn.close()

    attrs = _marker_attrs(rows, f'subagent.start-{AGENT}')
    assert attrs['agent_span_count'] == 2
    assert attrs['agent_tool_count'] == 1
    assert attrs['agent_output_tokens'] == 200
    assert 'agent_cost_usd' not in attrs
    assert 'agent_input_tokens' not in attrs
    assert attrs['agent_duration_ms'] == 2000


def test_a_real_zero_cost_survives_the_omission_rule(tmp_db):
    """A recorded 0.0 is an answer; no cost row at all is not."""
    _insert_span(tmp_db, f'subagent.start-{AGENT}', 'subagent.start',
                 '2026-04-18T12:00:01', agent_id=AGENT,
                 attrs={'agent_id': AGENT})
    _insert_span(tmp_db, 'free-tool', 'tool.Read', '2026-04-18T12:00:02',
                 agent_id=AGENT, cost_usd=0.0, attrs={'agent_id': AGENT})
    conn = _conn()
    try:
        rows = _fetch_spans_for_group(conn, [PARENT])
    finally:
        conn.close()

    assert _marker_attrs(rows, f'subagent.start-{AGENT}')['agent_cost_usd'] == 0.0


def test_two_agents_rollups_do_not_cross_contaminate(tmp_db):
    _insert_span(tmp_db, f'subagent.start-{AGENT}', 'subagent.start',
                 '2026-04-18T12:00:01', agent_id=AGENT,
                 attrs={'agent_id': AGENT})
    _insert_span(tmp_db, 'toolA', 'tool.Read', '2026-04-18T12:00:02',
                 agent_id=AGENT, output_tokens=10, attrs={'agent_id': AGENT})
    _insert_span(tmp_db, f'subagent.start-{OTHER}', 'subagent.start',
                 '2026-04-18T12:00:03', agent_id=OTHER,
                 attrs={'agent_id': OTHER})
    _insert_span(tmp_db, 'toolC', 'tool.Grep', '2026-04-18T12:00:04',
                 agent_id=OTHER, output_tokens=5, attrs={'agent_id': OTHER})
    _insert_span(tmp_db, 'toolD', 'tool.Bash', '2026-04-18T12:00:05',
                 agent_id=OTHER, output_tokens=5, attrs={'agent_id': OTHER})
    conn = _conn()
    try:
        rows = _fetch_spans_for_group(conn, [PARENT])
    finally:
        conn.close()

    mine = _marker_attrs(rows, f'subagent.start-{AGENT}')
    theirs = _marker_attrs(rows, f'subagent.start-{OTHER}')
    assert (mine['agent_span_count'], mine['agent_output_tokens']) == (1, 10)
    assert (theirs['agent_span_count'], theirs['agent_output_tokens']) == (2, 10)
    assert mine['agent_child_trace_id'] == SA
    assert theirs['agent_child_trace_id'] == f'sa-{OTHER}'


def test_rollup_counts_sdk_written_rows_under_the_roster_agent(tmp_db):
    """The rollup runs AFTER `_normalize_agent_ids`: a row the runner tagged
    with the launching call's id is the roster agent's work, not nobody's."""
    _seed_both_writers(tmp_db)
    conn = _conn()
    try:
        rows = _fetch_spans_for_group(conn, [PARENT, SDK_TRACE])
    finally:
        conn.close()

    attrs = _marker_attrs(rows, f'subagent.start-{AGENT}')
    assert attrs['agent_span_count'] == 3
    assert attrs['agent_tool_count'] == 2


def test_duration_is_omitted_when_a_bound_is_missing(tmp_db):
    """No span ever recorded an end, so the extent is unknown — not zero, and
    not a negative width."""
    _insert_span(tmp_db, f'subagent.start-{AGENT}', 'subagent.start',
                 '2026-04-18T12:00:01', agent_id=AGENT,
                 attrs={'agent_id': AGENT})
    _insert_span(tmp_db, 'toolA', 'tool.Read', '2026-04-18T12:00:02',
                 agent_id=AGENT, attrs={'agent_id': AGENT})
    conn = _conn()
    try:
        rows = _fetch_spans_for_group(conn, [PARENT])
    finally:
        conn.close()

    attrs = _marker_attrs(rows, f'subagent.start-{AGENT}')
    assert 'agent_duration_ms' not in attrs
    assert attrs['agent_span_count'] == 1


# ── an agent this agent launched ─────────────────────────────────────

NESTED = 'agentZ'
SA_NESTED = f'sa-{NESTED}'


def _seed_nested_agent(db_path):
    """`AGENT` delegates to `NESTED`, the shape `meta.json` records as
    `spawnDepth: 2`: the inner agent's rows are tagged with ITS id and its start
    marker names the outer one as its parent."""
    _insert_span(db_path, 'prompt-main', 'prompt', '2026-04-18T12:00:00',
                 attrs={'text': 'the user goal'})
    _insert_span(db_path, f'subagent.start-{AGENT}', 'subagent.start',
                 '2026-04-18T12:00:01', agent_id=AGENT,
                 attrs={'agent_id': AGENT})
    _insert_span(db_path, 'toolA', 'tool.Read', '2026-04-18T12:00:02',
                 agent_id=AGENT, attrs={'agent_id': AGENT})
    _insert_span(db_path, 'toolAgent', 'tool.Agent', '2026-04-18T12:00:03',
                 agent_id=AGENT, attrs={'agent_id': AGENT})
    _insert_span(db_path, f'subagent.start-{NESTED}', 'subagent.start',
                 '2026-04-18T12:00:04', agent_id=NESTED,
                 attrs={'agent_id': NESTED, 'parent_agent_id': AGENT})
    _insert_span(db_path, 'nested-tool', 'tool.Bash', '2026-04-18T12:00:05',
                 end_time='2026-04-18T12:00:06', agent_id=NESTED,
                 output_tokens=42, attrs={'agent_id': NESTED})
    _insert_span(db_path, 'nested-resp', 'assistant_response',
                 '2026-04-18T12:00:07', end_time='2026-04-18T12:00:08',
                 agent_id=NESTED, attrs={'agent_id': NESTED})
    _insert_span(db_path, f'subagent.stop-{NESTED}', 'subagent.stop',
                 '2026-04-18T12:00:09', agent_id=NESTED,
                 attrs={'agent_id': NESTED, 'parent_agent_id': AGENT})


def test_a_nested_agents_card_survives_in_its_launchers_trace(tmp_db):
    """A subagent this agent launched is ITS delegated work: in the child it
    behaves exactly as a subagent does in a top-level session — a card carrying
    the rollup and the address of its own trace."""
    from lib.trace.trace_service import fetch_session_projection
    _seed_nested_agent(tmp_db)

    widened, _tree = fetch_session_projection(SA, drop_delegated=True)
    by_id = {s['span_id']: s for s in widened}

    marker = by_id[f'subagent.start-{NESTED}']
    assert marker['name'] == 'subagent.start'
    assert marker['attributes']['agent_id'] == NESTED
    assert marker['attributes']['agent_child_trace_id'] == SA_NESTED
    assert marker['attributes']['agent_span_count'] == 2
    assert marker['attributes']['agent_tool_count'] == 1
    assert marker['attributes']['agent_output_tokens'] == 42
    assert f'subagent.stop-{NESTED}' in by_id


def test_a_nested_agents_work_is_withheld_like_any_delegated_work(tmp_db):
    """Its spans are served by the trace its card links to, not inline here —
    the same split the parent makes for this agent."""
    from lib.trace.trace_service import fetch_session_projection
    _seed_nested_agent(tmp_db)

    widened, tree = fetch_session_projection(SA, drop_delegated=True)
    placed = _flatten(tree)

    assert {s['span_id'] for s in widened} == {
        f'subagent.start-{AGENT}', 'toolA', 'toolAgent',
        f'subagent.start-{NESTED}', f'subagent.stop-{NESTED}',
    }
    assert 'nested-tool' not in placed
    assert 'nested-resp' not in placed
    assert len(tree) == 1


def test_the_nested_agents_own_trace_drops_its_own_markers(tmp_db):
    """Depth is not special: the inner agent's trace is a child like any
    other."""
    from lib.trace.trace_service import fetch_session_projection
    _seed_nested_agent(tmp_db)

    widened, tree = fetch_session_projection(SA_NESTED, drop_delegated=True)

    assert not [s for s in widened if s['name'].startswith('subagent.')]
    assert {s['span_id'] for s in widened} == {
        f'subagent.start-{NESTED}', 'nested-tool', 'nested-resp',
    }
    assert len(tree) == 1


def test_the_parent_trace_sees_both_agents_unchanged(tmp_db):
    """Non-regression on the run that launched them: the nested agent is a card
    there too, and neither agent's work renders inline."""
    from lib.trace.trace_service import fetch_session_projection
    _seed_nested_agent(tmp_db)

    widened, _tree = fetch_session_projection(PARENT, drop_delegated=True)

    assert {s['span_id'] for s in widened} == {
        'prompt-main', f'subagent.start-{AGENT}',
        f'subagent.start-{NESTED}', f'subagent.stop-{NESTED}',
    }


def test_a_self_naming_parent_edge_cannot_readmit_own_markers(tmp_db):
    """An agent naming itself as its own parent must not pull its markers back
    in through the delegated-agent door."""
    from lib.trace.trace_service import fetch_session_projection
    _insert_span(tmp_db, f'subagent.start-{AGENT}', 'subagent.start',
                 '2026-04-18T12:00:01', agent_id=AGENT,
                 attrs={'agent_id': AGENT, 'parent_agent_id': AGENT})
    _insert_span(tmp_db, 'toolA', 'tool.Read', '2026-04-18T12:00:02',
                 agent_id=AGENT, attrs={'agent_id': AGENT})

    widened, tree = fetch_session_projection(SA, drop_delegated=True)

    assert not [s for s in widened if s['name'].startswith('subagent.')]
    assert len(tree) == 1


def test_a_trace_without_subagents_is_untouched(tmp_db):
    """Non-regression: no marker in the window, no key added to any row."""
    _insert_span(tmp_db, 'prompt-main', 'prompt', '2026-04-18T12:00:00',
                 attrs={'text': 'the user goal'})
    _insert_span(tmp_db, 'toolA', 'tool.Read', '2026-04-18T12:00:01',
                 attrs={'tool_name': 'Read'})
    conn = _conn()
    try:
        rows = _fetch_spans_for_group(conn, [PARENT])
        raw = conn.execute(
            "SELECT span_id, attributes FROM session_spans "
            "WHERE trace_id = ? ORDER BY start_time ASC, id ASC",
            (PARENT,)).fetchall()
    finally:
        conn.close()

    for row, stored in zip(rows, raw, strict=True):
        assert row['attributes'] == json.loads(stored['attributes'])
