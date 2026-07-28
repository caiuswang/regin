"""Repo-shipped rule bundles: discovery, the trust gate, and repo scoping.

Covers the path where a rule pack lives in `<repo>/.regin/rules/` instead of
the user's global patterns dir — discovered for every registered repo, scoped
to that repo, and executed only after an explicit trust decision.
"""

from __future__ import annotations

import pytest
import yaml

from lib import rule_engines
from lib.rule_engines import bundle_trust
from lib.rule_engines.bundle import BundleEngine

RUNNER_SRC = (
    'import json,sys\n'
    'payload = json.load(sys.stdin)\n'
    'print(json.dumps({"matches": 1, "details": ["hit"]}))\n'
)
CHECKER_SRC = 'def run(**kw):\n    return {"matches": 1, "details": ["hit"]}\n'


def _write_repo_bundle(repo_root, slug='conventions', checker_src=CHECKER_SRC):
    """Create `<repo>/.regin/rules/<slug>/` with a manifest, rule, and runner."""
    bundle_root = repo_root / '.regin' / 'rules' / slug
    (bundle_root / 'rules').mkdir(parents=True)
    (bundle_root / 'checkers').mkdir(parents=True)
    (bundle_root / 'bin').mkdir(parents=True)
    (bundle_root / 'regin-bundle.yaml').write_text(yaml.safe_dump({
        'schema': 'rule-bundle/v1',
        'id': slug,
        'language_ids': ['python'],
        'rules_dir': 'rules',
        'checkers_dir': 'checkers',
        'runner': {'kind': 'python', 'entry': 'bin/runner.py'},
    }))
    (bundle_root / 'rules' / 'r.yaml').write_text(yaml.safe_dump([{
        'id': f'{slug}-rule',
        'checker': 'demo',
        'summary': 'demo rule',
        'triggers': ['**/*.py'],
    }]))
    (bundle_root / 'bin' / 'runner.py').write_text(RUNNER_SRC)
    (bundle_root / 'checkers' / 'demo.py').write_text(checker_src)
    return bundle_root


@pytest.fixture
def repo(tmp_path):
    """A registered repo that ships one bundle, with an isolated trust store."""
    repo_root = tmp_path / 'myrepo'
    repo_root.mkdir()
    _write_repo_bundle(repo_root)
    return repo_root


@pytest.fixture(autouse=True)
def _isolate(monkeypatch, tmp_path, repo):
    """Central discovery off, repo discovery on, trust store in tmp."""
    from lib import settings as settings_mod

    data_dir = tmp_path / 'data'
    data_dir.mkdir()
    monkeypatch.setattr(settings_mod.settings, 'rule_engines', [])
    monkeypatch.setattr(settings_mod.settings, 'bundle_autoload', False)
    monkeypatch.setattr(settings_mod.settings, 'repo_bundle_autoload', True)
    monkeypatch.setattr(settings_mod.settings, 'data_dir', data_dir)
    monkeypatch.setattr(
        rule_engines, '_registered_repos', lambda: [('myrepo', str(repo))],
    )
    rule_engines.invalidate_cache()
    yield
    rule_engines.invalidate_cache()


# ── Discovery ──────────────────────────────────────────────────────────


def test_repo_bundle_is_discovered_and_namespaced(repo):
    engines = rule_engines.all_engines()
    assert [e.id for e in engines] == ['myrepo:conventions']
    engine = engines[0]
    assert isinstance(engine, BundleEngine)
    assert engine.origin_repo_name == 'myrepo'
    assert engine.origin_repo_path == str(repo.resolve())


def test_repo_bundle_rules_parse_without_trust():
    """Discovery must list rules so the UI can show what it would enforce."""
    engine = rule_engines.get('myrepo:conventions')
    assert [r.id for r in engine.parse_rules()] == ['conventions-rule']


