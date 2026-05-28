"""Tests for lib.rule_engines registry: settings-driven engines + bundle discovery."""

from __future__ import annotations

import pytest

from lib import rule_engines
from lib.rule_engines.grit import GritEngine
from lib.settings import RuleEngineConfig


@pytest.fixture(autouse=True)
def _reset_cache(monkeypatch):
    """Each test gets a clean engine cache and bundle auto-discovery
    disabled by default. Tests that exercise discovery flip
    `bundle_autoload` back on explicitly.
    """
    from lib import settings as settings_mod
    rule_engines.invalidate_cache()
    monkeypatch.setattr(settings_mod.settings, 'bundle_autoload', False)
    yield
    rule_engines.invalidate_cache()


# ── rule_engines list takes precedence ───────────────────────

def test_configured_rule_engines_win(monkeypatch, tmp_path):
    from lib import settings as settings_mod
    cfg = RuleEngineConfig(
        id='grit', kind='grit',
        grit_dir=tmp_path, language_ids=('java',),
    )
    monkeypatch.setattr(settings_mod.settings, 'rule_engines', [cfg])
    engines = rule_engines.all_engines()
    assert len(engines) == 1
    assert engines[0].id == 'grit'
    assert isinstance(engines[0], GritEngine)
    assert engines[0].grit_dir == str(tmp_path)


def test_disabled_engines_are_skipped(monkeypatch, tmp_path):
    from lib import settings as settings_mod
    cfg = RuleEngineConfig(
        id='grit', kind='grit', enabled=False,
        grit_dir=tmp_path, language_ids=('java',),
    )
    monkeypatch.setattr(settings_mod.settings, 'rule_engines', [cfg])
    assert rule_engines.all_engines() == []


def test_unknown_kind_raises(monkeypatch, tmp_path):
    from lib import settings as settings_mod
    cfg = RuleEngineConfig(id='weird', kind='not-a-real-kind',
                           grit_dir=tmp_path)
    monkeypatch.setattr(settings_mod.settings, 'rule_engines', [cfg])
    with pytest.raises(ValueError, match='unknown rule engine kind'):
        rule_engines.all_engines()


def test_get_raises_on_unknown(monkeypatch):
    from lib import settings as settings_mod
    monkeypatch.setattr(settings_mod.settings, 'rule_engines', [])
    with pytest.raises(KeyError):
        rule_engines.get('grit')


# ── Skill registry integration ──────────────────────────────

def test_skill_registry_hides_grit_rules_when_no_engines(monkeypatch):
    """With zero engines configured, the grit-rules auto-skill disappears
    from the registry — a generic regin deployment surfaces no lint chrome
    in Managed Skills."""
    from lib import settings as settings_mod
    from lib.skills import skill_registry

    monkeypatch.setattr(settings_mod.settings, 'rule_engines', [])
    rule_engines.invalidate_cache()

    ids = skill_registry.all_ids()
    assert 'grit-rules' not in ids


def test_skill_registry_exposes_grit_rules_when_engine_configured(
        monkeypatch, tmp_path):
    from lib import settings as settings_mod
    from lib.skills import skill_registry

    cfg = RuleEngineConfig(
        id='grit', kind='grit',
        grit_dir=tmp_path, language_ids=('java',),
    )
    monkeypatch.setattr(settings_mod.settings, 'rule_engines', [cfg])
    rule_engines.invalidate_cache()

    ids = skill_registry.all_ids()
    assert 'grit-rules' in ids


# ── Bundle auto-discovery ───────────────────────────────────

def _write_bundle(patterns_dir, slug):
    import yaml
    bundle_root = patterns_dir / slug
    bundle_root.mkdir(parents=True)
    (bundle_root / 'regin-bundle.yaml').write_text(yaml.safe_dump({
        'schema': 'rule-bundle/v1',
        'id': slug,
        'language_ids': ['python'],
        'rules_dir': 'rules',
        'runner': {'kind': 'python', 'entry': 'bin/runner.py'},
    }))
    (bundle_root / 'rules').mkdir()
    (bundle_root / 'rules' / 'r.yaml').write_text(yaml.safe_dump([{
        'id': f'{slug}-rule', 'triggers': ['**/*.py'],
    }]))
    (bundle_root / 'bin').mkdir()
    (bundle_root / 'bin' / 'runner.py').write_text(
        'import sys,json\nprint(json.dumps({"matches":0,"details":[]}))\n'
    )
    return bundle_root


