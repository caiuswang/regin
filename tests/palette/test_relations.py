"""`lib/palette/relations.py` + `GET /api/palette/relations/<kind>/<id>` —
the palette's entity preview (f146 S4)."""

from __future__ import annotations

import hashlib
import sqlite3

from lib.palette.relations import relations


def refs(rows):
    return [r["ref"] for r in rows]


def _rels(payload):
    return [(r['rel'], r['target'].get('ref') or r['target'].get('none'))
            for r in payload['relations']]


def test_defect_relations_name_reporter_and_fix(conn, seed, flask_client):
    seed.feature(146, 'Search palette')
    seed.task(430, 'Fix the drill', feature_id=146)
    seed.session('eeeeeeee-child')
    seed.alias('sdk-run1', 'eeeeeeee-child')
    seed.defect(276, feature_id=146, reporter='sdk-run1', fix=430,
                symptom='Show all is a dead p\nIt cannot be focused')
    seed.defect(278)
    conn.commit()

    linked = relations(conn, 'defect', 276)
    bare = flask_client.get('/api/palette/relations/defect/278').get_json()

    assert _rels(linked) == [('Belongs to', 'F146'), ('Reported in', '#eeeeeeee'),
                             ('Fixed by', 'T430')]
    assert linked['relations'][1]['target']['id'] == 'eeeeeeee-child'
    assert linked['body'] == 'Show all is a dead p\nIt cannot be focused'
    assert linked['lists'] == []
    assert _rels(bare) == [('Belongs to', 'No feature yet'),
                           ('Reported in', 'No reporting session'),
                           ('Fixed by', 'No fix task yet')]
    assert flask_client.get('/api/palette/relations/defect/999').status_code == 404


def test_feature_progress_excludes_archived(conn, seed):
    seed.feature(146, 'Search palette', building=True)
    seed.task(1, status='done', feature_id=146)
    seed.task(2, status='running', feature_id=146)
    seed.task(3, status='archived', feature_id=146)
    for tid in range(5, 11):
        seed.task(tid, status='idea', feature_id=146)
    seed.defect(40, feature_id=146)
    seed.defect(41, feature_id=146)
    seed.defect(42, feature_id=146, status='closed')
    seed.session('aaaaaaaa-linked')
    seed.link('aaaaaaaa-linked', 146)
    conn.commit()

    payload = relations(conn, 'feature', 146)
    lists = {lst['label']: lst for lst in payload['lists']}

    assert payload['progress'] == {'done': 1, 'tasks': 8, 'open_defects': 2}
    assert payload['row']['context'] == '1/8 done · 2 open defects'
    assert {label: (lst['total'], refs(lst['items'])) for label, lst in lists.items()} == {
        'Tasks': (8, ['T2', 'T10', 'T9', 'T8', 'T7']),
        'Open defects': (2, ['D41', 'D40']),
        'Recent sessions': (1, ['#aaaaaaaa']),
    }
    assert payload['body'] == 'Search palette summary'


def test_task_relations_list_runs_and_fixes(conn, seed):
    seed.feature(146, 'Search palette')
    seed.task(430, 'Fix the drill', feature_id=146)
    seed.session('cccccccc-child')
    seed.alias('sdk-run430', 'cccccccc-child')
    seed.run(430, 'sdk-run430', attempt=1, status='failed')
    seed.defect(276, feature_id=146, fix=430)
    conn.commit()

    payload = relations(conn, 'task', 430)
    runs, fixes = payload['lists']

    assert _rels(payload) == [('Belongs to', 'F146')]
    assert refs(runs['items']) == ['#cccccccc']
    assert runs['items'][0]['aside'] == 'run 1 · failed'
    assert refs(fixes['items']) == ['D276']


def _dump_hash(path) -> str:
    db = sqlite3.connect(str(path))
    try:
        return hashlib.sha256('\n'.join(db.iterdump()).encode()).hexdigest()
    finally:
        db.close()


def _palette_methods(client) -> set:
    return {m for rule in client.application.url_map.iter_rules()
            if rule.rule.startswith('/api/palette/') for m in rule.methods}


def test_waiting_block_is_read_only(conn, seed, tmp_db, flask_client):
    seed.feature(146, 'Search palette')
    seed.link('aaaaaaaa-rm', 146)
    seed.parked('aaaaaaaa-rm', seconds_ago=300,
                attrs={'tool_name': 'Bash', 'command_preview': 'rm -rf build/',
                       'requested_permission': 'Run shell command: rm -rf build/'})
    seed.parked('bbbbbbbb-ask', seconds_ago=60,
                attrs={'tool_name': 'AskUserQuestion', 'kind': 'question',
                       'tool_input': {'questions': [{'question': 'Ship it?'}]}})
    seed.session('cccccccc-calm')
    conn.commit()
    before = _dump_hash(tmp_db)

    rm = flask_client.get('/api/palette/relations/session/aaaaaaaa-rm').get_json()
    ask = flask_client.get('/api/palette/relations/session/bbbbbbbb-ask').get_json()
    calm = flask_client.get('/api/palette/relations/session/cccccccc-calm').get_json()
    flask_client.get('/api/palette/home')

    assert rm['waiting'] == {'kind': 'approval', 'text': 'rm -rf build/',
                             'destructive': True, 'since': rm['waiting']['since']}
    assert ask['waiting'] == {'kind': 'question', 'text': 'Ship it?',
                              'destructive': False,
                              'since': ask['waiting']['since']}
    assert 'waiting' not in calm
    assert _rels(rm) == [('Belongs to', 'F146')]
    assert _dump_hash(tmp_db) == before
    assert _palette_methods(flask_client) <= {'GET', 'HEAD', 'OPTIONS'}
