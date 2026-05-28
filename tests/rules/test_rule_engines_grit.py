"""Unit tests for lib.rule_engines.grit.GritEngine."""

from __future__ import annotations

import os
import subprocess
from unittest.mock import patch

import pytest

from lib import rule_engines
from lib.rule_engines.base import Rule
from lib.rule_engines.grit import GritEngine


# ── Fixtures ─────────────────────────────────────────────────

@pytest.fixture
def grit_dir(tmp_path):
    """Create a .grit/patterns/java/ layout with one rule file."""
    patterns = tmp_path / 'patterns' / 'java'
    patterns.mkdir(parents=True)
    (patterns / 'sample.grit').write_text(
        '// @rule id=require_remote_service\n'
        '// @rule layer=service-impl\n'
        '// @rule triggers=*ServiceImpl.java,@RemoteService\n'
        '// @rule severity=warn\n'
        '// @rule guide=entity-pattern\n'
        '// @rule summary=ServiceImpl must carry @RemoteService\n'
        'pattern require_remote_service() {\n'
        '  // body\n'
        '}\n'
    )
    return str(tmp_path)


@pytest.fixture
def engine(grit_dir):
    return GritEngine(
        grit_dir=grit_dir,
        language_ids=('java',),
        project_root=grit_dir,
    )


# ── parse_rules ──────────────────────────────────────────────

def test_parse_rules_reads_grit_files(engine):
    rules = engine.parse_rules()
    assert len(rules) == 1
    r = rules[0]
    assert r.id == 'require_remote_service'
    assert r.engine == 'grit'
    assert r.summary == 'ServiceImpl must carry @RemoteService'
    assert r.severity == 'warn'
    assert r.metadata['layer'] == 'service-impl'
    assert r.metadata['guide'] == 'entity-pattern'
    assert r.metadata['language'] == 'java'


def test_parse_rules_empty_dir(tmp_path):
    e = GritEngine(grit_dir=str(tmp_path), language_ids=('java',),
                   project_root=str(tmp_path))
    assert e.parse_rules() == []


# ── patterns_dir ─────────────────────────────────────────────

def test_patterns_dir_nests_per_language(engine, grit_dir):
    assert engine.patterns_dir('java') == os.path.join(grit_dir, 'patterns', 'java')


# ── applies_to ───────────────────────────────────────────────

def test_applies_to_matches_java_glob(engine):
    rule = engine.parse_rules()[0]
    # Has filename glob *ServiceImpl.java AND content trigger @RemoteService;
    # AND semantics: both must match.
    content_with_ann = '@RemoteService\npublic class FooServiceImpl {}'
    assert engine.applies_to(rule, '/repo/FooServiceImpl.java', content_with_ann)


def test_applies_to_rejects_non_java_basename(engine):
    rule = engine.parse_rules()[0]
    content_with_ann = '@RemoteService\nclass Bar {}'
    assert not engine.applies_to(rule, '/repo/Foo.py', content_with_ann)


def test_applies_to_rejects_when_content_trigger_missing(engine):
    rule = engine.parse_rules()[0]
    # Filename glob matches but content trigger does not.
    assert not engine.applies_to(rule, '/repo/FooServiceImpl.java',
                                 'public class FooServiceImpl {}')


def test_applies_to_accepts_dict_rule(engine):
    """Legacy callers hand us `rule.json`-style dicts, not Rule dataclasses."""
    legacy = {
        'id': 'x',
        'triggers': ['*ServiceImpl.java', '@RemoteService'],
    }
    assert engine.applies_to(legacy, '/repo/FooServiceImpl.java',
                             '@RemoteService\nclass X {}')


def test_applies_to_no_triggers_returns_false(engine):
    assert not engine.applies_to({'id': 'empty', 'triggers': []},
                                 '/repo/Foo.java', 'content')


# ── language_extensions ──────────────────────────────────────

def test_language_extensions_for_rule(engine):
    rule = engine.parse_rules()[0]
    assert engine.language_extensions(rule) == ('.java',)


def test_language_extensions_for_legacy_dict_defaults_to_first_language(engine):
    assert engine.language_extensions({'id': 'x'}) == ('.java',)


# ── run ──────────────────────────────────────────────────────

def test_run_returns_violation_when_grit_reports_match(engine, tmp_path):
    rule = engine.parse_rules()[0]
    file_path = str(tmp_path / 'Foo.java')
    open(file_path, 'w').close()

    class _FakeProc:
        stdout = '1 match'
        stderr = ''

    with patch('lib.rule_engines.grit.subprocess.run', return_value=_FakeProc()):
        v = engine.run(rule, file_path, str(tmp_path))
    assert v is not None
    assert v.rule_id == 'require_remote_service'
    assert v.match_count == 1


def test_run_returns_none_when_grit_binary_missing(engine, tmp_path):
    rule = engine.parse_rules()[0]
    file_path = str(tmp_path / 'Foo.java')
    open(file_path, 'w').close()

    def _raise(*_a, **_kw):
        raise FileNotFoundError

    with patch('lib.rule_engines.grit.subprocess.run', side_effect=_raise):
        assert engine.run(rule, file_path, str(tmp_path)) is None


def test_run_returns_none_on_timeout(engine, tmp_path):
    rule = engine.parse_rules()[0]
    file_path = str(tmp_path / 'Foo.java')
    open(file_path, 'w').close()

    def _timeout(*_a, **_kw):
        raise subprocess.TimeoutExpired(cmd='grit', timeout=1)

    with patch('lib.rule_engines.grit.subprocess.run', side_effect=_timeout):
        assert engine.run(rule, file_path, str(tmp_path)) is None


# ── contributed_skills ───────────────────────────────────────

def test_contributed_skills_returns_grit_rules_entry(engine):
    skills = engine.contributed_skills()
    assert len(skills) == 1
    assert skills[0]['id'] == 'grit-rules'
    assert skills[0]['kind'] == 'auto'
    assert skills[0]['engine_id'] == 'grit'


# ── Disk layout ownership ────────────────────────────────────

def test_patterns_dir_drives_parse_path(tmp_path):
    """GritEngine.patterns_dir is the sole place the patterns/<lang>/
    layout is assembled. Parse should work against a freshly-instantiated
    engine pointed at any directory."""
    e = GritEngine(grit_dir=str(tmp_path), language_ids=('java',),
                   project_root=str(tmp_path))
    patterns = tmp_path / 'patterns' / 'java'
    patterns.mkdir(parents=True)
    (patterns / 'a.grit').write_text(
        '// @rule id=a\n'
        '// @rule layer=entity\n'
        '// @rule triggers=*Entity.java\n'
        '// @rule severity=warn\n'
        '// @rule guide=x\n'
        '// @rule summary=sample\n'
        'pattern a() { `foo` }\n'
    )
    assert e.patterns_dir('java') == str(patterns)
    rules = e.parse_rules()
    assert [r.id for r in rules] == ['a']


# ── Registry ─────────────────────────────────────────────────

def test_registry_get_grit_returns_engine_instance(configured_grit_engine):
    engine = rule_engines.get('grit')
    assert isinstance(engine, GritEngine)
    assert engine.id == 'grit'


def test_registry_get_unknown_raises():
    with pytest.raises(KeyError):
        rule_engines.get('no-such-engine')


def test_registry_all_engines_includes_grit(configured_grit_engine):
    engines = rule_engines.all_engines()
    assert any(e.id == 'grit' for e in engines)
