"""The HTTP surface of a subagent addressed as its own trace (`sa-<agent_id>`).

None of a subagent's rows are STORED under that id — they live in the parent's
trace tagged `session_spans.agent_id` — so every reader that scoped itself with
`trace_id IN (…)` answered for the child with silence: an empty map, a
`span_count_total` of 0, a blank header, a 404 on each span body. The resolution
happens on the way in (`lib/trace/subagent_trace.py`), which is only visible
from outside; these go through the real app for that reason.

Two agents are seeded under one parent, one finished and one still running, so
"the child's spans" is an assertion with teeth: everything the child serves must
belong to it and not to its sibling or to the run's main loop.
"""

from __future__ import annotations

import json
import sqlite3

import pytest

PARENT = 'parent-1111-2222-3333'
AGENT_A = 'a1111111111111111'
AGENT_B = 'b2222222222222222'
SA_A = f'sa-{AGENT_A}'
SA_B = f'sa-{AGENT_B}'

# Agent A's own rows: launch marker, launch prompt, two spans, stop marker.
AGENT_A_SPANS = {
    'A-start', 'prompt-sa-' + AGENT_A, 'A-bash', 'A-resp', 'A-stop'}
MAIN_SPANS = {'p-main', 'm-bash'}
AGENT_B_SPANS = {'B-start', 'prompt-sa-' + AGENT_B, 'B-bash'}
# What the child's own trace SERVES: its rows minus the markers that describe
# it, which belong to the parent's view of it and render it inside itself.
AGENT_A_SERVED = AGENT_A_SPANS - {'A-start', 'A-stop'}
AGENT_B_SERVED = AGENT_B_SPANS - {'B-start'}


def _span(conn, span_id, name, start, *, end=None, agent_id=None, attrs=None,
          parent_id=None, input_tokens=None, output_tokens=None,
          trace_id=PARENT):
    conn.execute(
        """INSERT INTO session_spans
           (trace_id, span_id, parent_id, name, kind, start_time, end_time,
            attributes, status_code, agent_id, input_tokens, output_tokens)
           VALUES (?, ?, ?, ?, 'internal', ?, ?, ?, 'OK', ?, ?, ?)""",
        (trace_id, span_id, parent_id, name, start, end,
         json.dumps(attrs or {}), agent_id, input_tokens, output_tokens))


@pytest.fixture
def seeded(tmp_db):
    """One parent run: a main-loop turn, a finished agent, a running one."""
    conn = sqlite3.connect(str(tmp_db))
    try:
        conn.execute(
            """INSERT INTO sessions (trace_id, started_at, last_seen, origin,
                                     is_test, title, span_count, model, status,
                                     cwd)
               VALUES (?, '2026-08-01T10:00:00', '2026-08-01T10:05:00',
                       'session', 0, 'the main prompt', 10, 'claude-opus-5',
                       'active', '/repo')""",
            (PARENT,))
        _span(conn, 'p-main', 'prompt', '2026-08-01T10:00:00',
              attrs={'text': 'the main prompt'})
        _span(conn, 'm-bash', 'tool.Bash', '2026-08-01T10:00:05',
              attrs={'command': 'ls'})

        a = {'agent_id': AGENT_A, 'agent_type': 'goal-builder'}
        # The launch prompt precedes the marker by the fraction of a second the
        # store really records, which is what makes it the child's root.
        _span(conn, f'prompt-sa-{AGENT_A}', 'prompt', '2026-08-01T10:01:00',
              agent_id=AGENT_A, attrs={**a, 'text': 'build it'})
        _span(conn, 'A-start', 'subagent.start', '2026-08-01T10:01:00.500',
              agent_id=AGENT_A, attrs={**a, 'description': 'Build the thing'})
        _span(conn, 'A-bash', 'tool.Bash', '2026-08-01T10:01:05',
              agent_id=AGENT_A, attrs={**a, 'command': 'pytest -q'})
        _span(conn, 'A-resp', 'assistant_response', '2026-08-01T10:01:10',
              agent_id=AGENT_A, attrs={**a, 'text': 'built'})
        _span(conn, 'A-stop', 'subagent.stop', '2026-08-01T10:02:00',
              end='2026-08-01T10:02:00', agent_id=AGENT_A,
              attrs={**a, 'result_preview': 'built'},
              input_tokens=1000, output_tokens=200)

        b = {'agent_id': AGENT_B, 'agent_type': 'goal-verifier'}
        _span(conn, 'B-start', 'subagent.start', '2026-08-01T10:03:00',
              agent_id=AGENT_B, attrs=dict(b))
        _span(conn, f'prompt-sa-{AGENT_B}', 'prompt', '2026-08-01T10:03:00',
              agent_id=AGENT_B, attrs={**b, 'text': 'verify it'})
        _span(conn, 'B-bash', 'tool.Bash', '2026-08-01T10:03:05',
              agent_id=AGENT_B, attrs={**b, 'command': 'git diff'})
        conn.commit()
    finally:
        conn.close()
    return tmp_db


