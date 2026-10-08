"""`GET /api/palette/list/<key>`, Home's per-kind totals, the session row's full
id, and the session preview's prompt list read through the serve-time merge."""

from __future__ import annotations

from lib.palette import home as H
from lib.palette import rows as R

from conftest import ago


def refs(payload):
    return [r["ref"] for r in payload["items"]]


def test_session_row_carries_full_trace_id(conn, seed):
    seed.session('aaaaaaaa-1111-2222-3333-444444444444')
    conn.commit()

    row = R.session_rows(conn, ['aaaaaaaa-1111-2222-3333-444444444444'])[
        'aaaaaaaa-1111-2222-3333-444444444444']

    assert row['ref'] == '#aaaaaaaa'
    assert row['id'] == 'aaaaaaaa-1111-2222-3333-444444444444'


def test_entity_rows_carry_their_id(conn, seed):
    seed.feature(7)
    seed.task(8, feature_id=7)
    seed.defect(9)
    conn.commit()

    assert R.feature_rows(conn, [7])[7]['id'] == 7
    assert R.task_rows(conn, [8])[8]['id'] == 8
    assert R.defect_rows(conn, [9])[9]['id'] == 9


def test_list_reaches_every_queued_defect_past_the_home_cap(conn, seed, flask_client):
    for did in range(1, 9):
        seed.defect(did, opened_at=f'2026-09-0{did} 00:00:00')
    conn.commit()

    home = flask_client.get('/api/palette/home').get_json()
    first = flask_client.get('/api/palette/list/needs_me?limit=5').get_json()
    rest = flask_client.get('/api/palette/list/needs_me?limit=5&offset=5').get_json()

    assert home['needs_me']['total'] == 8
    assert len(home['needs_me']['items']) == H.NEEDS_ME_CAP
    assert first['total'] == rest['total'] == 8
    assert refs(first) + refs(rest) == [f'D{i}' for i in range(1, 9)]


def test_list_kind_pages_without_overlap(conn, seed, flask_client):
    for fid in range(1, 16):
        seed.feature(fid, f'feature {fid}')
    seed.task(30, status='idea')
    seed.task(31, status='archived')
    seed.defect(40)
    conn.commit()

    page1 = flask_client.get('/api/palette/list/feature').get_json()
    page2 = flask_client.get('/api/palette/list/feature?offset=12').get_json()
    tasks = flask_client.get('/api/palette/list/task').get_json()
    defects = flask_client.get('/api/palette/list/defect').get_json()

    assert page1['total'] == 15 and len(page1['items']) == 12
    assert len(page2['items']) == 3
    assert not set(refs(page1)) & set(refs(page2))
    assert {r['kind'] for r in page1['items'] + page2['items']} == {'feature'}
    assert refs(tasks) == ['T30']
    assert refs(defects) == ['D40']
    assert flask_client.get('/api/palette/list/nope').status_code == 404


def test_home_reports_per_kind_totals(conn, seed, flask_client):
    seed.feature(1)
    seed.feature(2)
    seed.task(3, status='idea')
    seed.defect(4)
    seed.session('aaaaaaaa-t')
    conn.commit()

    totals = flask_client.get('/api/palette/home').get_json()['totals']

    assert totals == {'feature': 2, 'task': 1, 'defect': 1, 'session': 1}


def test_latest_prompts_fold_live_placeholders(conn, seed, flask_client):
    trace = 'aaaaaaaa-prompts'
    seed.session(trace)
    seed.span(trace, 'promptlive-1111', 'prompt', start=ago(120), status='PENDING',
              attrs={'text': 'first ask'})
    seed.span(trace, 'prompt-1111', 'prompt', start=ago(119), attrs={'text': 'first ask'})
    seed.span(trace, 'promptlive-2222', 'prompt', start=ago(60), status='PENDING',
              attrs={'text': 'second ask'})
    seed.span(trace, 'prompt-2222', 'prompt', start=ago(59), attrs={'text': 'second ask'})
    conn.commit()

    prompts = flask_client.get(f'/api/palette/relations/session/{trace}').get_json()['prompts']

    assert [p['text'] for p in prompts] == ['second ask', 'first ask']
    assert [p['span_id'] for p in prompts] == ['prompt-2222', 'prompt-1111']


def test_latest_prompts_keep_one_slash_command_with_its_expansion(conn, seed, flask_client):
    trace = 'aaaaaaaa-slash'
    seed.session(trace)
    seed.span(trace, 'promptlive-3333', 'prompt', start=ago(60), status='PENDING',
              attrs={'text': '/run\n## Delivers the slice'})
    seed.span(trace, 'prompt-3333', 'prompt', start=ago(59), attrs={'text': '/run'})
    conn.commit()

    prompts = flask_client.get(f'/api/palette/relations/session/{trace}').get_json()['prompts']

    assert [p['text'] for p in prompts] == ['/run\n## Delivers the slice']


def test_latest_prompts_leave_out_subagent_prompts(conn, seed, flask_client):
    trace = 'aaaaaaaa-sub'
    seed.session(trace)
    seed.span(trace, 'prompt-main', 'prompt', start=ago(60), attrs={'text': 'main ask'})
    seed.span(trace, 'prompt-sa-1234', 'prompt', start=ago(30),
              attrs={'text': 'You did NOT write this.', 'agent_id': 'a1234'})
    conn.execute("UPDATE session_spans SET agent_id = 'a1234' WHERE span_id = 'prompt-sa-1234'")
    conn.commit()

    prompts = flask_client.get(f'/api/palette/relations/session/{trace}').get_json()['prompts']

    assert [p['text'] for p in prompts] == ['main ask']
