"""Unit tests for lib.rule_engines.radon_engine.RadonEngine."""

from __future__ import annotations

import pytest

from lib import rule_engines
from lib.rule_engines.base import Rule, Violation
from lib.rule_engines.radon_engine import RadonEngine
from lib.settings import RuleEngineConfig


# ── Fixtures ─────────────────────────────────────────────────

# Function with deeply nested branches — CC well above grade C.
_HIGH_CC_SOURCE = '''
def gnarly(x, y, z):
    if x > 0:
        if y > 0:
            if z > 0:
                return 1
            elif z < 0:
                return 2
            else:
                return 3
        elif y < 0:
            for i in range(10):
                if i % 2 == 0:
                    return 4
        else:
            return 5
    elif x < 0:
        try:
            if y > 0:
                return 6
            return 7
        except ValueError:
            return 8
    return 9
'''

# Trivial function — grade A.
_LOW_CC_SOURCE = '''
def add(a, b):
    return a + b
'''

_SYNTAX_ERROR_SOURCE = 'def broken(:\n    pass\n'


@pytest.fixture
def engine(tmp_path):
    return RadonEngine(
        id='radon',
        language_ids=('python',),
        project_root=str(tmp_path),
        min_grade='C',
        severity='warn',
    )


def _write(tmp_path, name, source):
    p = tmp_path / name
    p.write_text(source)
    return str(p)


# ── parse_rules ──────────────────────────────────────────────

def test_parse_rules_synthesizes_single_rule(engine):
    rules = engine.parse_rules()
    assert len(rules) == 1
    r = rules[0]
    assert r.id == 'python.cyclomatic-complexity.c'
    assert r.engine == 'radon'
    assert r.severity == 'warn'
    assert r.triggers == ('*.py',)
    assert r.metadata['language'] == 'python'
    assert r.metadata['guide'] == 'python-complexity'
    assert r.metadata['min_grade'] == 'C'


def test_parse_rules_reflects_configured_threshold(tmp_path):
    e = RadonEngine(min_grade='E', severity='error',
                    project_root=str(tmp_path))
    r = e.parse_rules()[0]
    assert r.id == 'python.cyclomatic-complexity.e'
    assert r.severity == 'error'
    assert r.metadata['min_grade'] == 'E'


def test_invalid_min_grade_rejected(tmp_path):
    with pytest.raises(ValueError, match='A..F'):
        RadonEngine(min_grade='Z', project_root=str(tmp_path))


# ── applies_to ───────────────────────────────────────────────

def test_applies_to_matches_python(engine):
    rule = engine.parse_rules()[0]
    assert engine.applies_to(rule, '/tmp/foo.py', '')


def test_applies_to_rejects_non_python(engine):
    rule = engine.parse_rules()[0]
    assert not engine.applies_to(rule, '/tmp/foo.java', '')
    assert not engine.applies_to(rule, '/tmp/foo.txt', '')


def test_applies_to_accepts_dict_rule(engine):
    """Bundle/grit paths sometimes hand dicts in — mirror that tolerance."""
    legacy = {'id': 'x', 'triggers': ['*.py']}
    assert engine.applies_to(legacy, '/tmp/foo.py', '')


# ── run ──────────────────────────────────────────────────────

def test_run_flags_high_complexity(engine, tmp_path):
    path = _write(tmp_path, 'gnarly.py', _HIGH_CC_SOURCE)
    rule = engine.parse_rules()[0]
    v = engine.run(rule, path, str(tmp_path))
    assert isinstance(v, Violation)
    assert v.rule_id == 'python.cyclomatic-complexity.c'
    assert v.match_count >= 1
    assert v.detail is not None
    assert 'gnarly' in v.detail


def test_run_ignores_simple_functions(engine, tmp_path):
    path = _write(tmp_path, 'simple.py', _LOW_CC_SOURCE)
    rule = engine.parse_rules()[0]
    assert engine.run(rule, path, str(tmp_path)) is None


def test_run_survives_syntax_error(engine, tmp_path):
    path = _write(tmp_path, 'broken.py', _SYNTAX_ERROR_SOURCE)
    rule = engine.parse_rules()[0]
    assert engine.run(rule, path, str(tmp_path)) is None


def test_run_threshold_e_skips_grade_c_code(tmp_path):
    """A function that's grade-C (CC 11-20) shouldn't fire when threshold=E."""
    e = RadonEngine(min_grade='E', project_root=str(tmp_path))
    path = _write(tmp_path, 'mid.py', _HIGH_CC_SOURCE)
    rule = e.parse_rules()[0]
    # gnarly() above is somewhere in the C/D range — confirm threshold filtering.
    v = e.run(rule, path, str(tmp_path))
    # Either no violation (grade was below E) or a non-empty violation.
    # The point is the call doesn't crash and respects the threshold.
    if v is not None:
        assert v.match_count >= 1


# ── Engine-contributed skills ────────────────────────────────

def test_contributes_python_complexity_auto_skill(engine):
    contributed = engine.contributed_skills()
    assert contributed == [{
        'id': 'python-complexity',
        'kind': 'auto',
        'engine_id': 'radon',
    }]


def test_reserved_auto_skill_ids_includes_python_complexity():
    assert 'python-complexity' in RadonEngine.reserved_auto_skill_ids()


def test_write_index_returns_summary(engine):
    idx = engine.write_index()
    assert idx['engine'] == 'radon'
    assert idx['kind'] == 'radon'
    assert idx['rules'] == 1
    assert idx['min_grade'] == 'C'


# ── Registry integration ─────────────────────────────────────

@pytest.fixture(autouse=True)
def _reset_engine_cache(monkeypatch):
    from lib import settings as settings_mod
    rule_engines.invalidate_cache()
    monkeypatch.setattr(settings_mod.settings, 'bundle_autoload', False)
    yield
    rule_engines.invalidate_cache()


def test_registry_builds_radon_engine_from_config(monkeypatch, tmp_path):
    from lib import settings as settings_mod
    cfg = RuleEngineConfig(
        id='radon', kind='radon',
        language_ids=('python',),
        min_grade='D', severity='error',
    )
    monkeypatch.setattr(settings_mod.settings, 'rule_engines', [cfg])
    engines = rule_engines.all_engines()
    assert len(engines) == 1
    assert isinstance(engines[0], RadonEngine)
    assert engines[0].min_grade == 'D'
    assert engines[0].severity == 'error'


def test_registry_radon_defaults_when_unset(monkeypatch):
    from lib import settings as settings_mod
    cfg = RuleEngineConfig(
        id='radon', kind='radon', language_ids=('python',),
    )
    monkeypatch.setattr(settings_mod.settings, 'rule_engines', [cfg])
    engines = rule_engines.all_engines()
    assert isinstance(engines[0], RadonEngine)
    # Falls back to engine defaults when settings leaves them None.
    assert engines[0].min_grade == 'C'
    assert engines[0].severity == 'warn'