def _get(client, url):
    r = client.get(url)
    assert r.status_code == 200, r.get_data(as_text=True)
    return r.get_json()


def _span_ids(payload) -> set:
    return {s['span_id'] for s in payload.get('spans') or []}


def _node_ids(payload) -> set:
    """Span ids named by a wire tree, at whatever depth."""
    out: set = set()

    def walk(nodes):
        for n in nodes:
            data = n.get('data') or n
            if data.get('span_id'):
                out.add(data['span_id'])
            walk(n.get('children') or [])

    walk(payload.get('tree') or payload.get('children') or [])
    return out


# ── 1: the shallow map ────────────────────────────────────────

def test_shallow_map_serves_the_child_with_a_real_total(flask_client, seeded):
    """Was `spans=1, span_count_total=0`: the stored-trace_id query matched
    nothing and the count fell through to the absent `sessions` row."""
    d = _get(flask_client, f'/api/sessions/{SA_A}/map?shallow=1&limit=10')
    assert d['span_count_total'] == len(AGENT_A_SPANS)
    assert d['tree']
    assert _span_ids(d) <= AGENT_A_SERVED
    assert _span_ids(d)
    assert d['span_count'] == len(AGENT_A_SERVED)


def test_shallow_map_roots_are_the_childs_own(flask_client, seeded):
    d = _get(flask_client, f'/api/sessions/{SA_A}/map?shallow=1&limit=10')
    assert _node_ids(d) == {f'prompt-sa-{AGENT_A}'}


# ── 2: the full map ───────────────────────────────────────────

def test_full_map_serves_every_span_the_agent_owns(flask_client, seeded):
    d = _get(flask_client, f'/api/sessions/{SA_A}/map')
    assert _span_ids(d) == AGENT_A_SERVED
    assert d['span_count'] == len(AGENT_A_SERVED)


def test_full_map_of_the_other_agent_is_its_own(flask_client, seeded):
    d = _get(flask_client, f'/api/sessions/{SA_B}/map')
    assert _span_ids(d) == AGENT_B_SERVED


def test_the_child_never_renders_itself_as_a_subagent(flask_client, seeded):
    """The reported defect: `sa-<agent>` served the agent's own `subagent.start`
    / `subagent.stop`, so its pane opened with a `subagent: goal-builder` card
    and a "Subagent done" row — the agent nested inside itself."""
    for url in (f'/api/sessions/{SA_A}/map',
                f'/api/sessions/{SA_A}/map?shallow=1&limit=10',
                f'/api/sessions/{SA_A}'):
        d = _get(flask_client, url)
        served = _span_ids(d) | _node_ids(d)
        assert not served & {'A-start', 'A-stop'}, url
        assert not [s for s in d.get('spans') or []
                    if s['name'].startswith('subagent.')], url


# ── 3: pagination, and what must never leak ───────────────────

@pytest.mark.parametrize('query', [
    'shallow=1&limit=2',
    'shallow=1&limit=10',
    'shallow=1&limit=2&before_id=99999999',
    'shallow=1&limit=2&after_id=0',
])
def test_paging_never_leaks_another_agents_spans(flask_client, seeded, query):
    d = _get(flask_client, f'/api/sessions/{SA_A}/map?{query}')
    served = _span_ids(d) | _node_ids(d)
    assert not served & AGENT_B_SPANS
    assert not served & MAIN_SPANS
    assert served <= AGENT_A_SPANS


def test_scroll_up_from_the_oldest_loaded_id(flask_client, seeded):
    """The cursor the client actually sends back, not a synthetic one."""
    first = _get(flask_client, f'/api/sessions/{SA_A}/map?shallow=1&limit=1')
    cursor = first['oldest_loaded_id']
    older = _get(
        flask_client,
        f'/api/sessions/{SA_A}/map?shallow=1&limit=5&before_id={cursor}')
    assert _span_ids(older) | _node_ids(older) <= AGENT_A_SPANS


