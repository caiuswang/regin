"""Tests for /api/repos/<name>/bundles — listing and the trust toggle."""

from __future__ import annotations

import pytest
import yaml

from lib.rule_engines import bundle_trust


@pytest.fixture
def repo_with_bundle(tmp_path, monkeypatch):
    """A repo shipping one bundle, with the trust store isolated to tmp."""
    from lib import settings as settings_mod
    from web.blueprints import repo_bundles

    repo_root = tmp_path / 'demo-repo'
    bundle_root = repo_root / '.regin' / 'rules' / 'house-style'
    (bundle_root / 'rules').mkdir(parents=True)
    (bundle_root / 'checkers').mkdir(parents=True)
    (bundle_root / 'bin').mkdir(parents=True)
    (bundle_root / 'regin-bundle.yaml').write_text(yaml.safe_dump({
        'schema': 'rule-bundle/v1',
        'id': 'house-style',
        'language_ids': ['typescript'],
        'rules_dir': 'rules',
        'checkers_dir': 'checkers',
        'runner': {'kind': 'node', 'entry': 'bin/runner.mjs'},
        'description': 'demo',
    }))
    (bundle_root / 'rules' / 'r.yaml').write_text(yaml.safe_dump([{
        'id': 'no-alert', 'checker': 'demo', 'triggers': ['**/*.ts'],
    }]))
    (bundle_root / 'bin' / 'runner.mjs').write_text('// runner\n')
    (bundle_root / 'checkers' / 'demo.mjs').write_text('export function run() {}\n')

    data_dir = tmp_path / 'data'
    data_dir.mkdir()
    monkeypatch.setattr(settings_mod.settings, 'data_dir', data_dir)
    monkeypatch.setattr(repo_bundles, '_repo_path', lambda name: (
        str(repo_root) if name == 'demo-repo' else None
    ))
    return repo_root


def test_lists_bundle_as_untrusted(flask_client, repo_with_bundle):
    r = flask_client.get('/api/repos/demo-repo/bundles')
    assert r.status_code == 200
    payload = r.get_json()
    assert len(payload['bundles']) == 1
    entry = payload['bundles'][0]
    assert entry['engine_id'] == 'demo-repo:house-style'
    assert entry['languages'] == ['typescript']
    assert entry['trusted'] is False
    assert entry['code_changed'] is False
    assert len(entry['fingerprint']) == 12


def test_unknown_repo_404s(flask_client, repo_with_bundle):
    assert flask_client.get('/api/repos/nope/bundles').status_code == 404


def test_trust_then_untrust_round_trip(flask_client, repo_with_bundle):
    r = flask_client.post('/api/repos/demo-repo/bundles/house-style/trust')
    assert r.status_code == 200
    assert r.get_json()['trusted'] is True
    assert flask_client.get('/api/repos/demo-repo/bundles').get_json()['bundles'][0]['trusted'] is True

    r = flask_client.delete('/api/repos/demo-repo/bundles/house-style/trust')
    assert r.status_code == 200
    assert r.get_json()['removed'] == 1
    assert flask_client.get('/api/repos/demo-repo/bundles').get_json()['bundles'][0]['trusted'] is False


def test_trusting_unknown_bundle_404s(flask_client, repo_with_bundle):
    r = flask_client.post('/api/repos/demo-repo/bundles/not-a-bundle/trust')
    assert r.status_code == 404


def test_checker_edit_surfaces_as_code_changed(flask_client, repo_with_bundle):
    flask_client.post('/api/repos/demo-repo/bundles/house-style/trust')
    checker = repo_with_bundle / '.regin' / 'rules' / 'house-style' / 'checkers' / 'demo.mjs'
    checker.write_text('export function run() { /* new behaviour */ }\n')

    entry = flask_client.get('/api/repos/demo-repo/bundles').get_json()['bundles'][0]
    assert entry['trusted'] is False
    assert entry['code_changed'] is True


def test_trust_store_keyed_by_realpath(repo_with_bundle):
    """Trusting via a symlinked path must be recognised via the real one."""
    fingerprint = 'a' * 64
    bundle_trust.trust(str(repo_with_bundle), 'house-style', fingerprint)
    assert bundle_trust.is_trusted(
        str(repo_with_bundle) + '/', 'house-style', fingerprint,
    ) is True
