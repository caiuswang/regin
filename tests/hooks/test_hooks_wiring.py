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


# ── another checkout's entries (CAI-26) ──────────────────────

_FOREIGN_MANAGER = '/other/regin/.venv/bin/python -P -m hook_manager PostToolUse --agent-type claude'
_FOREIGN_DEBUG = '/other/regin/.venv/bin/python /other/regin/scripts/hook_payload_debug.py'


def test_foreign_checkout_is_reported_with_its_root(claude):
    """A *moved* checkout looks exactly like a second one, and reads as
    not-installed — so without this state doctor advises `hooks install`,
    which adds a second entry beside the old one and both then fire."""
    provider, path = claude
    _write_json_hooks(path, {'PostToolUse': [
        {'hooks': [{'type': 'command', 'command': _FOREIGN_MANAGER}]},
    ]})
    status = hooks_wiring.wiring_status(provider, path)['hook_manager']
    assert status['installed'] is False
    assert status['foreign_events'] == ['PostToolUse']
    assert status['foreign_roots'] == ['/other/regin']
    assert status['foreign_commands']['PostToolUse'] == [_FOREIGN_MANAGER]


def test_foreign_entry_beside_a_healthy_install_is_still_reported(claude):
    provider, path = claude
    hooks_wiring.install_hook_manager(provider, path)
    data = json.load(open(path))
    data['hooks']['PostToolUse'].append({'hooks': [{'type': 'command', 'command': _FOREIGN_MANAGER}]})
    with open(path, 'w') as f:
        json.dump(data, f)

    status = hooks_wiring.wiring_status(provider, path)['hook_manager']
    assert status['installed'] is True
    assert status['stale'] is False  # ours is fine; theirs is not ours to repair
    assert status['foreign_events'] == ['PostToolUse']


def test_install_leaves_a_foreign_entry_firing_beside_ours(claude):
    """The defect CAI-26 names, pinned: install is not a fix for this state."""
    provider, path = claude
    _write_json_hooks(path, {'PostToolUse': [
        {'hooks': [{'type': 'command', 'command': _FOREIGN_MANAGER}]},
    ]})
    hooks_wiring.install_hook_manager(provider, path)
    commands = [h['command'] for entry in json.load(open(path))['hooks']['PostToolUse']
                for h in entry['hooks']]
    assert _FOREIGN_MANAGER in commands
    assert len(commands) == 2


def test_adopt_replaces_a_foreign_entry_with_ours(claude):
    provider, path = claude
    _write_json_hooks(path, {'PostToolUse': [
        {'hooks': [{'type': 'command', 'command': _FOREIGN_MANAGER}]},
    ]})
    result = hooks_wiring.adopt_hook_manager(provider, path)
    assert result['ok'] is True
    assert result['adopted'] == 1
    assert '/other/regin' in result['msg']

    commands = [h['command'] for entry in json.load(open(path))['hooks']['PostToolUse']
                for h in entry['hooks']]
    assert commands == [hooks_wiring.expected_hook_manager_commands(provider)['PostToolUse']]
    status = hooks_wiring.wiring_status(provider, path)['hook_manager']
    assert status['foreign_events'] == []
    assert status['stale'] is False


def test_adopt_is_a_noop_without_foreign_entries(claude):
    provider, path = claude
    hooks_wiring.install_hook_manager(provider, path)
    before = open(path).read()
    result = hooks_wiring.adopt_hook_manager(provider, path)
    assert result['adopted'] == 0
    assert 'No entries from another checkout' in result['msg']
    assert open(path).read() == before


def test_adopt_hook_manager_leaves_a_foreign_debug_entry_alone(claude):
    """Adopt is scoped to one hook, like every other writer here — taking over
    the router must not silently claim the other checkout's debug logger."""
    provider, path = claude
    _write_json_hooks(path, {'PostToolUse': [
        {'hooks': [{'type': 'command', 'command': _FOREIGN_MANAGER},
                   {'type': 'command', 'command': _FOREIGN_DEBUG}]},
    ]})
    hooks_wiring.adopt_hook_manager(provider, path)
    commands = [h['command'] for entry in json.load(open(path))['hooks']['PostToolUse']
                for h in entry['hooks']]
    assert _FOREIGN_DEBUG in commands
    assert _FOREIGN_MANAGER not in commands


def test_adopt_debug_hook_takes_over_the_debug_entry(claude):
    provider, path = claude
    _write_json_hooks(path, {'UserPromptSubmit': [
        {'hooks': [{'type': 'command', 'command': _FOREIGN_DEBUG}]},
    ]})
    result = hooks_wiring.adopt_debug_hook(provider, path)
    assert result['adopted'] == 1
    status = hooks_wiring.wiring_status(provider, path)['debug']
    assert status['foreign_events'] == []
    assert status['installed'] is True