def test_live_tail_after_the_newest_loaded_id(flask_client, seeded):
    first = _get(flask_client, f'/api/sessions/{SA_A}/map?shallow=1&limit=10')
    tail = _get(
        flask_client,
        f'/api/sessions/{SA_A}/map?shallow=1&limit=10'
        f'&after_id={first["newest_loaded_id"]}')
    assert _span_ids(tail) | _node_ids(tail) <= AGENT_A_SPANS


# ── 4: one span's children and body ───────────────────────────

def test_children_of_the_childs_root(flask_client, seeded):
    d = _get(flask_client,
             f'/api/sessions/{SA_A}/spans/prompt-sa-{AGENT_A}/children')
    assert _span_ids(d) | _node_ids(d) <= AGENT_A_SPANS
    assert 'A-bash' in _span_ids(d)


def test_span_content_resolves_for_an_agent_owned_span(flask_client, seeded):
    """404 before: the row is stored under the parent, so matching the served
    id as a `trace_id` found nothing for every body the child asks for."""
    d = _get(flask_client, f'/api/sessions/{SA_A}/spans/A-bash/content')
    assert d['attributes']['command'] == 'pytest -q'


def test_span_content_refuses_another_agents_span(flask_client, seeded):
    assert flask_client.get(
        f'/api/sessions/{SA_A}/spans/B-bash/content').status_code == 404
    assert flask_client.get(
        f'/api/sessions/{SA_A}/spans/m-bash/content').status_code == 404


# ── 5: subtrees ───────────────────────────────────────────────

def test_subtrees_resolve_for_the_childs_root(flask_client, seeded):
    d = _get(flask_client,
             f'/api/sessions/{SA_A}/subtrees?ids=prompt-sa-{AGENT_A}')
    assert f'prompt-sa-{AGENT_A}' in d['subtrees']
    assert _span_ids(d) <= AGENT_A_SPANS
    assert 'A-bash' in _span_ids(d)


# ── 6: the synthetic header ───────────────────────────────────

_HEADER_FIELDS = (
    'title', 'model', 'status', 'cwd', 'parent_trace_id', 'started_at',
    'ended_at', 'input_tokens', 'output_tokens', 'span_count_total')


def test_finished_agents_header_is_derived_not_blank(flask_client, seeded):
    d = _get(flask_client, f'/api/sessions/{SA_A}/map?shallow=1&limit=10')
    assert {k: d.get(k) for k in _HEADER_FIELDS} == {
        'title': 'Build the thing',      # its description
        'model': 'claude-opus-5',        # the parent's
        'status': 'ended',               # it has a stop marker
        'cwd': '/repo',                  # the parent's
        'parent_trace_id': PARENT,
        'started_at': '2026-08-01T10:01:00',
        'ended_at': '2026-08-01T10:02:00',
        'input_tokens': 1000,
        'output_tokens': 200,
        'span_count_total': len(AGENT_A_SPANS),
    }


def test_running_agent_falls_back_to_type_and_inherits_status(
        flask_client, seeded):
    d = _get(flask_client, f'/api/sessions/{SA_B}/map?shallow=1&limit=10')
    assert d['title'] == 'goal-verifier'           # no description recorded
    assert d['status'] == 'active'                 # the parent's, no stop yet
    assert d['parent_trace_id'] == PARENT


def test_header_omits_what_no_source_supplies(flask_client, tmp_db):
    """An agent whose parent has no `sessions` row must report no model and no
    cwd rather than a fabricated one."""
    conn = sqlite3.connect(str(tmp_db))
    try:
        _span(conn, 'C-start', 'subagent.start', '2026-08-01T11:00:00',
              agent_id='c3333', attrs={'agent_id': 'c3333'},
              trace_id='orphan-parent')
        conn.commit()
    finally:
        conn.close()
    d = _get(flask_client, '/api/sessions/sa-c3333/map?shallow=1&limit=10')
    assert d['title'] == 'c3333'                   # no description, no type
    assert 'model' not in d
    assert 'cwd' not in d
    assert d['parent_trace_id'] == 'orphan-parent'


def test_detail_route_carries_the_same_header(flask_client, seeded):
    d = _get(flask_client, f'/api/sessions/{SA_A}')
    assert d['title'] == 'Build the thing'
    assert d['parent_trace_id'] == PARENT
    assert _span_ids(d) == AGENT_A_SERVED


def test_unknown_agent_reads_as_an_empty_session(flask_client, seeded):
    d = _get(flask_client, '/api/sessions/sa-nosuchagent/map?shallow=1&limit=5')
    assert d['spans'] == []
    assert 'parent_trace_id' not in d


