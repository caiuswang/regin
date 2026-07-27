"""Unit tests for lib.hooks_wiring — the install-vs-disk comparison that makes
stale hook wiring visible and repairable (CAI-21).

The regression these guard is CAI-15: a routed hook whose *command* had drifted
from what install writes today looked healthy through an installed/not-installed
check, so no surface ever offered to rewrite it.
"""

from __future__ import annotations

import json
import os

import pytest

from lib import hooks_wiring
from lib import settings as _settings_mod
from lib.providers.claude import ClaudeProvider
from lib.providers.kimi import KimiProvider


_PREFIX = os.path.join(str(_settings_mod.settings.project_root), '.venv/bin/python')


@pytest.fixture
def claude(tmp_path):
    path = tmp_path / 'claude-settings.json'
    return ClaudeProvider({'hook_settings_path': path}), str(path)


@pytest.fixture
def kimi(tmp_path):
    path = tmp_path / 'kimi-config.toml'
    provider = KimiProvider({
        'hook_settings_path': path,
        'hook_payload_log_path': tmp_path / 'kimi-hook-payloads.jsonl',
    })
    return provider, str(path)


def _write_json_hooks(path: str, hooks: dict) -> None:
    with open(path, 'w') as f:
        json.dump({'hooks': hooks}, f)


# ── staleness detection ──────────────────────────────────────

def test_not_installed_is_not_stale(claude):
    provider, path = claude
    status = hooks_wiring.wiring_status(provider, path)
    assert status['hook_manager']['installed'] is False
    assert status['hook_manager']['stale'] is False


def test_fresh_install_is_not_stale(claude):
    provider, path = claude
    hooks_wiring.install_hook_manager(provider, path)
    status = hooks_wiring.wiring_status(provider, path)
    assert status['hook_manager']['installed'] is True
    assert status['hook_manager']['stale'] is False
    assert status['hook_manager']['stale_events'] == []
    assert status['hook_manager']['missing_events'] == []


def test_drifted_command_reads_stale_and_names_the_event(claude):
    provider, path = claude
    hooks_wiring.install_hook_manager(provider, path)
    data = json.load(open(path))
    data['hooks']['PostToolUse'][0]['hooks'][0]['command'] = (
        f'{_PREFIX} -m hook_manager PostToolUse')  # pre-`-P`, pre-`--agent-type`
    with open(path, 'w') as f:
        json.dump(data, f)

    status = hooks_wiring.wiring_status(provider, path)
    assert status['hook_manager']['stale'] is True
    assert status['hook_manager']['stale_events'] == ['PostToolUse']


def test_install_repairs_a_drifted_command(claude):
    provider, path = claude
    _write_json_hooks(path, {'PostToolUse': [
        {'hooks': [{'type': 'command', 'command': f'{_PREFIX} -m hook_manager PostToolUse'}]},
    ]})
    assert hooks_wiring.wiring_status(provider, path)['hook_manager']['stale'] is True

    result = hooks_wiring.install_hook_manager(provider, path)
    assert result['ok'] is True
    assert 'updated' in result['msg']
    assert hooks_wiring.wiring_status(provider, path)['hook_manager']['stale'] is False


def test_partial_install_reports_missing_events(claude):
    provider, path = claude
    expected = hooks_wiring.expected_hook_manager_commands(provider)
    _write_json_hooks(path, {'PostToolUse': [
        {'hooks': [{'type': 'command', 'command': expected['PostToolUse']}]},
    ]})
    status = hooks_wiring.wiring_status(provider, path)
    assert status['hook_manager']['stale'] is True
    assert status['hook_manager']['stale_events'] == []
    assert 'SessionStart' in status['hook_manager']['missing_events']


def test_foreign_checkout_command_is_not_ours(claude):
    provider, path = claude
    _write_json_hooks(path, {'PostToolUse': [
        {'hooks': [{'type': 'command', 'command': '/other/regin/.venv/bin/python -m hook_manager PostToolUse'}]},
    ]})
    status = hooks_wiring.wiring_status(provider, path)
    assert status['hook_manager']['installed'] is False


# ── the CAI-15 shape: a debug hook missing --silent ──────────

def test_kimi_debug_hook_without_silent_is_stale_and_repairable(kimi):
    provider, path = kimi
    # What an install predating the per-provider command wrote: no log path,
    # no --silent, so its Claude-style stdout leaked into Kimi's UI.
    stale_cmd = hooks_wiring.debug_base_command()
    with open(path, 'w') as f:
        f.write('[[hooks]]\nevent = "UserPromptSubmit"\n'
                f'command = "{stale_cmd}"\ntimeout = 10\n')

    status = hooks_wiring.wiring_status(provider, path)
    assert status['debug']['installed'] is True
    assert status['debug']['stale'] is True
    assert 'UserPromptSubmit' in status['debug']['stale_events']

    hooks_wiring.install_debug_hook(provider, path)
    after = hooks_wiring.wiring_status(provider, path)
    assert after['debug']['stale'] is False
    assert all('--silent' in cmds[0] for cmds in after['debug']['commands'].values())


