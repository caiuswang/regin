"""Two regin checkouts on one machine share the agent's hook config, so every
path that removes regin's wiring must remove only what *this* checkout wrote
(F156 R4, invariant 4) — and the commands it writes must survive a checkout
path that needs shell quoting (F156 R7).
"""

from __future__ import annotations

import io
import json
import os
import shlex
import subprocess
import sys

import pytest
from typer.testing import CliRunner

from cli import output as cli_output
from cli.commands import db as db_cmd
from cli.commands import hooks as hooks_cmd
from lib import hooks_wiring
from lib.providers import kimi_hooks
from lib.providers.claude import ClaudeProvider
from lib.providers.kimi import KimiProvider
from lib.settings import settings


_OTHER = '/other/regin/.venv/bin/python'


def _other_manager(event: str, agent: str = 'claude') -> str:
    return f'{_OTHER} -P -m hook_manager {event} --agent-type {agent}'


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


def _commands(path: str) -> list[str]:
    hooks = json.load(open(path)).get('hooks', {})
    return [h['command'] for entries in hooks.values()
            for h in hooks_wiring.hook_entries(entries)]


# ── init --force ──────────────────────────────────────────────

def test_init_force_spares_another_checkout_s_hooks(claude, monkeypatch, tmp_path):
    provider, path = claude
    hooks_wiring.install_hook_manager(provider, path)
    data = json.load(open(path))
    data['hooks']['Stop'].append({'hooks': [{'type': 'command', 'command': _other_manager('Stop')}]})
    with open(path, 'w') as f:
        json.dump(data, f)
    ours = len(hooks_wiring.expected_hook_manager_commands(provider))

    monkeypatch.setattr(db_cmd, '_PROVIDER', provider)
    monkeypatch.setattr(db_cmd, 'CLAUDE_SETTINGS_PATH', path)
    monkeypatch.setattr(db_cmd, 'HOOK_MANAGER_CONFIG_PATH', str(tmp_path / 'hm.json'))
    monkeypatch.setattr(db_cmd, '_clear_recorded_deployments', lambda: 0)
    monkeypatch.setattr(db_cmd, '_remove_primary_db', lambda: None)
    monkeypatch.setattr(db_cmd, '_clear_jwt_secret', lambda: False)

    assert db_cmd._force_reset_state()[3] == ours
    assert _commands(path) == [_other_manager('Stop')]


def test_init_force_leaves_a_missing_settings_file_missing(claude, monkeypatch):
    provider, path = claude
    monkeypatch.setattr(db_cmd, '_PROVIDER', provider)
    monkeypatch.setattr(db_cmd, 'CLAUDE_SETTINGS_PATH', path)
    assert db_cmd._clear_hook_manager_settings() == 0
    assert not os.path.exists(path)


# ── Kimi managed block ────────────────────────────────────────

def _other_kimi_block(label: str = kimi_hooks.DEFAULT_LABEL) -> str:
    return kimi_hooks._render_block(
        ['PostToolUse', 'Stop'], lambda e: _other_manager(e, 'kimi'), 60, label)


def test_kimi_install_spares_another_checkout_s_block(kimi):
    provider, path = kimi
    other = _other_kimi_block()
    with open(path, 'w') as f:
        f.write('default_model = "k"\n\n' + other + '\n')

    hooks_wiring.install_hook_manager(provider, path)
    text = open(path).read()
    assert other in text
    assert hooks_wiring.routed_events(provider, path)

    hooks_wiring.install_hook_manager(provider, path)  # a reinstall, too
    assert open(path).read() == text

    hooks_wiring.uninstall_hook_manager(provider, path)
    assert open(path).read() == 'default_model = "k"\n\n' + other + '\n'


def test_kimi_adopt_takes_over_the_moved_checkout_s_block(kimi):
    provider, path = kimi
    with open(path, 'w') as f:
        f.write(_other_kimi_block() + '\n')
    hooks_wiring.adopt_hook_manager(provider, path)
    text = open(path).read()
    assert '/other/regin' not in text
    assert text.count(kimi_hooks._begin(kimi_hooks.DEFAULT_LABEL)) == 1


# ── debug hook removal (JSON) ─────────────────────────────────

def test_debug_uninstall_writes_nothing_when_the_file_is_missing(claude):
    provider, path = claude
    assert hooks_wiring.uninstall_debug_hook(provider, path)['ok'] is True
    assert not os.path.exists(path)


def test_debug_uninstall_drops_the_event_keys_it_empties(claude):
    provider, path = claude
    keep = {'type': 'command', 'command': 'prettier --write'}
    with open(path, 'w') as f:
        json.dump({'hooks': {'PostToolUse': [{'hooks': [keep]}], 'Weird': 'not-a-list'}}, f)
    hooks_wiring.install_debug_hook(provider, path)
    hooks_wiring.uninstall_debug_hook(provider, path)
    assert json.load(open(path)) == {
        'hooks': {'PostToolUse': [{'hooks': [keep]}], 'Weird': 'not-a-list'}}


def test_json_settings_write_leaves_no_temp_file(claude, tmp_path):
    provider, path = claude
    hooks_wiring.install_hook_manager(provider, path)
    assert [n for n in os.listdir(tmp_path) if n.endswith('.tmp')] == []
    assert json.load(open(path))['hooks']


# ── paths that need quoting ───────────────────────────────────