# ── 7: what the parent keeps of an agent ──────────────────────

_MARKERS = {'A-start', 'A-stop', 'B-start'}
_DELEGATED = (AGENT_A_SPANS | AGENT_B_SPANS) - _MARKERS


def test_parent_map_keeps_the_markers_and_none_of_the_work(flask_client,
                                                           seeded):
    """The parent announces each agent; the conversation itself is the child
    trace's to serve."""
    d = _get(flask_client, f'/api/sessions/{PARENT}/map')
    assert _span_ids(d) == MAIN_SPANS | _MARKERS
    assert not _span_ids(d) & _DELEGATED


def test_parent_shallow_map_keeps_the_markers_and_none_of_the_work(
        flask_client, seeded):
    """The shallow map ships roots and counts; the markers themselves arrive
    through `/children`, which is where they are asserted."""
    d = _get(flask_client, f'/api/sessions/{PARENT}/map?shallow=1&limit=10')
    served = _span_ids(d) | _node_ids(d)
    assert not served & _DELEGATED
    assert d['span_count'] == len(MAIN_SPANS | _MARKERS)
    kids = _get(flask_client, f'/api/sessions/{PARENT}/spans/p-main/children')
    assert {'A-start', 'B-start'} <= _span_ids(kids) | _node_ids(kids)
    assert not _span_ids(kids) & _DELEGATED
    # The stop marker stays nested under the start it closes.
    stop = _get(flask_client, f'/api/sessions/{PARENT}/spans/A-start/children')
    assert _span_ids(stop) | _node_ids(stop) == {'A-stop'}


def _by_id(payload) -> dict:
    return {s['span_id']: s for s in payload['spans']}


def test_parent_map_marker_still_carries_its_rollup(flask_client, seeded):
    """The card stands alone off the marker: dropping the spans it summarizes
    must not drop what they were summarized into."""
    a_start = _by_id(_get(flask_client, f'/api/sessions/{PARENT}/map'))['A-start']
    attrs = a_start['attributes']
    assert attrs['agent_span_count'] == len(AGENT_A_SPANS - _MARKERS)
    assert attrs['agent_tool_count'] == 1
    assert attrs['agent_input_tokens'] == 1000
    assert attrs['agent_output_tokens'] == 200
    assert attrs['agent_child_trace_id'] == SA_A


def test_shallow_map_marker_still_carries_its_rollup(flask_client, seeded):
    d = _get(flask_client, f'/api/sessions/{PARENT}/map?shallow=1&limit=10')
    kids = _get(flask_client,
                f'/api/sessions/{PARENT}/spans/p-main/children')
    marker = _by_id(kids)['A-start']
    assert marker['attributes']['agent_span_count'] == 3
    assert marker['attributes']['agent_child_trace_id'] == SA_A
    assert d['span_count_delegated'] == len(_DELEGATED)


def test_delegated_count_reconciles_the_total_with_what_is_shown(
        flask_client, seeded):
    """`span_count_total` keeps meaning the session's real total, so the gap
    between it and what the view holds is named rather than hidden."""
    d = _get(flask_client, f'/api/sessions/{PARENT}/map?shallow=1&limit=10')
    assert d['span_count_total'] == 10            # the `sessions` row's count
    assert d['span_count'] == len(MAIN_SPANS | _MARKERS)
    assert d['span_count_delegated'] == len(_DELEGATED)
    assert d['span_count'] + d['span_count_delegated'] == d['span_count_total']


def test_the_cards_own_counts_agree_with_the_delegated_count(
        flask_client, seeded, tmp_db):
    """Two numbers for the same rows: what the header withheld, and what the
    cards claim to stand for. A PENDING twin counted by one and not the other
    is how they drifted apart by 1,457 on a real session."""
    conn = sqlite3.connect(str(tmp_db))
    try:
        conn.execute(
            """INSERT INTO session_spans
               (trace_id, span_id, name, kind, start_time, attributes,
                status_code, agent_id)
               VALUES (?, 'pending-A-bash', 'tool.Bash', 'internal',
                       '2026-08-01T10:01:04', ?, 'PENDING', ?)""",
            (PARENT, json.dumps({'agent_id': AGENT_A, 'tool_name': 'Bash'}),
             AGENT_A))
        conn.commit()
    finally:
        conn.close()
    d = _get(flask_client, f'/api/sessions/{PARENT}/map?shallow=1&limit=10')
    full = _get(flask_client, f'/api/sessions/{PARENT}/map')
    claimed = sum((s.get('attributes') or {}).get('agent_span_count') or 0
                  for s in full['spans'] if s['name'].startswith('subagent.'))
    assert d['span_count_delegated'] == len(_DELEGATED)
    assert claimed == d['span_count_delegated']
    assert full['span_count'] + d['span_count_delegated'] == d['span_count_total']