def test_triggers_are_relative_to_the_repo_not_the_bundle(repo):
    """A `server/**/*.ts`-style trigger must match `<repo>/server/x.ts`.

    Rule paths in a repo-shipped bundle are written against the repo. If the
    engine anchored them at the bundle root (`.regin/rules/<id>/`) every path
    would come out as `../../../server/x.ts` and such triggers would silently
    never fire.
    """
    bundle_root = repo / '.regin' / 'rules' / 'conventions'
    (bundle_root / 'rules' / 'r.yaml').write_text(yaml.safe_dump([{
        'id': 'server-only', 'checker': 'demo',
        'triggers': ['server/**/*.py'],
    }]))
    target = repo / 'server' / 'relay.py'
    target.parent.mkdir()
    target.write_text('x = 1\n')

    engine = rule_engines.get('myrepo:conventions')
    assert engine.project_root == str(repo.resolve())
    applicable = engine.applicable_rules(str(target), 'x = 1\n')
    assert [r.id for _, r, _ in applicable.items] == ['server-only']


def test_repo_discovery_can_be_disabled(monkeypatch):
    from lib import settings as settings_mod
    monkeypatch.setattr(settings_mod.settings, 'repo_bundle_autoload', False)
    assert rule_engines.all_engines() == []


def test_two_repos_may_ship_same_bundle_id(monkeypatch, tmp_path):
    other = tmp_path / 'other'
    other.mkdir()
    _write_repo_bundle(other)
    monkeypatch.setattr(
        rule_engines, '_registered_repos',
        lambda: [('myrepo', str(tmp_path / 'myrepo')), ('other', str(other))],
    )
    assert sorted(e.id for e in rule_engines.all_engines()) == [
        'myrepo:conventions', 'other:conventions',
    ]


# ── Trust gate ─────────────────────────────────────────────────────────


def test_untrusted_bundle_never_executes(repo, tmp_path):
    engine = rule_engines.get('myrepo:conventions')
    assert engine.trusted is False
    target = repo / 'a.py'
    target.write_text('x = 1\n')
    rule = engine.parse_rules()[0]
    assert engine.run(rule, str(target), str(repo)) is None


def test_trusted_bundle_executes(repo):
    engine = rule_engines.get('myrepo:conventions')
    bundle_trust.trust(
        str(repo), 'conventions',
        bundle_trust.fingerprint(engine.bundle_root, engine.manifest),
    )
    rule_engines.invalidate_cache()
    engine = rule_engines.get('myrepo:conventions')
    assert engine.trusted is True

    target = repo / 'a.py'
    target.write_text('x = 1\n')
    violation = engine.run(engine.parse_rules()[0], str(target), str(repo))
    assert violation is not None
    assert violation.match_count == 1


def test_editing_checker_code_revokes_trust(repo):
    engine = rule_engines.get('myrepo:conventions')
    bundle_trust.trust(
        str(repo), 'conventions',
        bundle_trust.fingerprint(engine.bundle_root, engine.manifest),
    )
    checker = repo / '.regin' / 'rules' / 'conventions' / 'checkers' / 'demo.py'
    checker.write_text(CHECKER_SRC + '# something new\n')

    engine = rule_engines.get('myrepo:conventions')
    assert engine.trusted is False
    state = bundle_trust.describe(
        str(repo), 'conventions',
        bundle_trust.fingerprint(engine.bundle_root, engine.manifest),
    )
    assert state['known'] is True
    assert state['code_changed'] is True


def test_editing_rule_yaml_keeps_trust(repo):
    """Rule data is not code — tightening a threshold must not re-prompt."""
    engine = rule_engines.get('myrepo:conventions')
    bundle_trust.trust(
        str(repo), 'conventions',
        bundle_trust.fingerprint(engine.bundle_root, engine.manifest),
    )
    rule_file = repo / '.regin' / 'rules' / 'conventions' / 'rules' / 'r.yaml'
    rule_file.write_text(yaml.safe_dump([{
        'id': 'conventions-rule', 'checker': 'demo',
        'summary': 'demo rule', 'severity': 'error',
        'triggers': ['**/*.py'],
    }]))

    assert rule_engines.get('myrepo:conventions').trusted is True