def test_kimi_hook_manager_roundtrip(kimi):
    provider, path = kimi
    hooks_wiring.install_hook_manager(provider, path)
    status = hooks_wiring.wiring_status(provider, path)
    assert status['hook_manager']['installed'] is True
    assert status['hook_manager']['stale'] is False

    hooks_wiring.uninstall_hook_manager(provider, path)
    assert hooks_wiring.wiring_status(provider, path)['hook_manager']['installed'] is False


def test_unexpected_event_is_reported_but_does_not_gate_stale(claude):
    provider, path = claude
    hooks_wiring.install_hook_manager(provider, path)
    data = json.load(open(path))
    data['hooks']['NotASpecEvent'] = [{'hooks': [{
        'type': 'command',
        'command': hooks_wiring.hook_manager_command('NotASpecEvent', provider),
    }]}]
    with open(path, 'w') as f:
        json.dump(data, f)

    status = hooks_wiring.wiring_status(provider, path)
    assert status['hook_manager']['unexpected_events'] == ['NotASpecEvent']
    assert status['hook_manager']['stale'] is False


# ── defects found in adversarial review ──────────────────────

def test_duplicate_but_correct_entries_read_stale(claude):
    provider, path = claude
    expected = hooks_wiring.expected_hook_manager_commands(provider)['PostToolUse']
    _write_json_hooks(path, {'PostToolUse': [
        {'hooks': [{'type': 'command', 'command': expected}]},
        {'hooks': [{'type': 'command', 'command': expected}]},
    ]})
    status = hooks_wiring.wiring_status(provider, path)
    # The hook would fire twice; a command-*set* comparison calls this healthy.
    assert status['hook_manager']['commands']['PostToolUse'] == [expected, expected]
    assert status['hook_manager']['stale_events'] == ['PostToolUse']


def test_repair_deduplicates_and_converges(claude):
    provider, path = claude
    expected = hooks_wiring.expected_hook_manager_commands(provider)['PostToolUse']
    _write_json_hooks(path, {'PostToolUse': [
        {'hooks': [{'type': 'command', 'command': expected}]},
        {'hooks': [{'type': 'command', 'command': f'{_PREFIX} -m hook_manager PostToolUse'}]},
    ]})
    hooks_wiring.install_hook_manager(provider, path)
    status = hooks_wiring.wiring_status(provider, path)
    assert status['hook_manager']['commands']['PostToolUse'] == [expected]
    assert status['hook_manager']['stale'] is False


def test_dedupe_keeps_foreign_and_user_hooks_on_the_same_event(claude):
    provider, path = claude
    expected = hooks_wiring.expected_hook_manager_commands(provider)['PostToolUse']
    foreign = '/other/regin/.venv/bin/python -P -m hook_manager PostToolUse --agent-type claude'
    _write_json_hooks(path, {'PostToolUse': [
        {'hooks': [{'type': 'command', 'command': expected},
                   {'type': 'command', 'command': foreign}]},
        {'hooks': [{'type': 'command', 'command': expected}]},
    ]})
    hooks_wiring.install_hook_manager(provider, path)
    data = json.load(open(path))
    commands = [h['command'] for entry in data['hooks']['PostToolUse'] for h in entry['hooks']]
    assert commands.count(expected) == 1
    assert foreign in commands


def test_malformed_event_value_is_reported_not_overwritten(claude):
    provider, path = claude
    with open(path, 'w') as f:
        json.dump({'hooks': {
            'PostToolUse': [{'hooks': [{
                'type': 'command',
                'command': hooks_wiring.hook_manager_command('PostToolUse', provider)}]}],
            'PreToolUse': 'hand-edited-typo',
        }}, f)

    status = hooks_wiring.wiring_status(provider, path)
    assert status['hook_manager']['malformed_events'] == ['PreToolUse']
    # Not counted as repairable drift — install cannot write into a string.
    assert 'PreToolUse' not in status['hook_manager']['missing_events']


def test_install_survives_a_malformed_event_value(claude):
    provider, path = claude
    with open(path, 'w') as f:
        json.dump({'hooks': {'PreToolUse': 'hand-edited-typo'}}, f)

    result = hooks_wiring.install_hook_manager(provider, path)
    assert result['ok'] is True
    data = json.load(open(path))
    assert data['hooks']['PreToolUse'] == 'hand-edited-typo'
    assert 'SessionStart' in data['hooks']