# ── 7a: a resumed agent's segments survive the shallow strip ──

@pytest.fixture
def resumed(seeded, tmp_db):
    """Agent A continued by a `SendMessage`: a second start/stop pair under
    the same agent id, with one span of its own."""
    conn = sqlite3.connect(str(tmp_db))
    try:
        _span(conn, 'A-start-2', 'subagent.start', '2026-08-01T10:03:00',
              agent_id=AGENT_A, attrs={'agent_id': AGENT_A})
        _span(conn, 'A-grep', 'tool.Grep', '2026-08-01T10:03:01',
              end='2026-08-01T10:03:02', agent_id=AGENT_A,
              attrs={'agent_id': AGENT_A, 'tool_name': 'Grep'})
        _span(conn, 'A-stop-2', 'subagent.stop', '2026-08-01T10:03:03',
              agent_id=AGENT_A, attrs={'agent_id': AGENT_A})
        conn.commit()
    finally:
        conn.close()
    return tmp_db


def test_shallow_map_keeps_the_segment_keys(flask_client, resumed):
    """The shallow payload strips attributes to an allowlist. Dropped here,
    the two runs of one agent reach the card indistinguishable — which is the
    duplicate-looking pair this exists to prevent."""
    kids = _get(flask_client, f'/api/sessions/{PARENT}/spans/p-main/children')
    by_id = _by_id(kids)
    assert by_id['A-start']['attributes']['agent_segment_index'] == 0
    second = by_id['A-start-2']['attributes']
    assert (second['agent_segment_index'], second['agent_segment_count']) == (1, 2)
    assert second['agent_segment_span_count'] == 1
    assert second['agent_segment_tool_count'] == 1


def test_a_resumed_agents_segments_still_sum_to_its_total(flask_client, resumed):
    d = _get(flask_client, f'/api/sessions/{PARENT}/map')
    starts = [s for s in d['spans'] if s['name'] == 'subagent.start'
              and s['attributes'].get('agent_id') == AGENT_A]
    # `.get`: a segment with nothing resolved yet reports no shape at all.
    total = sum(s['attributes'].get('agent_segment_span_count', 0) for s in starts)
    first = next(s for s in starts if s['span_id'] == 'A-start')
    assert total == first['attributes']['agent_span_count']


# ── 7b: an agent with no marker to stand for it ───────────────

MARKERLESS = 'c3333333333333333'


@pytest.fixture
def markerless(seeded, tmp_db):
    """A third agent that emitted work and no marker at all — 128 of the 3,533
    agents in the local store."""
    conn = sqlite3.connect(str(tmp_db))
    try:
        _span(conn, 'C-bash', 'tool.Bash', '2026-08-01T10:04:00',
              agent_id=MARKERLESS,
              attrs={'agent_id': MARKERLESS, 'command': 'make'})
        conn.commit()
    finally:
        conn.close()
    return tmp_db


def test_an_agent_with_no_marker_still_renders_inline(flask_client,
                                                      markerless):
    """Nothing here could stand for it or link to its child, so hiding it
    would lose the work outright."""
    d = _get(flask_client, f'/api/sessions/{PARENT}/map')
    assert 'C-bash' in _span_ids(d)
    assert not _span_ids(d) & _DELEGATED       # the marked agents still collapse


def test_a_markerless_agents_spans_are_not_counted_as_delegated(
        flask_client, markerless):
    """It is on screen, so counting it as withheld would report one row as
    both shown and delegated."""
    d = _get(flask_client, f'/api/sessions/{PARENT}/map?shallow=1&limit=10')
    assert d['span_count_delegated'] == len(_DELEGATED)
    assert d['span_count'] == len(MAIN_SPANS | _MARKERS) + 1


def test_a_markerless_agents_span_is_still_deep_linkable(flask_client,
                                                         markerless):
    """It was never withheld, so the reveal path resolves a chain here rather
    than sending the client to a child trace."""
    d = _get(flask_client, f'/api/sessions/{PARENT}/spans/C-bash/ancestors')
    assert d['chain'][-1] == 'C-bash'
    assert 'child_trace_id' not in d


