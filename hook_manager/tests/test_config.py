"""Per-handler enable/disable config: round-trip and filter_enabled behavior."""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from hook_manager import config as cfg


@dataclass
class _H:
    name: str


@pytest.fixture
def isolated_config(tmp_path, monkeypatch):
    """Point CONFIG_PATH at a tmp file so tests don't clobber user state."""
    fake = tmp_path / 'hook-manager-config.json'
    monkeypatch.setattr(cfg, 'CONFIG_PATH', str(fake))
    yield fake


def test_default_all_enabled(isolated_config):
    assert cfg.disabled_set() == frozenset()
    assert cfg.is_enabled('anything')


def test_disable_then_enable_round_trip(isolated_config):
    cfg.set_enabled('rule_check', False)
    assert not cfg.is_enabled('rule_check')
    assert 'rule_check' in cfg.disabled_set()

    cfg.set_enabled('rule_check', True)
    assert cfg.is_enabled('rule_check')
    assert 'rule_check' not in cfg.disabled_set()


def test_disable_is_idempotent(isolated_config):
    cfg.set_enabled('x', False)
    cfg.set_enabled('x', False)
    assert cfg.disabled_set() == frozenset({'x'})


def test_filter_enabled_drops_disabled(isolated_config):
    handlers = [_H('a'), _H('b'), _H('c')]
    cfg.set_enabled('b', False)
    kept = cfg.filter_enabled(handlers)
    assert [h.name for h in kept] == ['a', 'c']


def test_filter_enabled_handles_objects_without_name(isolated_config):
    class Bare:
        pass
    kept = cfg.filter_enabled([Bare(), _H('x')])
    assert len(kept) == 2  # no name → treated as enabled


def test_corrupt_config_falls_back_to_empty(isolated_config):
    isolated_config.write_text('not-json{')
    assert cfg.disabled_set() == frozenset()
    assert cfg.is_enabled('anything')


def test_write_creates_parent_dir(tmp_path, monkeypatch):
    nested = tmp_path / 'nope' / 'deeper' / 'config.json'
    monkeypatch.setattr(cfg, 'CONFIG_PATH', str(nested))
    cfg.set_enabled('x', False)
    assert nested.exists()


def test_disabled_handlers_non_list_is_treated_as_empty(isolated_config):
    """If the config got corrupted into `{"disabled_handlers": {"weird": 1}}`,
    the loader must not crash on iteration. The defensive isinstance check
    downgrades unknown shapes to empty."""
    isolated_config.write_text('{"disabled_handlers": {"weird": "dict"}}')
    assert cfg.disabled_set() == frozenset()


def test_disabled_handlers_filters_non_string_entries(isolated_config):
    """Hand-edited config with mixed types: strings survive, everything
    else is dropped — better than crashing or coercing numbers to names."""
    isolated_config.write_text(
        '{"disabled_handlers": ["rule_check", 42, null, "prompt_trace", true]}')
    assert cfg.disabled_set() == frozenset({'rule_check', 'prompt_trace'})


def test_disabled_handlers_missing_key_is_empty(isolated_config, monkeypatch):
    """Config without the `disabled_handlers` key at all (e.g. an older
    schema or user-written `{}`) yields an empty set — not a crash."""
    isolated_config.write_text('{"schema_version": 1}')
    assert cfg.disabled_set() == frozenset()


def test_set_enabled_writes_sorted_disabled_list(isolated_config):
    """Disabled handler names are sorted on write so diffs stay stable
    — otherwise every toggle would reorder the list and muddy git
    history for users who commit this file."""
    import json
    cfg.set_enabled('zeta', False)
    cfg.set_enabled('alpha', False)
    cfg.set_enabled('mu', False)
    data = json.loads(isolated_config.read_text())
    assert data['disabled_handlers'] == ['alpha', 'mu', 'zeta']


def test_set_enabled_writes_schema_version(isolated_config):
    """Every write must stamp schema_version so a future migration can
    detect pre-migration shapes."""
    import json
    cfg.set_enabled('x', False)
    data = json.loads(isolated_config.read_text())
    assert data.get('schema_version') == 1


