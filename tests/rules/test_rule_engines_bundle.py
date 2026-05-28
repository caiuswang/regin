"""Tests for the generic BundleEngine rule adapter and its manifest helpers."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from lib.rule_engines.bundle import BundleEngine
from lib.rule_engines.manifest import (
    BundleManifest,
    RunnerSpec,
    discover_bundles,
    load_manifest,
    manifest_path,
    resolve_runner_entry,
    scaffold_bundle,
    validate_bundle,
)


# ── Test fixtures ────────────────────────────────────────────────────────


def _write_python_runner(bundle_root: Path, body: str) -> None:
    (bundle_root / 'bin').mkdir(parents=True, exist_ok=True)
    (bundle_root / 'bin' / 'runner.py').write_text(body, encoding='utf-8')


_ECHO_RUNNER = """\
import json, sys
payload = json.loads(sys.stdin.read())
rule = payload.get('rule') or {}
# Emit one match if the rule's options include `match: true`; otherwise none.
matches = 1 if (rule.get('options') or {}).get('match') else 0
out = {'matches': matches, 'details': ['hit'] if matches else []}
json.dump(out, sys.stdout)
"""


def _make_bundle(tmp_path: Path, *, slug: str = 'test-bundle',
                 rule_overrides: dict | None = None) -> Path:
    bundle_root = tmp_path / 'patterns' / slug
    bundle_root.mkdir(parents=True)
    manifest = {
        'schema': 'rule-bundle/v1',
        'id': slug,
        'language_ids': ['python'],
        'rules_dir': 'rules',
        'checkers_dir': 'checkers',
        'runner': {'kind': 'python', 'entry': 'bin/runner.py'},
    }
    (bundle_root / 'regin-bundle.yaml').write_text(yaml.safe_dump(manifest))
    (bundle_root / 'rules').mkdir()
    rule = {
        'id': 'r1',
        'summary': 'Test rule',
        'severity': 'warn',
        'triggers': ['**/*.py'],
        'options': {'match': True},
    }
    if rule_overrides:
        rule.update(rule_overrides)
    (bundle_root / 'rules' / 'r1.yaml').write_text(yaml.safe_dump([rule]))
    _write_python_runner(bundle_root, _ECHO_RUNNER)
    return bundle_root


# ── Manifest parsing ─────────────────────────────────────────────────────


def test_manifest_parses_minimal_valid(tmp_path):
    bundle_root = _make_bundle(tmp_path)
    mpath = manifest_path(bundle_root)
    assert mpath is not None
    manifest = load_manifest(mpath)
    assert manifest.id == 'test-bundle'
    assert manifest.language_ids == ('python',)
    assert manifest.runner.kind == 'python'


def test_manifest_rejects_bad_schema(tmp_path):
    bundle_root = tmp_path / 'p'
    bundle_root.mkdir()
    (bundle_root / 'regin-bundle.yaml').write_text(yaml.safe_dump({
        'schema': 'rule-bundle/v99',
        'id': 'x',
        'language_ids': ['python'],
        'runner': {'kind': 'python', 'entry': 'r.py'},
    }))
    with pytest.raises(Exception):
        load_manifest(bundle_root / 'regin-bundle.yaml')


def test_manifest_rejects_bad_id(tmp_path):
    bundle_root = tmp_path / 'p'
    bundle_root.mkdir()
    (bundle_root / 'regin-bundle.yaml').write_text(yaml.safe_dump({
        'schema': 'rule-bundle/v1',
        'id': 'Bad_ID',
        'language_ids': ['python'],
        'runner': {'kind': 'python', 'entry': 'r.py'},
    }))
    with pytest.raises(Exception):
        load_manifest(bundle_root / 'regin-bundle.yaml')


def test_resolve_runner_entry_rejects_traversal(tmp_path):
    bundle_root = tmp_path / 'p'
    bundle_root.mkdir()
    with pytest.raises(ValueError, match='escapes'):
        resolve_runner_entry(bundle_root, '../outside.py')


# ── Engine parse / applies / run ─────────────────────────────────────────


def _engine_for(bundle_root: Path) -> BundleEngine:
    manifest = load_manifest(manifest_path(bundle_root))
    return BundleEngine(id=manifest.id, bundle_root=bundle_root, manifest=manifest)


def test_parse_rules_dedupes_by_id(tmp_path):
    bundle_root = _make_bundle(tmp_path)
    # Add a second rule file with a duplicate id — should be ignored.
    (bundle_root / 'rules' / 'r1_dup.yaml').write_text(yaml.safe_dump([{
        'id': 'r1', 'summary': 'dup', 'severity': 'warn', 'triggers': ['**/*.py'],
    }]))
    rules = _engine_for(bundle_root).parse_rules()
    assert [r.id for r in rules] == ['r1']


def test_parse_rules_skips_disabled(tmp_path):
    bundle_root = _make_bundle(tmp_path)
    (bundle_root / 'rules' / 'r2.yaml').write_text(yaml.safe_dump([{
        'id': 'r2', 'disabled': True, 'triggers': ['**/*.py'],
    }]))
    ids = {r.id for r in _engine_for(bundle_root).parse_rules()}
    assert ids == {'r1'}


def test_applies_to_uses_glob(tmp_path):
    bundle_root = _make_bundle(tmp_path)
    engine = _engine_for(bundle_root)
    rule = engine.parse_rules()[0]
    assert engine.applies_to(rule, '/repo/foo.py', '')
    assert not engine.applies_to(rule, '/repo/foo.txt', '')


def test_content_triggers_anded_with_glob(tmp_path):
    bundle_root = _make_bundle(tmp_path, rule_overrides={
        'content_triggers': ['BANNED'],
    })
    engine = _engine_for(bundle_root)
    rule = engine.parse_rules()[0]
    assert engine.applies_to(rule, '/x.py', 'hello BANNED world')
    assert not engine.applies_to(rule, '/x.py', 'innocuous')


def test_run_returns_violation_when_runner_emits_match(tmp_path):
    bundle_root = _make_bundle(tmp_path)
    engine = _engine_for(bundle_root)
    rule = engine.parse_rules()[0]
    violation = engine.run(rule, '/tmp/whatever.py', str(tmp_path))
    assert violation is not None
    assert violation.match_count == 1


def test_run_returns_none_when_runner_emits_no_match(tmp_path):
    bundle_root = _make_bundle(tmp_path, rule_overrides={
        'options': {'match': False},
    })
    engine = _engine_for(bundle_root)
    rule = engine.parse_rules()[0]
    assert engine.run(rule, '/tmp/whatever.py', str(tmp_path)) is None


def test_bundle_engine_contributed_skills_is_empty(tmp_path):
    """Pattern-as-skill path owns SKILL.md deployment; engine returns []."""
    bundle_root = _make_bundle(tmp_path)
    assert _engine_for(bundle_root).contributed_skills() == []


# ── Discovery ────────────────────────────────────────────────────────────


def test_discover_bundles_yields_well_formed(tmp_path):
    _make_bundle(tmp_path, slug='a')
    _make_bundle(tmp_path, slug='b')
    found = {m.id for _, m in discover_bundles(tmp_path / 'patterns')}
    assert found == {'a', 'b'}


def test_discover_skips_malformed_silently(tmp_path):
    bundle_a = _make_bundle(tmp_path, slug='good')
    bad = tmp_path / 'patterns' / 'bad'
    bad.mkdir()
    (bad / 'regin-bundle.yaml').write_text('not: a [valid manifest')
    found = {m.id for _, m in discover_bundles(tmp_path / 'patterns')}
    assert found == {'good'}


def test_discover_skips_hidden_dirs(tmp_path):
    _make_bundle(tmp_path, slug='regular')
    hidden = tmp_path / 'patterns' / '.hidden'
    hidden.mkdir()
    (hidden / 'regin-bundle.yaml').write_text(yaml.safe_dump({
        'schema': 'rule-bundle/v1',
        'id': 'hidden',
        'language_ids': ['python'],
        'runner': {'kind': 'python', 'entry': 'r.py'},
    }))
    found = {m.id for _, m in discover_bundles(tmp_path / 'patterns')}
    assert found == {'regular'}


# ── Scaffold + doctor ────────────────────────────────────────────────────


def test_scaffold_writes_complete_bundle(tmp_path):
    bundle_root = tmp_path / 'new-bundle'
    created = scaffold_bundle(bundle_root, slug='new-bundle')
    assert (bundle_root / 'regin-bundle.yaml').is_file()
    assert (bundle_root / 'rules' / 'example.yaml').is_file()
    assert (bundle_root / 'checkers' / 'example_checker.py').is_file()
    assert (bundle_root / 'bin' / 'runner.py').is_file()
    assert len(created) == 4


def test_scaffold_refuses_to_overwrite(tmp_path):
    bundle_root = tmp_path / 'b'
    scaffold_bundle(bundle_root, slug='b')
    with pytest.raises(FileExistsError):
        scaffold_bundle(bundle_root, slug='b')


def test_validate_bundle_ok_path(tmp_path):
    bundle_root = _make_bundle(tmp_path)
    manifest, diags = validate_bundle(bundle_root)
    assert manifest is not None
    assert not any(d.level == 'error' for d in diags)


def test_validate_bundle_missing_manifest(tmp_path):
    bundle_root = tmp_path / 'empty'
    bundle_root.mkdir()
    manifest, diags = validate_bundle(bundle_root)
    assert manifest is None
    assert any(d.level == 'error' for d in diags)


def test_validate_bundle_warns_on_no_rules(tmp_path):
    bundle_root = _make_bundle(tmp_path)
    for f in (bundle_root / 'rules').iterdir():
        f.unlink()
    _, diags = validate_bundle(bundle_root)
    assert any(d.level == 'warn' and 'rule files' in d.message for d in diags)


# ── Reserved-filename guard at bundle root ───────────────────────────────


def test_parse_rules_skips_package_json_at_root(tmp_path):
    """A bundle with rules_dir='.' must not slurp `package.json` as a rule file."""
    bundle_root = tmp_path / 'p'
    bundle_root.mkdir()
    (bundle_root / 'regin-bundle.yaml').write_text(yaml.safe_dump({
        'schema': 'rule-bundle/v1',
        'id': 'p',
        'language_ids': ['python'],
        'rules_dir': '.',
        'runner': {'kind': 'python', 'entry': 'bin/runner.py'},
    }))
    (bundle_root / 'package.json').write_text(json.dumps({'name': 'p'}))
    (bundle_root / 'r1.yaml').write_text(yaml.safe_dump([{
        'id': 'r1', 'triggers': ['**/*.py'],
    }]))
    _write_python_runner(bundle_root, _ECHO_RUNNER)
    ids = {r.id for r in _engine_for(bundle_root).parse_rules()}
    assert ids == {'r1'}