def test_parent_shallow_map_is_anchored_on_its_own_prompt(flask_client,
                                                          seeded):
    d = _get(flask_client, f'/api/sessions/{PARENT}/map?shallow=1&limit=10')
    assert _node_ids(d) >= {'p-main'}
    assert d['title'] == 'the main prompt'
    assert 'parent_trace_id' not in d


def test_parent_span_content_is_unchanged(flask_client, seeded):
    """Only the projection narrowed: a body the operator already has a span id
    for still resolves under the parent."""
    body = _get(flask_client, f'/api/sessions/{PARENT}/spans/m-bash/content')
    assert body['attributes']['command'] == 'ls'
    agent_body = _get(flask_client, f'/api/sessions/{PARENT}/spans/A-bash/content')
    assert agent_body['attributes']['command'] == 'pytest -q'


def test_parent_children_of_a_marker_are_empty(flask_client, seeded):
    kids = _get(flask_client, f'/api/sessions/{PARENT}/spans/A-start/children')
    assert not _span_ids(kids) & _DELEGATED


# ── 8: the child is unchanged by the parent's narrowing ───────

def test_child_still_serves_every_span_it_owns(flask_client, seeded):
    full = _get(flask_client, f'/api/sessions/{SA_A}/map')
    shallow = _get(flask_client, f'/api/sessions/{SA_A}/map?shallow=1&limit=10')
    assert _span_ids(full) == AGENT_A_SERVED
    assert shallow['span_count'] == len(AGENT_A_SERVED)
    assert 'span_count_delegated' not in shallow


# ── 9: the retired list still means "superseded" ──────────────

def test_retired_ids_never_name_a_delegated_span(flask_client, seeded, tmp_db):
    """The client prunes `retired_span_ids` from its append-only cards. A span
    a child trace serves has not been superseded, and reporting it here would
    make the two indistinguishable — and the list enormous."""
    from lib.trace.pending_spans import prompt_placeholder_id
    ph = prompt_placeholder_id(PARENT, 'the main prompt')
    conn = sqlite3.connect(str(tmp_db))
    try:
        _span(conn, ph, 'prompt', '2026-08-01T09:59:59',
              attrs={'text': 'the main prompt'})
        conn.commit()
    finally:
        conn.close()
    d = _get(flask_client, f'/api/sessions/{PARENT}/map?shallow=1&limit=10')
    retired = set(d['retired_span_ids'])
    assert ph in retired                       # genuinely superseded
    assert not retired & _DELEGATED            # merely delegated
    assert retired == {ph}


def test_retired_list_is_empty_when_only_delegation_narrowed_the_view(
        flask_client, seeded):
    d = _get(flask_client, f'/api/sessions/{PARENT}/map?shallow=1&limit=10')
    assert d['retired_span_ids'] == []
    assert d['retired_digest'] == ''


# ── 10: a deep link into a span that moved ────────────────────

def test_ancestors_of_a_delegated_span_name_the_child_trace(flask_client,
                                                            seeded):
    """`?span=` deep links and search hits both come through here; the span is
    no longer revealable in this trace, so the answer is where it went."""
    d = _get(flask_client, f'/api/sessions/{PARENT}/spans/A-bash/ancestors')
    assert d['child_trace_id'] == SA_A
    assert d['chain'] == []
    d_b = _get(flask_client, f'/api/sessions/{PARENT}/spans/B-bash/ancestors')
    assert d_b['child_trace_id'] == SA_B


def test_ancestors_of_a_main_span_still_resolve_a_chain(flask_client, seeded):
    d = _get(flask_client, f'/api/sessions/{PARENT}/spans/m-bash/ancestors')
    assert d['chain'][-1] == 'm-bash'
    assert d['root_span_id'] == d['chain'][0]
    assert 'child_trace_id' not in d


def test_ancestors_of_a_span_that_does_not_exist_is_still_404(flask_client,
                                                              seeded):
    r = flask_client.get(f'/api/sessions/{PARENT}/spans/nosuchspan/ancestors')
    assert r.status_code == 404


def test_ancestors_inside_the_child_resolve_normally(flask_client, seeded):
    d = _get(flask_client, f'/api/sessions/{SA_A}/spans/A-bash/ancestors')
    assert d['chain'][-1] == 'A-bash'
    assert 'child_trace_id' not in d


# ── 11: billing and the roster read SQL, not the projection ───