def test_bundle_autoload_picks_up_bundles(monkeypatch, tmp_path):
    from lib import settings as settings_mod
    from lib.rule_engines.bundle import BundleEngine

    patterns_dir = tmp_path / 'patterns'
    _write_bundle(patterns_dir, 'demo')
    monkeypatch.setattr(settings_mod.settings, 'rule_engines', [])
    monkeypatch.setattr(settings_mod.settings, 'patterns_dir', patterns_dir)
    monkeypatch.setattr(settings_mod.settings, 'bundle_autoload', True)

    engines = rule_engines.all_engines()
    assert [e.id for e in engines] == ['demo']
    assert isinstance(engines[0], BundleEngine)


def test_bundle_autoload_skipped_when_disabled(monkeypatch, tmp_path):
    from lib import settings as settings_mod
    patterns_dir = tmp_path / 'patterns'
    _write_bundle(patterns_dir, 'demo')
    monkeypatch.setattr(settings_mod.settings, 'rule_engines', [])
    monkeypatch.setattr(settings_mod.settings, 'patterns_dir', patterns_dir)
    monkeypatch.setattr(settings_mod.settings, 'bundle_autoload', False)
    assert rule_engines.all_engines() == []


def test_explicit_settings_win_over_discovered(monkeypatch, tmp_path):
    """An explicit rule_engines entry with the same id as a discovered
    bundle takes precedence — discovery never overwrites explicit config."""
    from lib import settings as settings_mod
    from lib.rule_engines.grit import GritEngine

    patterns_dir = tmp_path / 'patterns'
    _write_bundle(patterns_dir, 'shared-id')
    monkeypatch.setattr(settings_mod.settings, 'patterns_dir', patterns_dir)
    monkeypatch.setattr(settings_mod.settings, 'bundle_autoload', True)

    explicit_grit_dir = tmp_path / 'g'
    explicit_grit_dir.mkdir()
    cfg = RuleEngineConfig(
        id='shared-id', kind='grit',
        grit_dir=explicit_grit_dir, language_ids=('java',),
    )
    monkeypatch.setattr(settings_mod.settings, 'rule_engines', [cfg])

    engines = rule_engines.all_engines()
    assert len(engines) == 1
    assert engines[0].id == 'shared-id'
    assert isinstance(engines[0], GritEngine)  # explicit grit, not bundle


def test_explicit_bundle_kind_loads_via_settings(monkeypatch, tmp_path):
    """Users can pin a bundle through settings.rule_engines with kind='bundle'
    even when bundle_autoload is off (e.g. bundle lives outside patterns_dir)."""
    from lib import settings as settings_mod
    from lib.rule_engines.bundle import BundleEngine

    bundle_root = tmp_path / 'standalone-bundle'
    _write_bundle(tmp_path, 'standalone-bundle')
    cfg = RuleEngineConfig(
        id='standalone-bundle', kind='bundle',
        bundle_root=bundle_root,
        language_ids=('python',),
    )
    monkeypatch.setattr(settings_mod.settings, 'rule_engines', [cfg])
    monkeypatch.setattr(settings_mod.settings, 'bundle_autoload', False)

    engines = rule_engines.all_engines()
    assert len(engines) == 1
    assert isinstance(engines[0], BundleEngine)
    assert engines[0].id == 'standalone-bundle'


def test_dangling_bundle_root_is_skipped_not_fatal(monkeypatch, tmp_path):
    """A configured kind='bundle' engine whose bundle_root no longer exists
    (e.g. its pattern was deleted) must be skipped, not crash all_engines() —
    otherwise the rules / pattern-detail / settings pages 500."""
    from lib import settings as settings_mod
    from lib.rule_engines.grit import GritEngine

    healthy = RuleEngineConfig(
        id='grit', kind='grit',
        grit_dir=tmp_path / 'g', language_ids=('java',),
    )
    (tmp_path / 'g').mkdir()
    dangling = RuleEngineConfig(
        id='frontend-style-convention', kind='bundle',
        bundle_root=tmp_path / 'gone',  # never created
    )
    monkeypatch.setattr(settings_mod.settings, 'rule_engines', [healthy, dangling])

    engines = rule_engines.all_engines()
    assert [e.id for e in engines] == ['grit']
    assert isinstance(engines[0], GritEngine)
    # get() on the missing bundle is a clean KeyError, not a 500-y ValueError.
    with pytest.raises(KeyError):
        rule_engines.get('frontend-style-convention')
