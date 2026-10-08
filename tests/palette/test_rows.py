"""`lib/palette/rows.py` — the batched `PaletteRow` builders (f146 S3)."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta

import pytest

from lib.palette import rows as R


@pytest.fixture
def conn(tmp_db):
    c = sqlite3.connect(str(tmp_db))
    yield c
    c.close()


def _ago(seconds: float) -> str:
    return (datetime.now() - timedelta(seconds=seconds)).isoformat()


def _session(conn, trace_id, *, title=None, title_source=None, status='active',
             last_seen=None):
    conn.execute(
        "INSERT INTO sessions (trace_id, title, title_source, status, started_at, "
        " last_seen, cwd) VALUES (?, ?, ?, ?, '2026-10-01T00:00:00', ?, '/tmp/r')",
        (trace_id, title, title_source, status, last_seen or _ago(5)))


def _span(conn, trace_id, span_id, name, *, start, status='OK', attrs=None,
          agent_id=None):
    conn.execute(
        "INSERT INTO session_spans (trace_id, span_id, name, kind, start_time, "
        " status_code, attributes, agent_id) VALUES (?, ?, ?, 'internal', ?, ?, ?, ?)",
        (trace_id, span_id, name, start, status, json.dumps(attrs or {}), agent_id))


def _prompt(conn, trace_id, span_id, text, *, start):
    _span(conn, trace_id, span_id, 'prompt', start=start, attrs={'text': text})


def _feature(conn, fid, title):
    conn.execute("INSERT INTO features (id, title) VALUES (?, ?)", (fid, title))


def _task(conn, tid, title, *, status='running', feature_id=None):
    conn.execute(
        "INSERT INTO agent_tasks (id, title, status, source, feature_id) "
        "VALUES (?, ?, ?, 'native', ?)", (tid, title, status, feature_id))


def _run(conn, task_id, trace_id, *, attempt=1, status='running'):
    conn.execute(
        "INSERT INTO agent_task_runs (task_id, trace_id, attempt, status) "
        "VALUES (?, ?, ?, ?)", (task_id, trace_id, attempt, status))


def _link(conn, trace_id, feature_id, *, primary=0):
    conn.execute(
        "INSERT INTO session_features (trace_id, feature_id, is_primary) "
        "VALUES (?, ?, ?)", (trace_id, feature_id, primary))


def _refs(row):
    return [chip['ref'] for chip in row['owners']]


def test_session_row_names_its_owners(conn):
    _session(conn, 'a1b2c3d4e5f6', title='Palette row builder', title_source='user')
    _feature(conn, 146, 'Search palette')
    _task(conn, 419, 'Write the palette spec', feature_id=146)
    _link(conn, 'a1b2c3d4e5f6', 146, primary=1)
    _run(conn, 419, 'a1b2c3d4e5f6')
    _prompt(conn, 'a1b2c3d4e5f6', 'p1', 'first ask', start=_ago(300))
    _prompt(conn, 'a1b2c3d4e5f6', 'p2', 'now wire the switcher\nsecond line',
            start=_ago(60))
    conn.commit()

    row = R.session_rows(conn, ['a1b2c3d4e5f6'])['a1b2c3d4e5f6']

    assert row['ref'] == '#a1b2c3d4'
    assert row['title'] == 'Palette row builder'
    assert _refs(row) == ['F146', 'T419']
    assert row['owners'][0]['title'] == 'Search palette'
    assert row['context'] == 'now wire the switcher'
    assert row['status'] == {'word': 'idle', 'bucket': R.QUIET}


def test_session_row_folds_extra_features_into_a_count(conn):
    _session(conn, 's-multi', title='t', title_source='user')
    for fid in (1, 2, 3):
        _feature(conn, fid, f'F{fid}')
        _link(conn, 's-multi', fid, primary=1 if fid == 2 else 0)
    conn.commit()

    row = R.session_rows(conn, ['s-multi'])['s-multi']

    assert _refs(row) == ['F2', '+2']
    assert row['owners'][1]['title'] == 'F1, F3'


def test_run_child_session_inherits_owners_recorded_on_the_run_id(conn):
    _session(conn, 'child-1', title='t', title_source='user')
    conn.execute("INSERT INTO agent_runs (trace_id, cli_session_id) "
                 "VALUES ('sdk-run1', 'child-1')")
    _feature(conn, 144, 'Tasks')
    _task(conn, 428, 'Fix the filter', feature_id=144)
    _run(conn, 428, 'sdk-run1')
    conn.commit()

    _session(conn, 'sdk-run1', title='t', title_source='user')
    conn.commit()

    rows = R.session_rows(conn, ['child-1', 'sdk-run1'])

    assert _refs(rows['child-1']) == ['F144', 'T428']
    assert _refs(rows['sdk-run1']) == ['F144', 'T428']
    assert _refs(R.session_rows(conn, ['child-1'])['child-1']) == ['F144', 'T428']


def test_context_skips_the_skill_invocation_line(conn):
    _session(conn, 'skill-run', title='t', title_source='user')
    _prompt(conn, 'skill-run', 'p1', '/goal-verified-treenav\nWire the rows',
            start=_ago(30))
    conn.commit()

    _session(conn, 'skill-args', title='t', title_source='user')
    _prompt(conn, 'skill-args', 'p1', '/goal-verified-treenav Run the slice\n## Delivers',
            start=_ago(30))
    conn.commit()

    rows = R.session_rows(conn, ['skill-run', 'skill-args'])

    assert rows['skill-run']['context'] == 'Wire the rows'
    assert rows['skill-args']['context'] == 'Run the slice'


def test_untitled_session_takes_task_title(conn):
    _session(conn, 'run-untitled')
    _task(conn, 419, 'Write the palette spec')
    _run(conn, 419, 'run-untitled')
    conn.commit()

    row = R.session_rows(conn, ['run-untitled'])['run-untitled']

    assert row['title'] == 'Write the palette spec'


def test_task_title_outranks_a_first_prompt_title(conn):
    _session(conn, 'run-skill', title='/goal-verified-treenav', title_source='first_prompt')
    _task(conn, 419, 'Write the palette spec')
    _run(conn, 419, 'run-skill')
    conn.commit()

    assert R.session_rows(conn, ['run-skill'])['run-skill']['title'] == \
        'Write the palette spec'


def test_untitled_session_falls_back_to_first_prompt_line(conn):
    _session(conn, 'no-title')
    _prompt(conn, 'no-title', 'p1', 'Fix the drill pager\nmore detail', start=_ago(90))
    _prompt(conn, 'no-title', 'p2', 'later ask', start=_ago(30))
    conn.commit()

    row = R.session_rows(conn, ['no-title'])['no-title']

    assert row['title'] == 'Fix the drill pager'
    assert row['context'] == 'later ask'


def test_untitled_no_prompts(conn):
    _session(conn, 'deadbeef0000')
    conn.commit()

    row = R.session_rows(conn, ['deadbeef0000'])['deadbeef0000']

    assert row['title'] == 'untitled · no prompts'
    assert 'deadbeef' not in row['title']
    assert row['owners'] == []


def test_retrying_is_active_not_needs_you(conn):
    _session(conn, 'net-stall', title='t', title_source='user')
    _session(conn, 'parked', title='t', title_source='user')
    conn.commit()

    rows = R.session_rows(conn, ['net-stall', 'parked'],
                          phases={'net-stall': 'retrying',
                                  'parked': 'waiting-input'})

    assert rows['net-stall']['status'] == {'word': 'reconnecting', 'bucket': R.ACTIVE}
    assert rows['parked']['status'] == {'word': 'needs you', 'bucket': R.NEEDS_YOU}


def test_awaiting_session_reads_needs_you_with_what_it_asks(conn):
    _session(conn, 'asks', title='t', title_source='user')
    _prompt(conn, 'asks', 'p1', 'clean the vendored dir', start=_ago(700))
    _span(conn, 'asks', 'q1', 'tool.AskUserQuestion', start=_ago(360),
          status='PENDING', attrs={'question': 'Delete the vendored binary?'})
    conn.commit()

    row = R.session_rows(conn, ['asks'])['asks']

    assert row['status']['word'] == 'needs you'
    assert row['context'] == 'asks: Delete the vendored binary?'
    assert row['aside'] == 'question · 6m'


def test_answered_blocker_and_never_ran_fork_do_not_park(conn):
    _session(conn, 'answered', title='t', title_source='user')
    _span(conn, 'answered', 'q1', 'permission.request', start=_ago(8),
          status='PENDING', attrs={'command': 'rm -rf build'})
    _span(conn, 'answered', 'r1', 'tool.Bash', start=_ago(4))
    _session(conn, 'forked', title='t', title_source='user')
    _span(conn, 'forked', 'm1', 'tool.Read', start=_ago(4))
    _span(conn, 'forked', 'f1', 'permission.request', start=_ago(2),
          status='PENDING', agent_id='side-fork')
    conn.commit()

    rows = R.session_rows(conn, ['answered', 'forked'])

    assert rows['answered']['status']['word'] == 'working'
    assert rows['forked']['status']['word'] == 'working'


def test_task_needs_you_when_run_awaits(conn):
    _task(conn, 1, 'waiting task')
    _task(conn, 2, 'busy task')
    _task(conn, 3, 'finished task', status='done')
    for trace_id in ('run-wait', 'run-busy', 'run-done'):
        _session(conn, trace_id)
    _run(conn, 1, 'run-wait', attempt=2)
    _run(conn, 2, 'run-busy')
    _run(conn, 3, 'run-done', status='done')
    _prompt(conn, 'run-wait', 'p1', 'go', start=_ago(120))
    _span(conn, 'run-wait', 'perm', 'permission.request', start=_ago(60),
          status='PENDING', attrs={'command': 'git push'})
    _span(conn, 'run-busy', 'b1', 'tool.Bash', start=_ago(2))
    _span(conn, 'run-done', 'perm', 'permission.request', start=_ago(60),
          status='PENDING')
    conn.commit()

    rows = R.task_rows(conn, [1, 2, 3])

    assert rows[1]['status'] == {'word': 'needs you', 'bucket': R.NEEDS_YOU}
    assert rows[1]['context'] == 'run 2 · running'
    assert rows[2]['status'] == {'word': 'running', 'bucket': R.ACTIVE}
    assert rows[3]['status'] == {'word': 'done', 'bucket': R.DONE}
    assert rows[1]['ref'] == 'T1'


def test_task_needs_you_when_its_sdk_run_child_awaits(conn):
    _task(conn, 9, 'sdk task')
    _run(conn, 9, 'sdk-run9')
    conn.execute("INSERT INTO agent_runs (trace_id, cli_session_id) "
                 "VALUES ('sdk-run9', 'child-9')")
    _session(conn, 'child-9')
    _prompt(conn, 'child-9', 'p1', 'go', start=_ago(120))
    _span(conn, 'child-9', 'perm', 'permission.request', start=_ago(60),
          status='PENDING')
    conn.commit()

    assert R.task_rows(conn, [9])[9]['status']['word'] == 'needs you'


def test_feature_row_progress_excludes_archived(conn):
    _feature(conn, 146, 'Search palette')
    conn.execute("INSERT INTO feature_events (feature_id, kind, declared_by) "
                 "VALUES (146, 'build_started', 'test')")
    _task(conn, 1, 'a', status='done', feature_id=146)
    _task(conn, 2, 'b', status='idea', feature_id=146)
    _task(conn, 3, 'c', status='archived', feature_id=146)
    conn.execute("INSERT INTO defects (feature_id, symptom, symptom_key) "
                 "VALUES (146, 'x', 'x'), (146, 'y', 'y')")
    conn.commit()

    row = R.feature_rows(conn, [146])[146]

    assert row['ref'] == 'F146'
    assert row['status'] == {'word': 'building · round 1', 'bucket': R.ACTIVE}
    assert row['context'] == '1/2 done · 2 open defects'


def test_defect_row_titles_by_symptom_and_names_fix(conn):
    _feature(conn, 146, 'Search palette')
    conn.execute(
        "INSERT INTO defects (id, feature_id, symptom, symptom_key, fix_task_id, "
        " triage_state) VALUES (276, NULL, ?, 'k1', NULL, 'queue'), "
        " (277, 146, 'Drill skips a page', 'k2', 430, NULL)",
        ('Show all row is a dead p\nIt cannot be focused',))
    conn.commit()

    rows = R.defect_rows(conn, [276, 277])

    assert rows[276]['title'] == 'Show all row is a dead p'
    assert rows[276]['status']['word'] == 'triage'
    assert _refs(rows[276]) == ['no feature']
    assert rows[276]['aside'] == 'no fix'
    assert rows[276]['context'] == 'It cannot be focused'
    assert rows[277]['status']['word'] == 'open'
    assert _refs(rows[277]) == ['F146']
    assert rows[277]['aside'] == 'fix T430'


def _seed_page(conn, n: int, offset: int) -> dict:
    ids = {'session': [], 'task': [], 'feature': [], 'defect': []}
    for i in range(offset, offset + n):
        trace_id = f'trace-{i:04d}'
        _session(conn, trace_id)
        _feature(conn, i, f'feature {i}')
        _task(conn, i, f'task {i}', feature_id=i)
        _link(conn, trace_id, i)
        _run(conn, i, trace_id)
        _prompt(conn, trace_id, f'p{i}', f'prompt {i}', start=_ago(30))
        conn.execute("INSERT INTO defects (id, feature_id, symptom, symptom_key) "
                     "VALUES (?, ?, 'bug', ?)", (i, i, f'k{i}'))
        ids['session'].append(trace_id)
        for kind in ('task', 'feature', 'defect'):
            ids[kind].append(i)
    conn.commit()
    return ids


def _statements(conn, build, ids) -> int:
    seen: list[str] = []
    conn.set_trace_callback(seen.append)
    try:
        out = build(conn, ids)
    finally:
        conn.set_trace_callback(None)
    assert len(out) == len(ids)
    return len(seen)


def test_rows_batch_queries(conn):
    small = _seed_page(conn, 2, offset=1)
    large = _seed_page(conn, 12, offset=100)
    builders = {'session': R.session_rows, 'task': R.task_rows,
                'feature': R.feature_rows, 'defect': R.defect_rows}

    for kind, build in builders.items():
        assert _statements(conn, build, small[kind]) == \
            _statements(conn, build, large[kind]), kind


def test_attach_rows_sets_row_on_session_items(conn):
    _session(conn, 'hit-1', title='Matched', title_source='user')
    conn.commit()
    groups = [{'kind': 'session', 'items': [{'id': 'hit-1'}, {'id': 'spans-only'}]},
              {'kind': 'task', 'items': [{'id': '999'}]},
              {'kind': 'pattern', 'items': [{'id': 'p.md'}]}]

    R.attach_rows(conn, groups)

    assert groups[0]['items'][0]['row']['title'] == 'Matched'
    assert groups[0]['items'][1]['row']['title'] == 'untitled · no prompts'
    assert 'row' not in groups[1]['items'][0]
    assert 'row' not in groups[2]['items'][0]


def test_switcher_rows_carry_palette_row(conn, flask_client):
    _session(conn, 'live-1', title='Roster row', title_source='user',
             last_seen=_ago(2))
    _feature(conn, 146, 'Search palette')
    _link(conn, 'live-1', 146)
    _span(conn, 'live-1', 's1', 'tool.Read', start=_ago(2))
    conn.commit()

    data = flask_client.get('/api/session-switcher').get_json()

    (entry,) = [s for s in data['sessions'] if s['trace_id'] == 'live-1']
    assert entry['row']['title'] == 'Roster row'
    assert _refs(entry['row']) == ['F146']
    assert entry['row']['status']['word'] == R._SESSION_STATUS[entry['phase']][0]


def test_search_session_items_carry_row(conn, flask_client):
    _session(conn, 'other-1', title='Other session', title_source='user')
    _task(conn, 419, 'Write the palette spec')
    _run(conn, 419, 'other-1')
    _span(conn, 'other-1', 'b1', 'tool.Bash', start=_ago(30),
          attrs={'command': 'palette build'})
    conn.commit()

    data = flask_client.get('/api/search?q=palette&kinds=session&scope=global').get_json()

    (group,) = data['groups']
    (item,) = group['items']
    assert item['row']['ref'] == '#other-1'
    assert _refs(item['row']) == ['T419']