def test_debug_uninstall_leaves_another_checkouts_entry_alone(claude):
    provider, path = claude
    foreign = '/other/regin/.venv/bin/python /other/regin/scripts/hook_payload_debug.py'
    hooks_wiring.install_debug_hook(provider, path)
    data = json.load(open(path))
    data['hooks']['PostToolUse'].append(
        {'hooks': [{'type': 'command', 'command': foreign}]})
    with open(path, 'w') as f:
        json.dump(data, f)

    hooks_wiring.uninstall_debug_hook(provider, path)
    remaining = [h['command'] for entries in json.load(open(path))['hooks'].values()
                 for entry in entries for h in entry['hooks']]
    assert remaining == [foreign]


def test_matcher_scoped_copies_are_not_duplicates(claude):
    """A hand-written `matcher` entry is deliberate scoping, not a duplicate.

    regin only ever writes matcher-less entries, so the two copies here can
    only have come from the user — and refresh used to keep the first and
    silently delete the rest.
    """
    provider, path = claude
    command = hooks_wiring.hook_manager_command('PostToolUse', provider)
    _write_json_hooks(path, {'PostToolUse': [
        {'matcher': 'Edit', 'hooks': [{'type': 'command', 'command': command}]},
        {'matcher': 'Bash', 'hooks': [{'type': 'command', 'command': command}]},
    ]})

    status = hooks_wiring.wiring_status(provider, path)
    assert status['hook_manager']['stale_events'] == []

    hooks_wiring.install_hook_manager(provider, path)
    matchers = [e.get('matcher') for e in json.load(open(path))['hooks']['PostToolUse']]
    assert matchers == ['Edit', 'Bash']


def test_two_copies_under_one_matcher_are_still_a_duplicate(claude):
    provider, path = claude
    command = hooks_wiring.hook_manager_command('PostToolUse', provider)
    _write_json_hooks(path, {'PostToolUse': [
        {'matcher': 'Edit', 'hooks': [{'type': 'command', 'command': command}]},
        {'matcher': 'Edit', 'hooks': [{'type': 'command', 'command': command}]},
    ]})

    assert hooks_wiring.wiring_status(provider, path)['hook_manager']['stale_events'] == ['PostToolUse']

    hooks_wiring.install_hook_manager(provider, path)
    ours = [h for entry in json.load(open(path))['hooks']['PostToolUse']
            for h in entry['hooks'] if hooks_wiring.is_hook_manager_command(h['command'])]
    assert len(ours) == 1
    assert hooks_wiring.wiring_status(provider, path)['hook_manager']['stale_events'] == []


def test_refresh_rewrites_every_matcher_scoped_copy(claude):
    """Updating only the first copy would leave the event stale forever."""
    provider, path = claude
    drifted = f'{_PREFIX} -m hook_manager PostToolUse'
    _write_json_hooks(path, {'PostToolUse': [
        {'matcher': 'Edit', 'hooks': [{'type': 'command', 'command': drifted}]},
        {'matcher': 'Bash', 'hooks': [{'type': 'command', 'command': drifted}]},
    ]})
    assert hooks_wiring.wiring_status(provider, path)['hook_manager']['stale_events'] == ['PostToolUse']

    hooks_wiring.install_hook_manager(provider, path)

    expected = hooks_wiring.hook_manager_command('PostToolUse', provider)
    commands = [h['command'] for entry in json.load(open(path))['hooks']['PostToolUse']
                for h in entry['hooks']]
    assert commands == [expected, expected]
    assert hooks_wiring.wiring_status(provider, path)['hook_manager']['stale_events'] == []


@pytest.mark.parametrize('writer', [
    'install_hook_manager', 'uninstall_hook_manager',
    'install_debug_hook', 'uninstall_debug_hook',
])
def test_writers_refuse_an_unparseable_settings_file(claude, writer):
    """`read_json_settings` reads a broken file as `{}`, so a writer that did
    not check would treat it as empty and overwrite the user's whole config."""
    provider, path = claude
    original = '{"model": "opus", "permissions": {"allow": ["Bash"]},}'
    with open(path, 'w') as f:
        f.write(original)

    result = getattr(hooks_wiring, writer)(provider, path)
    assert result['ok'] is False
    assert 'not valid JSON' in result['msg']
    assert open(path).read() == original


def test_writers_still_accept_an_empty_settings_file(claude):
    provider, path = claude
    with open(path, 'w') as f:
        f.write('   \n')

    assert hooks_wiring.install_hook_manager(provider, path)['ok'] is True
    assert json.load(open(path))['hooks']
