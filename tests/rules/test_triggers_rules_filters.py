"""Characterization of the `/api/triggers/rules` query parsing and row
filters (web/blueprints/rules/triggers.py)."""

from __future__ import annotations

import pytest
from werkzeug.datastructures import MultiDict

from web.blueprints.rules import triggers as t


class _Req:
    def __init__(self, **args):
        self.args = MultiDict(args)


def test_parse_defaults():
    p = t._parse_triggers_rules_params(_Req(), '7D')
    assert p == t._TriggersRulesParams(
        range_str='7d', search='', sev_filter=set(), eng_filter=set(),
        status_filter='all', marks_only=False, sort_by='rate')


def test_parse_explicit_values():
    p = t._parse_triggers_rules_params(_Req(
        range='30D', search='  Foo Bar ', severity='error,,warn',
        engine='grit,radon', status='NOISY', marks='1', sort='Fires'), '7d')
    assert p == t._TriggersRulesParams(
        range_str='30d', search='foo bar', sev_filter={'error', 'warn'},
        eng_filter={'grit', 'radon'}, status_filter='noisy', marks_only=True,
        sort_by='fires')


_ROW = {'rule_id': 'Py.Rule-X', 'severity': 'warn', 'source': 'grit',
        'status': 'noisy', 'suppressed_count': 2}


def _params(**over):
    base = dict(range_str='7d', search='', sev_filter=set(), eng_filter=set(),
                status_filter='all', marks_only=False, sort_by='rate')
    base.update(over)
    return t._TriggersRulesParams(**base)


@pytest.mark.parametrize('row_over,param_over,expected', [
    ({}, {}, True),
    ({}, {'search': 'rule-x'}, True),
    ({}, {'search': 'nope'}, False),
    ({}, {'sev_filter': {'warn'}}, True),
    ({}, {'sev_filter': {'error'}}, False),
    ({'severity': None}, {'sev_filter': {''}}, True),
    ({}, {'eng_filter': {'grit'}}, True),
    ({}, {'eng_filter': {'radon'}}, False),
    ({'source': None}, {'eng_filter': {'grit'}}, False),
    ({}, {'status_filter': 'noisy'}, True),
    ({}, {'status_filter': 'dead'}, False),
    ({}, {'marks_only': True}, True),
    ({'suppressed_count': 0}, {'marks_only': True}, False),
])
def test_passes_filters(row_over, param_over, expected):
    row = dict(_ROW, **row_over)
    assert t._passes_triggers_rules_filters(row, _params(**param_over)) is expected
