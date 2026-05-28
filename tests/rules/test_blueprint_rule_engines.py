"""Tests for /api/rule-engines."""

from __future__ import annotations

import pytest

from lib import rule_engines
from lib.settings import RuleEngineConfig


@pytest.fixture(autouse=True)
def _disable_bundle_autoload(monkeypatch):
    """Block auto-discovery of bundles from the user's real patterns dir."""
    from lib import settings as settings_mod
    monkeypatch.setattr(settings_mod.settings, 'bundle_autoload', False)


@pytest.fixture
def client_with_grit_engine(flask_client, tmp_path, monkeypatch):
    from lib import settings as settings_mod
    cfg = RuleEngineConfig(
        id='grit', kind='grit',
        grit_dir=tmp_path, language_ids=('java',),
    )
    monkeypatch.setattr(settings_mod.settings, 'rule_engines', [cfg])
    rule_engines.invalidate_cache()
    yield flask_client
    rule_engines.invalidate_cache()


def test_api_rule_engines_returns_configured_engine(client_with_grit_engine):
    r = client_with_grit_engine.get('/api/rule-engines')
    assert r.status_code == 200
    payload = r.get_json()
    assert len(payload) == 1
    entry = payload[0]
    assert entry['id'] == 'grit'
    assert entry['kind'] == 'grit'
    assert entry['languages'] == ['java']
    assert 'grit apply' in entry['invocation_hint']
    assert isinstance(entry['rule_count'], int)


def test_api_rule_engines_empty_list_when_no_engines_configured(
        flask_client, tmp_path, monkeypatch):
    from lib import settings as settings_mod
    monkeypatch.setattr(settings_mod.settings, 'rule_engines', [])
    empty = tmp_path / 'empty'
    empty.mkdir()
    monkeypatch.setattr(settings_mod.settings, 'grit_dir', empty)
    rule_engines.invalidate_cache()
    try:
        r = flask_client.get('/api/rule-engines')
        assert r.status_code == 200
        assert r.get_json() == []
    finally:
        rule_engines.invalidate_cache()