def test_enable_already_enabled_is_noop(isolated_config):
    """Enabling a handler that's not in the disabled list must not
    double-add or mutate the list — idempotent in both directions."""
    cfg.set_enabled('already_on', True)
    cfg.set_enabled('already_on', True)
    assert cfg.disabled_set() == frozenset()


def test_priority_overrides_default_empty(isolated_config):
    """Empty / missing config → no overrides, never raises."""
    assert cfg.priority_overrides() == {}
    assert cfg.effective_priority('rule_check', default=80) == 80


def test_set_and_read_priority_override(isolated_config):
    cfg.set_priorities({'rule_check': 120})
    assert cfg.priority_overrides() == {'rule_check': 120}
    assert cfg.effective_priority('rule_check', default=80) == 120
    assert cfg.effective_priority('other', default=50) == 50


def test_set_priorities_merges_rather_than_replacing(isolated_config):
    cfg.set_priorities({'a': 100})
    cfg.set_priorities({'b': 110})
    assert cfg.priority_overrides() == {'a': 100, 'b': 110}


def test_set_priorities_none_removes_override(isolated_config):
    cfg.set_priorities({'a': 100, 'b': 110})
    cfg.set_priorities({'a': None})
    assert cfg.priority_overrides() == {'b': 110}


def test_clear_priorities_drops_everything(isolated_config):
    cfg.set_priorities({'a': 100, 'b': 110})
    cfg.clear_priorities()
    assert cfg.priority_overrides() == {}


def test_priority_overrides_filters_non_numeric(isolated_config):
    """Bad values (bools, strings, lists) get dropped at read time."""
    isolated_config.write_text(
        '{"priority_overrides": {"good": 120, "bad_bool": true, "bad_str": "x", "bad_list": []}}'
    )
    assert cfg.priority_overrides() == {'good': 120}


def test_priority_overrides_floats_round_to_int(isolated_config):
    isolated_config.write_text('{"priority_overrides": {"x": 99.7}}')
    assert cfg.priority_overrides() == {'x': 99}


def test_priority_overrides_non_dict_is_treated_as_empty(isolated_config):
    """Defensive: corrupt config with a list under `priority_overrides`."""
    isolated_config.write_text('{"priority_overrides": ["not", "a", "dict"]}')
    assert cfg.priority_overrides() == {}


def test_set_priorities_writes_sorted_keys(isolated_config):
    """Stable on-disk diffs: keys are sorted on write."""
    import json
    cfg.set_priorities({'zeta': 30, 'alpha': 10, 'mu': 20})
    data = json.loads(isolated_config.read_text())
    assert list(data['priority_overrides'].keys()) == ['alpha', 'mu', 'zeta']


def test_set_priorities_empty_input_is_noop(isolated_config):
    """Calling with {} doesn't wipe existing overrides."""
    cfg.set_priorities({'x': 50})
    cfg.set_priorities({})
    assert cfg.priority_overrides() == {'x': 50}


def test_provider_specific_config_paths(tmp_path, monkeypatch):
    claude_cfg = tmp_path / 'claude-config.json'
    codex_cfg = tmp_path / 'codex-config.json'
    monkeypatch.setattr(cfg, 'CONFIG_PATH', str(claude_cfg))

    class _Provider:
        def __init__(self, path):
            self._path = path

        def hook_manager_config_path(self):
            return self._path

    monkeypatch.setattr(
        cfg,
        'build_provider',
        lambda provider_id: _Provider(codex_cfg if provider_id == 'codex' else claude_cfg),
    )

    cfg.set_enabled('prompt_trace', False, agent_type='codex')
    assert cfg.disabled_set(agent_type='codex') == frozenset({'prompt_trace'})
    assert cfg.disabled_set(agent_type='claude') == frozenset()
    assert not cfg.is_enabled('prompt_trace', agent_type='codex')
    assert cfg.is_enabled('prompt_trace', agent_type='claude')
