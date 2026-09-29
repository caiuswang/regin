"""Characterization of `_set_frontmatter_description` and
`_attached_rule_bundles_for_pattern` (web/blueprints/patterns/_helpers.py)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from lib.rule_engines.base import Rule
from web.blueprints.patterns import _helpers as h


@pytest.mark.parametrize('content,desc,expected', [
    ('Body only', 'New  desc', '---\ndescription: "New desc"\n---\n\nBody only'),
    ('Body only', '  ', 'Body only'),
    ('---\ntitle: x', 'D', '---\ntitle: x'),
    ('---\ntitle: "T"\ndescription: old\n  more\nprocedure: p\n---\nBody',
     'Fresh "q" \\ d',
     '---\ntitle: "T"\ndescription: "Fresh \\"q\\" \\\\ d"\nprocedure: p\n---\nBody'),
    ('---\ntitle: "T"\ndescription: old\nprocedure: p\n---\nBody', '',
     '---\ntitle: "T"\nprocedure: p\n---\nBody'),
    ('---\ntitle: "T"\nprocedure: p\n---\nBody', '',
     '---\ntitle: "T"\nprocedure: p\n---\nBody'),
    ('---\ntitle: "T"\n  cont\nprocedure: p\n---\nBody', 'Added',
     '---\ntitle: "T"\n  cont\ndescription: "Added"\nprocedure: p\n---\nBody'),
    ('---\nprocedure: p\n---\nBody', 'Top',
     '---\ndescription: "Top"\nprocedure: p\n---\nBody'),
    ('---\n\n\nprocedure: p\n---\nBody', 'Top',
     '---\ndescription: "Top"\nprocedure: p\n---\nBody'),
    ('---procedure: p\n---\nBody', 'Inline',
     '---description: "Inline"\nprocedure: p\n---\nBody'),
    ('---\ndescription: first\ntitle: t\n---\nB', 'X\nY',
     '---\ndescription: "X Y"\ntitle: t\n---\nB'),
])
def test_set_frontmatter_description(content, desc, expected):
    assert h._set_frontmatter_description(content, desc) == expected


def _rule(rid, engine, guide=None):
    meta = {'checker': f'c-{rid}', 'category': 'cat'}
    if guide:
        meta['guide'] = guide
    return Rule(id=rid, engine=engine, summary=f's-{rid}', severity='warn',
                triggers=('**/*.py',), source_file='f.yaml', metadata=meta)


class _Engine:
    def __init__(self, eid, kind, rules, description=None):
        self.id = eid
        self.kind = kind
        self._rules = rules
        self.manifest = (SimpleNamespace(description=description)
                         if description is not None else None)

    def parse_rules(self):
        return iter(self._rules)


@pytest.fixture
def engines(monkeypatch):
    monkeypatch.setattr(h.rule_engines, 'all_engines', lambda: [
        _Engine('grit', 'grit', [_rule('g', 'grit', guide='pat')]),
        _Engine('radon', 'radon', [_rule('r1', 'radon', guide='pat'),
                                   _rule('r2', 'radon', guide='other')],
                description='  '),
        _Engine('pat', 'bundle', [_rule('b1', 'pat'), _rule('b2', 'pat')],
                description='  Own bundle.  '),
        _Engine('unrelated', 'bundle', [_rule('u', 'unrelated')]),
    ])
    monkeypatch.setattr(h.engine_rule_disable, 'disabled_ids',
                        lambda eid: {'b2'} if eid == 'pat' else set())


def _rule_dict(rid, engine, disabled=False):
    return {'id': rid, 'summary': f's-{rid}', 'severity': 'warn',
            'engine': engine, 'source_file': 'f.yaml', 'checker': f'c-{rid}',
            'category': 'cat', 'check_kind': None, 'wcag_ref': None,
            'triggers': ['**/*.py'], 'disabled': disabled}


def _hint(eid):
    return (f'regin rules run --engine {eid} --rule <rule-id> '
            '--repo <repo-root> --file <relative-path>')


def test_attached_rule_bundles(engines):
    assert h._attached_rule_bundles_for_pattern('pat') == [
        {'engine_id': 'radon', 'engine_kind': 'radon',
         'title': 'radon rule bundle',
         'description': 'Portable rule bundle attached to this pattern skill.',
         'invocation_hint': _hint('radon'),
         'rules': [_rule_dict('r1', 'radon')]},
        {'engine_id': 'pat', 'engine_kind': 'bundle',
         'title': 'pat rule bundle', 'description': 'Own bundle.',
         'invocation_hint': _hint('pat'),
         'rules': [_rule_dict('b1', 'pat'), _rule_dict('b2', 'pat', True)]},
    ]


@pytest.mark.parametrize('pid', [None, '', 'nothing'])
def test_attached_rule_bundles_empty(engines, pid):
    assert h._attached_rule_bundles_for_pattern(pid) == []
