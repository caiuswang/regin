"""Characterization of `/api/skills` row shaping and the
`include_deployments` grouping (web/blueprints/skills.py)."""

from __future__ import annotations

import pytest

_DEPS = [
    {'pattern_slug': 'p1', 'scope': 'project', 'project_id': 2,
     'project_name': 'zeta', 'project_path': '/z'},
    {'pattern_slug': 'p1', 'scope': 'user', 'project_id': None},
    {'pattern_slug': 'p2', 'scope': 'project', 'project_id': 1,
     'project_name': 'alpha', 'project_path': '/a'},
    {'pattern_slug': 'p2', 'scope': 'project', 'project_id': 2,
     'project_name': 'zeta', 'project_path': '/z'},
    {'pattern_slug': 'p3', 'scope': 'project', 'project_id': 3,
     'project_name': None, 'project_path': '/n'},
]


@pytest.fixture
def stubbed(monkeypatch):
    from web.blueprints import skills as sk
    monkeypatch.setattr(sk.skill_sync, 'list_states', lambda: iter([
        ('p1', 'pattern', '/src/p1', '/dep/p1', sk.skill_sync.STATE_IN_SYNC),
        ('auto1', 'auto', '/src/a', '/dep/a', 'drifted'),
    ]))
    monkeypatch.setattr(sk.skill_registry, 'get',
                        lambda sid: {'procedure_id': f'proc-{sid}'})
    monkeypatch.setattr(sk.pattern_deployments, 'list_deployments',
                        lambda: [dict(d) for d in _DEPS])
    monkeypatch.setattr(sk, 'active_provider_skill_paths', lambda: {'p': 1})
    monkeypatch.setattr(sk, 'enabled_provider_skill_paths', lambda: [{'p': 1}])


def _get(client, qs=''):
    return client.get(f'/api/skills{qs}').get_json()


def test_skills_rows(flask_client, tmp_db, stubbed):
    body = _get(flask_client)
    assert body == {
        'rows': [
            {'id': 'p1', 'type': 'pattern', 'state': 'in_sync',
             'source': '/src/p1', 'deployed': '/dep/p1',
             'href': '/patterns/proc-p1'},
            {'id': 'auto1', 'type': 'auto', 'state': 'drifted',
             'source': '/src/a', 'deployed': '/dep/a', 'href': '/skills/auto1'},
        ],
        'by_type': {
            'auto': [{'id': 'auto1', 'type': 'auto', 'state': 'drifted',
                      'source': '/src/a', 'deployed': '/dep/a',
                      'href': '/skills/auto1'}],
            'pattern': [{'id': 'p1', 'type': 'pattern', 'state': 'in_sync',
                         'source': '/src/p1', 'deployed': '/dep/p1',
                         'href': '/patterns/proc-p1'}],
        },
        'drift_count': 1,
        'total': 2,
        'provider': {'p': 1},
        'enabled_providers': [{'p': 1}],
    }


@pytest.mark.parametrize('qs,present', [
    ('', False), ('?include_deployments=0', False),
    ('?include_deployments=1', True), ('?include_deployments=true', True),
])
def test_skills_deployments_toggle(flask_client, tmp_db, stubbed, qs, present):
    assert ('deployments' in _get(flask_client, qs)) is present


def test_skills_deployments_grouping(flask_client, tmp_db, stubbed):
    body = _get(flask_client, '?include_deployments=1')
    assert body['deployments'] == _DEPS
    assert body['deployments_by_skill'] == {
        'p1': _DEPS[0:2], 'p2': _DEPS[2:4], 'p3': _DEPS[4:5]}
    assert body['deployments_by_project'] == [
        {'project_id': 3, 'project_name': None, 'project_path': '/n',
         'items': [_DEPS[4]]},
        {'project_id': 1, 'project_name': 'alpha', 'project_path': '/a',
         'items': [_DEPS[2]]},
        {'project_id': 2, 'project_name': 'zeta', 'project_path': '/z',
         'items': [_DEPS[0], _DEPS[3]]},
    ]