def test_tool_rollup_still_bills_the_agents_work(flask_client, seeded, tmp_db):
    """The footer sums `session_spans` directly, so narrowing the projection
    must not move a dollar."""
    conn = sqlite3.connect(str(tmp_db))
    try:
        conn.execute("UPDATE session_spans SET input_tokens = 30, "
                     "output_tokens = 5 WHERE span_id = 'A-bash'")
        conn.execute("UPDATE session_spans SET input_tokens = 10, "
                     "output_tokens = 2 WHERE span_id = 'm-bash'")
        conn.commit()
    finally:
        conn.close()
    d = _get(flask_client, f'/api/sessions/{PARENT}/tool-rollup')
    bash = next(r for r in d['rollup'] if r['name'] == 'Bash')
    assert bash['calls'] == 2
    assert bash['input_tokens'] == 40
    assert bash['output_tokens'] == 7


def test_roster_still_lists_every_agent(flask_client, seeded):
    d = _get(flask_client, f'/api/sessions/{PARENT}/map?shallow=1&limit=10')
    assert {e['agent_id'] for e in d['agent_roster']} == {AGENT_A, AGENT_B}


# ── 12: a session that never delegated ────────────────────────

def test_a_trace_with_no_subagents_is_untouched(flask_client, tmp_db):
    """Acceptance for every session that never launched an agent: the same
    spans, and not one field that was not there before."""
    conn = sqlite3.connect(str(tmp_db))
    try:
        conn.execute(
            """INSERT INTO sessions (trace_id, started_at, last_seen, origin,
                                     is_test, title, span_count, model, status,
                                     cwd)
               VALUES ('solo-1', '2026-08-02T10:00:00', '2026-08-02T10:05:00',
                       'session', 0, 'solo', 2, 'claude-opus-5', 'ended',
                       '/repo')""")
        _span(conn, 's-prompt', 'prompt', '2026-08-02T10:00:00',
              attrs={'text': 'solo'}, trace_id='solo-1')
        _span(conn, 's-bash', 'tool.Bash', '2026-08-02T10:00:05',
              attrs={'command': 'ls'}, trace_id='solo-1')
        conn.commit()
    finally:
        conn.close()
    full = _get(flask_client, '/api/sessions/solo-1/map')
    shallow = _get(flask_client, '/api/sessions/solo-1/map?shallow=1&limit=10')
    assert _span_ids(full) == {'s-prompt', 's-bash'}
    assert full['span_count'] == 2
    assert 'span_count_delegated' not in full
    assert shallow['span_count'] == 2
    assert shallow['span_count_total'] == 2
    assert 'span_count_delegated' not in shallow
    assert shallow['retired_span_ids'] == []


# ── 13: a workflow run ────────────────────────────────────────

WF = 'wf_run_5555'
WF_AGENTS = ('w1111111111111111', 'w2222222222222222')
WF_KEPT = {'wf-session-start', 'wf-prompt', 'wf-phase-plan', 'wf-phase-build',
           'W1-start', 'W1-stop', 'W2-start', 'wf-session-end'}
WF_DELEGATED = {'W1-bash', 'W1-resp', 'W2-bash', 'W2-read'}


@pytest.fixture
def wf_seeded(tmp_db):
    """A workflow run: phases are never agent-tagged, so the structure the
    view keeps is prompt → phase → one card per agent."""
    w1, w2 = WF_AGENTS
    conn = sqlite3.connect(str(tmp_db))
    try:
        conn.execute(
            """INSERT INTO sessions (trace_id, started_at, last_seen, origin,
                                     is_test, title, span_count, model, status,
                                     cwd)
               VALUES (?, '2026-08-03T10:00:00', '2026-08-03T10:09:00',
                       'workflow', 0, 'the workflow', 12, 'claude-opus-5',
                       'ended', '/repo')""",
            (WF,))
        _span(conn, 'wf-session-start', 'session.start', '2026-08-03T10:00:00',
              trace_id=WF)
        _span(conn, 'wf-prompt', 'prompt', '2026-08-03T10:00:01',
              attrs={'text': 'run the workflow'}, trace_id=WF)
        _span(conn, 'wf-phase-plan', 'workflow.phase', '2026-08-03T10:00:02',
              attrs={'label': 'plan'}, trace_id=WF)
        for aid, tag, start in ((w1, 'W1', '10:01'), (w2, 'W2', '10:05')):
            a = {'agent_id': aid, 'agent_type': 'worker'}
            _span(conn, f'{tag}-start', 'subagent.start',
                  f'2026-08-03T{start}:00', agent_id=aid, attrs=dict(a),
                  trace_id=WF)
            _span(conn, f'{tag}-bash', 'tool.Bash', f'2026-08-03T{start}:05',
                  agent_id=aid, attrs={**a, 'command': 'ls'}, trace_id=WF)
        _span(conn, 'W1-resp', 'assistant_response', '2026-08-03T10:01:10',
              agent_id=w1, attrs={'agent_id': w1}, trace_id=WF)
        _span(conn, 'W1-stop', 'subagent.stop', '2026-08-03T10:02:00',
              end='2026-08-03T10:02:00', agent_id=w1,
              attrs={'agent_id': w1}, trace_id=WF)
        _span(conn, 'wf-phase-build', 'workflow.phase', '2026-08-03T10:04:00',
              attrs={'label': 'build'}, trace_id=WF)
        _span(conn, 'W2-read', 'tool.Read', '2026-08-03T10:05:10',
              agent_id=w2, attrs={'agent_id': w2}, trace_id=WF)
        _span(conn, 'wf-session-end', 'session.end', '2026-08-03T10:09:00',
              trace_id=WF)
        conn.commit()
    finally:
        conn.close()
    return tmp_db


