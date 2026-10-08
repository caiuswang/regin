"""`lib/palette/home.py` + `GET /api/palette/home` — the empty-query desk (f146 S4)."""

from __future__ import annotations

from lib.palette import home as H


def refs(rows):
    return [r["ref"] for r in rows]


def test_needs_me_orders_oldest_wait_first(conn, seed):
    seed.parked('aaaaaaaa-new', seconds_ago=120)
    seed.parked('bbbbbbbb-old', seconds_ago=1800,
                attrs={'tool_name': 'AskUserQuestion',
                       'tool_input': {'questions': [{'question': 'Which?'}]}})
    seed.working('cccccccc-busy')
    seed.feature(9)
    seed.defect(10, opened_at='2026-10-03 00:00:00')
    seed.defect(11, opened_at='2026-09-01 00:00:00')
    seed.defect(12, opened_at='2026-09-02 00:00:00')
    seed.defect(13, opened_at='2026-09-03 00:00:00')
    seed.defect(14, opened_at='2026-09-04 00:00:00')
    seed.defect(20, opened_at='2026-08-01 00:00:00', feature_id=9)
    seed.defect(21, opened_at='2026-08-01 00:00:00', triage='snoozed', snoozed_held=True)
    seed.defect(22, opened_at='2026-08-01 00:00:00', triage='declined')
    seed.defect(23, opened_at='2026-08-01 00:00:00', status='closed')
    seed.defect(24, opened_at='2026-08-02 00:00:00', triage='snoozed')
    conn.commit()

    needs = H.home(conn)['needs_me']

    assert refs(needs['items']) == ['#bbbbbbbb', '#aaaaaaaa', 'D24', 'D11', 'D12']
    assert needs['total'] == 2 + 6
    assert needs['items'][0]['aside'].startswith('question')
    assert all(r['status']['bucket'] == 'needs_you' for r in needs['items'])


def test_running_task_folds_into_its_session(conn, seed):
    seed.working('cccccccc-child')
    seed.alias('sdk-run430', 'cccccccc-child')
    seed.task(430, 'Wire the desk')
    seed.run(430, 'sdk-run430')
    seed.task(431, 'Not launched yet')
    seed.run(431, None, status='starting')
    seed.task(432, 'Idle run', status='idea')
    seed.session('dddddddd-old', status='ended', last_seen='2026-09-01T00:00:00')
    conn.commit()

    desk = H.home(conn)
    active = desk['active_now']['items']

    assert sorted(refs(active)) == ['#cccccccc', 'T431']
    session = next(r for r in active if r['kind'] == 'session')
    assert 'T430' in [c['ref'] for c in session['owners']]
    assert desk['active_now']['total'] == 2
    assert refs(desk['sessions']['items']) == ['#dddddddd']
    assert desk['sessions']['total'] == 1


def test_active_features_are_the_building_ones(conn, seed):
    seed.feature(1, 'Built', building=True)
    seed.feature(2, 'Spec only')
    conn.commit()

    group = H.home(conn)['active_features']

    assert refs(group['items']) == ['F1']
    assert group['total'] == 1


def test_this_session_chain(conn, seed, flask_client):
    seed.session('eeeeeeee-here', title='Here')
    seed.feature(146, 'Search palette')
    seed.link('eeeeeeee-here', 146)
    seed.task(419, 'Write the spec', feature_id=146)
    seed.run(419, 'eeeeeeee-here')
    seed.defect(276, feature_id=146, reporter='eeeeeeee-here')
    seed.defect(277, feature_id=146, reporter='ffffffff-else')
    conn.commit()

    body = flask_client.get('/api/palette/home?session=eeeeeeee-here').get_json()

    assert refs(body['chain']) == ['#eeeeeeee', 'F146', 'T419', 'D276']
    assert body['chain'][0]['here'] is True
    assert flask_client.get('/api/palette/home?session=nope').get_json() == {'chain': []}