@pytest.mark.parametrize('mangled', [
    ['not-an-entry'],
    [{'hooks': 'nope'}],
    [{'hooks': None}],
])
def test_adopt_survives_a_hand_mangled_event(claude, mangled):
    """Doctor prints `hooks adopt`, so a traceback here strands the user at the
    exact command the repair line sent them to — the CAI-21 lesson, restated."""
    provider, path = claude
    _write_json_hooks(path, {
        'PostToolUse': [{'hooks': [{'type': 'command', 'command': _FOREIGN_MANAGER}]}],
        'Stop': mangled,
    })
    result = hooks_wiring.adopt_hook_manager(provider, path)
    assert result['ok'] is True
    assert result['adopted'] == 1
    # Preserved as written — install may add its own entry beside it, but the
    # shape regin cannot read is never rewritten or dropped.
    assert mangled[0] in json.load(open(path))['hooks']['Stop']
    assert hooks_wiring.wiring_status(provider, path)['hook_manager']['foreign_events'] == []


def test_uninstall_survives_a_hand_mangled_event(claude):
    provider, path = claude
    hooks_wiring.install_hook_manager(provider, path)
    data = json.load(open(path))
    data['hooks']['Stop'] = [{'hooks': None}]
    with open(path, 'w') as f:
        json.dump(data, f)

    assert hooks_wiring.uninstall_hook_manager(provider, path)['ok'] is True
    assert json.load(open(path))['hooks']['Stop'] == [{'hooks': None}]


def test_adopt_keeps_an_empty_user_entry(claude):
    provider, path = claude
    _write_json_hooks(path, {'PostToolUse': [
        {'matcher': 'Bash', 'hooks': []},
        {'hooks': [{'type': 'command', 'command': _FOREIGN_MANAGER}]},
    ]})
    hooks_wiring.adopt_hook_manager(provider, path)
    matchers = [e.get('matcher') for e in json.load(open(path))['hooks']['PostToolUse']]
    assert 'Bash' in matchers


def test_our_interpreter_behind_a_wrapper_is_not_another_checkout(claude):
    """It would name *this* directory as the other checkout and offer to adopt
    — i.e. delete — the user's own wrapper."""
    provider, path = claude
    wrapped = f'cd /tmp && {_PREFIX} -P -m hook_manager PostToolUse --agent-type claude'
    _write_json_hooks(path, {'PostToolUse': [
        {'hooks': [{'type': 'command', 'command': wrapped}]},
    ]})
    assert hooks_wiring.wiring_status(provider, path)['hook_manager']['foreign_events'] == []


def test_a_lookalike_debug_script_is_not_ours_to_adopt(claude):
    """`adopt --only-debug` deletes what this matches, so the predicate has to
    name the script, not a prefix of it."""
    provider, path = claude
    lookalike = '/Users/me/bin/hook_payload_debug_wrapper.sh'
    _write_json_hooks(path, {'UserPromptSubmit': [
        {'hooks': [{'type': 'command', 'command': lookalike}]},
    ]})
    assert hooks_wiring.wiring_status(provider, path)['debug']['foreign_events'] == []

    hooks_wiring.adopt_debug_hook(provider, path)
    commands = [h['command'] for entry in json.load(open(path))['hooks']['UserPromptSubmit']
                for h in entry['hooks']]
    assert lookalike in commands


def test_adopt_refuses_an_unparseable_settings_file(claude):
    provider, path = claude
    original = '{"model": "opus",}'
    with open(path, 'w') as f:
        f.write(original)
    result = hooks_wiring.adopt_hook_manager(provider, path)
    assert result['ok'] is False
    assert open(path).read() == original


def test_kimi_foreign_entry_is_reported_and_adoptable(kimi):
    provider, path = kimi
    with open(path, 'w') as f:
        f.write('[[hooks]]\nevent = "PostToolUse"\n'
                f'command = "{_FOREIGN_MANAGER}"\ntimeout = 60\n')

    status = hooks_wiring.wiring_status(provider, path)['hook_manager']
    assert status['installed'] is False
    assert status['foreign_events'] == ['PostToolUse']
    assert status['foreign_roots'] == ['/other/regin']

    assert hooks_wiring.adopt_hook_manager(provider, path)['adopted'] == 1
    assert _FOREIGN_MANAGER not in open(path).read()
    after = hooks_wiring.wiring_status(provider, path)['hook_manager']
    assert after['installed'] is True
    assert after['foreign_events'] == []


# ── timeout drift (CAI-26) ───────────────────────────────────