@pytest.fixture
def spaced_checkout(tmp_path, monkeypatch):
    root = tmp_path / 'my regin'
    (root / '.venv' / 'bin').mkdir(parents=True)
    (root / 'scripts').mkdir()
    os.symlink(sys.executable, root / '.venv' / 'bin' / 'python')
    monkeypatch.setattr(settings, 'project_root', root)
    return root


def test_hook_commands_survive_a_path_with_spaces(spaced_checkout, claude, tmp_path):
    provider, path = claude
    hooks_wiring.install_hook_manager(provider, path)
    status = hooks_wiring.wiring_status(provider, path)['hook_manager']
    assert status['installed'] is True and status['stale'] is False

    command = hooks_wiring.hook_manager_command('Stop', provider)
    assert shlex.split(command)[0] == str(spaced_checkout / '.venv/bin/python')
    assert hooks_wiring.checkout_root(command) == str(spaced_checkout)
    assert not hooks_wiring.is_foreign_hook_manager_command(command)

    marker = tmp_path / 'ran'
    (spaced_checkout / 'scripts' / 'probe.py').write_text(
        f'open({str(marker)!r}, "w").write("ok")\n')
    subprocess.run(['/bin/sh', '-c', hooks_wiring.script_command('probe.py')], check=True)
    assert marker.read_text() == 'ok'


def test_an_unquoted_spaced_command_is_ours_to_rewrite(spaced_checkout, claude):
    provider, path = claude
    broken = f"{spaced_checkout}/.venv/bin/python -P -m hook_manager Stop --agent-type claude"
    with open(path, 'w') as f:
        json.dump({'hooks': {'Stop': [{'hooks': [{'type': 'command', 'command': broken}]}]}}, f)
    assert hooks_wiring.wiring_status(provider, path)['hook_manager']['stale_events'] == ['Stop']
    hooks_wiring.install_hook_manager(provider, path)
    assert broken not in _commands(path)


def test_a_quoted_foreign_command_names_its_checkout(claude):
    command = "'/old place/regin/.venv/bin/python' -P -m hook_manager Stop --agent-type claude"
    assert hooks_wiring.checkout_root(command) == '/old place/regin'
    assert hooks_wiring.is_foreign_hook_manager_command(command)


def test_an_apostrophe_in_the_checkout_path_reads_back_whole(tmp_path, monkeypatch):
    root = tmp_path / "o'brien" / 'regin'
    monkeypatch.setattr(settings, 'project_root', root)
    command = hooks_wiring.script_command('hook_payload_debug.py')
    assert hooks_wiring.checkout_root(command) == str(root)
    assert hooks_wiring.is_debug_hook_command(command)
    assert not hooks_wiring.is_foreign_debug_hook_command(command)


def test_writes_go_through_a_symlinked_settings_file(tmp_path):
    real = tmp_path / 'dotfiles' / 'settings.json'
    real.parent.mkdir()
    real.write_text('{"model": "x"}')
    os.chmod(real, 0o600)
    link = tmp_path / 'settings.json'
    link.symlink_to(real)
    provider = ClaudeProvider({'hook_settings_path': link})

    hooks_wiring.install_hook_manager(provider, str(link))
    assert link.is_symlink()
    assert json.loads(real.read_text())['hooks']
    assert real.stat().st_mode & 0o777 == 0o600


def test_removal_keeps_an_event_key_the_user_left_empty(claude):
    provider, path = claude
    with open(path, 'w') as f:
        json.dump({'hooks': {'Notification': []}}, f)
    hooks_wiring.install_debug_hook(provider, path)
    hooks_wiring.uninstall_debug_hook(provider, path)
    assert json.load(open(path)) == {'hooks': {'Notification': []}}


# ── repair after a checkout move ──────────────────────────────

@pytest.fixture
def cli(claude, monkeypatch):
    provider, path = claude
    monkeypatch.setattr(hooks_cmd, 'list_visible_provider_ids', lambda: ['claude'])
    monkeypatch.setattr(hooks_cmd, 'build_provider', lambda pid: provider)
    sink = io.StringIO()
    monkeypatch.setattr(cli_output, '_stdout', sink)
    monkeypatch.setattr(cli_output, '_stderr', sink)

    def run(*args):
        sink.truncate(0)
        sink.seek(0)
        result = CliRunner().invoke(hooks_cmd.hooks_app, list(args))
        return result, sink.getvalue()
    return provider, path, run


def test_repair_points_at_adopt_after_a_checkout_move(cli):
    provider, path, run = cli
    events = hooks_wiring.hook_manager_events(provider)
    with open(path, 'w') as f:
        json.dump({'hooks': {e: [{'hooks': [
            {'type': 'command', 'command': _other_manager(e), 'timeout': 60}]}]
            for e in events}}, f)
    before = open(path).read()

    result, out = run('repair')
    assert result.exit_code != 0
    assert 'up to date' not in out
    assert 'regin hooks adopt --provider claude`' in out
    assert '/other/regin' in out
    assert open(path).read() == before

    result, _ = run('adopt', '--provider', 'claude')
    assert result.exit_code == 0
    result, out = run('repair')
    assert result.exit_code == 0
    assert 'nothing to repair' in out


def test_repair_hint_names_the_debug_flag(cli):
    provider, path, run = cli
    debug = f'{_OTHER} /other/regin/scripts/hook_payload_debug.py'
    with open(path, 'w') as f:
        json.dump({'hooks': {'Stop': [{'hooks': [{'type': 'command', 'command': debug}]}]}}, f)
    result, out = run('repair')
    assert result.exit_code != 0
    assert 'regin hooks adopt --provider claude --only-debug' in out