def test_untrust_revokes(repo):
    engine = rule_engines.get('myrepo:conventions')
    fp = bundle_trust.fingerprint(engine.bundle_root, engine.manifest)
    bundle_trust.trust(str(repo), 'conventions', fp)
    assert bundle_trust.is_trusted(str(repo), 'conventions', fp) is True

    assert bundle_trust.untrust(str(repo), 'conventions') == 1
    assert bundle_trust.is_trusted(str(repo), 'conventions', fp) is False
    assert rule_engines.get('myrepo:conventions').trusted is False


def test_trust_store_survives_corruption(monkeypatch, tmp_path):
    from lib import settings as settings_mod
    (tmp_path / 'data' / 'trusted_bundles.json').write_text('{not json')
    monkeypatch.setattr(settings_mod.settings, 'data_dir', tmp_path / 'data')
    assert bundle_trust.load() == {}


# ── Repo scoping ───────────────────────────────────────────────────────


def test_engine_scoped_to_its_own_repo(repo, tmp_path):
    from hook_manager.handlers.rule_check import _engine_covers_file

    engine = rule_engines.get('myrepo:conventions')
    inside = repo / 'src' / 'a.py'
    inside.parent.mkdir(parents=True)
    inside.write_text('x = 1\n')
    outside = tmp_path / 'elsewhere' / 'b.py'
    outside.parent.mkdir(parents=True)
    outside.write_text('y = 2\n')

    assert _engine_covers_file(engine, str(inside)) is True
    assert _engine_covers_file(engine, str(outside)) is False


def test_sibling_path_prefix_is_not_inside(repo, tmp_path):
    """`/tmp/myrepo-other/x.py` must not count as inside `/tmp/myrepo`."""
    from hook_manager.handlers.rule_check import _engine_covers_file

    engine = rule_engines.get('myrepo:conventions')
    sibling = tmp_path / 'myrepo-other'
    sibling.mkdir()
    target = sibling / 'x.py'
    target.write_text('z = 3\n')
    assert _engine_covers_file(engine, str(target)) is False


def test_repo_rules_bypass_pattern_scope(repo):
    """A repo bundle's `guide` names the repo's own doc, not a regin pattern.

    Gating it through `pattern_scope` would require every repo to register its
    guides centrally — exactly the coupling repo-local rules avoid — and until
    it did, none of its rules would ever run.
    """
    from hook_manager.handlers.rule_check import _collect_applicable_rules

    bundle_root = repo / '.regin' / 'rules' / 'conventions'
    (bundle_root / 'rules' / 'r.yaml').write_text(yaml.safe_dump([{
        'id': 'guided', 'checker': 'demo',
        'guide': 'a-guide-with-no-deployment',
        'triggers': ['**/*.py'],
    }]))
    target = repo / 'x.py'
    target.write_text('x = 1\n')

    engine = rule_engines.get('myrepo:conventions')
    skipped = []
    applicable, _, _, _ = _collect_applicable_rules(
        [(engine, 'python')], str(target), 'x = 1\n', skipped,
    )
    assert [r.id for _, r, _ in applicable] == ['guided']
    assert skipped == []


def test_repo_engine_runs_against_its_own_repo_root(repo, tmp_path):
    """Checkers that shell out to the project toolchain need the right cwd.

    `effective_root` is resolved once per file from whichever engine answered
    first, so with a central bundle also in play a repo bundle could otherwise
    be handed that bundle's directory as `repo_root`.
    """
    from hook_manager.handlers.rule_check import _root_for_engine

    engine = rule_engines.get('myrepo:conventions')
    someone_elses_bundle = str(tmp_path / 'central-bundle')
    assert _root_for_engine(engine, someone_elses_bundle) == str(repo.resolve())

    class _Central:
        origin_repo_path = None

    assert _root_for_engine(_Central(), someone_elses_bundle) == someone_elses_bundle


def test_central_engine_covers_any_file(tmp_path):
    from hook_manager.handlers.rule_check import _engine_covers_file

    class _Central:
        origin_repo_path = None

    assert _engine_covers_file(_Central(), str(tmp_path / 'anything.py')) is True