def test_drifted_timeout_reads_stale_and_is_repaired(claude):
    """A command-only comparison calls this healthy, so the hook keeps being
    killed at the old limit with every surface reporting `ok`."""
    provider, path = claude
    expected = hooks_wiring.expected_hook_manager_commands(provider)['PostToolUse']
    _write_json_hooks(path, {'PostToolUse': [
        {'hooks': [{'type': 'command', 'command': expected, 'timeout': 5}]},
    ]})
    status = hooks_wiring.wiring_status(provider, path)['hook_manager']
    assert status['timeouts']['PostToolUse'] == [5]
    assert status['expected_timeout'] == hooks_wiring.HOOK_MANAGER_TIMEOUT
    assert status['timeout_drift_events'] == ['PostToolUse']
    assert status['stale_events'] == ['PostToolUse']

    hooks_wiring.install_hook_manager(provider, path)
    after = hooks_wiring.wiring_status(provider, path)['hook_manager']
    assert after['timeouts']['PostToolUse'] == [hooks_wiring.HOOK_MANAGER_TIMEOUT]
    assert after['stale'] is False


def test_missing_timeout_is_not_drift(claude):
    """The agent applies its own default there, so an entry predating the
    timeout key behaves correctly — calling it stale is noise, not a finding."""
    provider, path = claude
    expected = hooks_wiring.expected_hook_manager_commands(provider)['PostToolUse']
    _write_json_hooks(path, {'PostToolUse': [
        {'hooks': [{'type': 'command', 'command': expected}]},
    ]})
    status = hooks_wiring.wiring_status(provider, path)['hook_manager']
    assert status['timeout_drift_events'] == []
    assert status['stale_events'] == []


def test_drifted_debug_timeout_uses_the_debug_limit(claude):
    provider, path = claude
    _write_json_hooks(path, {'UserPromptSubmit': [
        {'hooks': [{'type': 'command', 'command': hooks_wiring.debug_hook_command(provider),
                    'timeout': 60}]},
    ]})
    status = hooks_wiring.wiring_status(provider, path)['debug']
    assert status['expected_timeout'] == hooks_wiring.DEBUG_TIMEOUT
    assert status['timeout_drift_events'] == ['UserPromptSubmit']

    hooks_wiring.install_debug_hook(provider, path)
    assert hooks_wiring.wiring_status(provider, path)['debug']['timeout_drift_events'] == []


def test_kimi_timeout_drift_is_visible(kimi):
    provider, path = kimi
    command = hooks_wiring.hook_manager_command('PostToolUse', provider)
    with open(path, 'w') as f:
        f.write(f'[[hooks]]\nevent = "PostToolUse"\ncommand = "{command}"\ntimeout = 5\n')

    status = hooks_wiring.wiring_status(provider, path)['hook_manager']
    assert status['timeout_drift_events'] == ['PostToolUse']
    hooks_wiring.install_hook_manager(provider, path)
    assert hooks_wiring.wiring_status(provider, path)['hook_manager']['timeout_drift_events'] == []


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


# ── WorktreeCreate retraction (CAI-122) ──────────────────────
# The harness reads a WorktreeCreate hook's stdout as the path of the worktree
# it created, so an older regin's route there broke `claude -w <branch>` with
# `worktree directory <repo>/{"suppressOutput": true} does not exist`.

def _install_with_legacy_worktree_route(provider, path) -> None:
    hooks_wiring.install_hook_manager(provider, path)
    data = json.load(open(path))
    data['hooks']['WorktreeCreate'] = [{'hooks': [{
        'type': 'command',
        'command': hooks_wiring.hook_manager_command('WorktreeCreate', provider),
        'timeout': 60,
    }]}]
    with open(path, 'w') as f:
        json.dump(data, f)


def test_install_does_not_wire_worktree_create(claude):
    provider, path = claude
    assert 'WorktreeCreate' not in hooks_wiring.hook_manager_events(provider)
    hooks_wiring.install_hook_manager(provider, path)
    assert 'WorktreeCreate' not in json.load(open(path))['hooks']


def test_legacy_worktree_route_reads_stale(claude):
    provider, path = claude
    _install_with_legacy_worktree_route(provider, path)
    status = hooks_wiring.wiring_status(provider, path)
    assert 'WorktreeCreate' in status['hook_manager']['stale_events']
    assert status['hook_manager']['stale'] is True


def test_repair_retracts_legacy_worktree_route(claude):
    provider, path = claude
    _install_with_legacy_worktree_route(provider, path)
    result = hooks_wiring.install_hook_manager(provider, path)
    assert result['ok'] is True
    assert 'retracted' in result['msg']
    assert 'WorktreeCreate' not in json.load(open(path))['hooks']
    assert hooks_wiring.wiring_status(provider, path)['hook_manager']['stale'] is False


def test_retraction_spares_a_foreign_worktree_hook(claude):
    provider, path = claude
    foreign = '/somewhere/else/bin/python -m their_tool WorktreeCreate'
    _install_with_legacy_worktree_route(provider, path)
    data = json.load(open(path))
    data['hooks']['WorktreeCreate'].append(
        {'hooks': [{'type': 'command', 'command': foreign}]})
    with open(path, 'w') as f:
        json.dump(data, f)

    hooks_wiring.install_hook_manager(provider, path)
    entries = json.load(open(path))['hooks']['WorktreeCreate']
    commands = [h['command'] for e in entries for h in e['hooks']]
    assert commands == [foreign]