def test_workflow_run_keeps_its_own_structure(flask_client, wf_seeded):
    d = _get(flask_client, f'/api/sessions/{WF}/map')
    assert _span_ids(d) == WF_KEPT
    assert not _span_ids(d) & WF_DELEGATED
    assert d['span_count_delegated'] == len(WF_DELEGATED)


def test_workflow_run_keeps_one_card_per_agent_with_its_rollup(flask_client,
                                                               wf_seeded):
    by_id = _by_id(_get(flask_client, f'/api/sessions/{WF}/map'))
    starts = {sid for sid, s in by_id.items() if s['name'] == 'subagent.start'}
    assert starts == {'W1-start', 'W2-start'}
    assert by_id['W1-start']['attributes']['agent_span_count'] == 2
    assert by_id['W2-start']['attributes']['agent_span_count'] == 2
    assert by_id['W1-start']['attributes']['agent_child_trace_id'] == \
        f'sa-{WF_AGENTS[0]}'


def test_workflow_agents_spans_are_served_by_the_child(flask_client,
                                                       wf_seeded):
    d = _get(flask_client, f'/api/sessions/sa-{WF_AGENTS[1]}/map')
    assert _span_ids(d) == {'W2-start', 'W2-bash', 'W2-read'}


def test_roster_names_each_agents_newest_span(flask_client, seeded):
    """The card's progress line reads the roster's `last_span`: the agent's
    newest row, phrased off its attributes — never a start/stop marker, which
    is the parent's account of the agent rather than a step it took."""
    d = _get(flask_client, f'/api/sessions/{PARENT}/map?shallow=1&limit=10')
    by_id = {e['agent_id']: e for e in d['agent_roster']}
    # A's newest row is its stop marker, so it reports no step.
    assert by_id[AGENT_A]['last_span'] is None
    b = by_id[AGENT_B]['last_span']
    assert b['name'] == 'tool.Bash'
    assert b['status_code'] is not None


def test_a_resumed_agents_duration_covers_every_run(flask_client, resumed):
    """The sheet presents this as how long the agent took. Measured from the
    LAST segment it reported 1m05s for an agent that ran nearly six minutes."""
    d = _get(flask_client, f'/api/sessions/{PARENT}/map?shallow=1&limit=10')
    entry = {e['agent_id']: e for e in d['agent_roster']}[AGENT_A]
    # 10:01:00.500 (first start) → 10:03:03 (last stop), not the 3s of run 2.
    assert entry['duration_ms'] > 120_000


def test_a_rescanned_response_does_not_outrank_the_live_tool(flask_client,
                                                             seeded, tmp_db):
    """`assistant_response`/`assistant.thinking` have no hook — they arrive in a
    transcript-rescan batch, so they carry the HIGHEST ids in the session while
    sitting EARLIER in time than the tool they precede. Picked by id, they made
    a running agent's card say "Thinking" while its timeline showed the shell
    command actually in flight."""
    conn = sqlite3.connect(str(tmp_db))
    try:
        # Ingested last (highest id), but timed BEFORE B's in-flight Bash.
        _span(conn, 'B-resp', 'assistant_response', '2026-08-01T10:03:01',
              agent_id=AGENT_B,
              attrs={'agent_id': AGENT_B, 'text': 'let me check'})
        conn.commit()
    finally:
        conn.close()
    d = _get(flask_client, f'/api/sessions/{PARENT}/map?shallow=1&limit=10')
    by_id = {e['agent_id']: e for e in d['agent_roster']}
    assert by_id[AGENT_B]['last_span']['name'] == 'tool.Bash'
