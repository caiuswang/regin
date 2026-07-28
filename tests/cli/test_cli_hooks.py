"""Unit tests for `regin hooks` — the CLI surface that installs and repairs
hook wiring (CAI-21). `repair` is the one-liner `regin doctor` points at, so
it must be a genuine no-op on a healthy checkout.
"""

from __future__ import annotations

import io
import json
import os

import pytest
from typer.testing import CliRunner

from cli import output as cli_output
from cli.commands import hooks as hooks_cmd
from lib import hooks_wiring
from lib import settings as _settings_mod
from lib.providers.claude import ClaudeProvider


_PREFIX = os.path.join(str(_settings_mod.settings.project_root), '.venv/bin/python')


class _Env:
    """CliRunner plus the `cli.output` sink, which the runner cannot capture:
    `output._stdout` is bound to the real stdout at import time."""

    def __init__(self, provider, path, sink):
        self.runner = CliRunner()
        self.provider = provider
        self.path = path
        self._sink = sink

    def run(self, *args):
        self._sink.truncate(0)
        self._sink.seek(0)
        result = self.runner.invoke(hooks_cmd.hooks_app, list(args))
        return result, self._sink.getvalue()


@pytest.fixture
def env(tmp_path, monkeypatch):
    """A single visible `claude` provider whose hooks live in tmp_path."""
    path = tmp_path / 'claude-settings.json'
    provider = ClaudeProvider({'hook_settings_path': path})
    monkeypatch.setattr(hooks_cmd, 'list_visible_provider_ids', lambda: ['claude'])
    monkeypatch.setattr(hooks_cmd, 'build_provider', lambda pid: provider)
    sink = io.StringIO()
    monkeypatch.setattr(cli_output, '_stdout', sink)
    monkeypatch.setattr(cli_output, '_stderr', sink)
    return _Env(provider, path, sink)


def test_status_reports_not_installed(env):
    result, out = env.run('status')
    assert result.exit_code == 0
    assert 'not installed' in out


def test_install_then_status_is_ok(env):
    env.run('install', '--all')
    _, out = env.run('status', '--json')
    rows = {r['hook']: r for r in json.loads(out)}
    assert rows['hook_manager']['installed'] is True
    assert rows['hook_manager']['stale'] is False


def test_repair_is_a_noop_when_wiring_is_current(env):
    env.run('install', '--all')
    before = env.path.read_text()
    result, out = env.run('repair')
    assert result.exit_code == 0
    assert 'nothing to repair' in out
    assert env.path.read_text() == before


def test_repair_rewrites_a_drifted_command(env):
    env.path.write_text(json.dumps({'hooks': {'PostToolUse': [
        {'hooks': [{'type': 'command', 'command': f'{_PREFIX} -m hook_manager PostToolUse'}]},
    ]}}))
    result, out = env.run('repair')
    assert result.exit_code == 0
    assert 'claude/hook_manager' in out
    status = hooks_wiring.wiring_status(env.provider, str(env.path))
    assert status['hook_manager']['stale'] is False
    assert '--agent-type claude' in status['hook_manager']['commands']['PostToolUse'][0]


def test_status_flags_stale_wiring_and_points_at_repair(env):
    env.path.write_text(json.dumps({'hooks': {'PostToolUse': [
        {'hooks': [{'type': 'command', 'command': f'{_PREFIX} -m hook_manager PostToolUse'}]},
    ]}}))
    _, out = env.run('status')
    assert 'STALE' in out
    assert 'regin hooks repair' in out
    # The needs-repair column truncates rather than listing every spec event.
    assert 'more)' in out


def test_only_debug_scopes_the_write(env):
    env.run('install', '--all', '--only-debug')
    status = hooks_wiring.wiring_status(env.provider, str(env.path))
    assert status['debug']['installed'] is True
    assert status['hook_manager']['installed'] is False


def test_remove_undoes_install(env):
    env.run('install', '--all', '--debug')
    env.run('remove', '--all', '--debug')
    status = hooks_wiring.wiring_status(env.provider, str(env.path))
    assert status['hook_manager']['installed'] is False
    assert status['debug']['installed'] is False


def test_unknown_provider_exits_nonzero(env):
    result, out = env.run('status', '--provider', 'nope')
    assert result.exit_code == 1
    assert 'Unknown provider' in out


def test_install_requires_explicit_scope(env):
    result, out = env.run('install')
    assert result.exit_code == 2
    assert '--all' in out
    assert not env.path.exists()


def test_remove_requires_explicit_scope(env):
    result, out = env.run('remove')
    assert result.exit_code == 2
    assert '--all' in out


def test_all_flag_fans_out(env):
    result, _ = env.run('install', '--all')
    assert result.exit_code == 0
    assert hooks_wiring.wiring_status(env.provider, str(env.path))['hook_manager']['installed']


_FOREIGN = '/other/regin/.venv/bin/python -P -m hook_manager PostToolUse --agent-type claude'


def test_status_names_the_other_checkout_and_points_at_adopt(env):
    """`install` here would add a second entry beside the old one, so status
    must not read as a plain "not installed" row."""
    env.path.write_text(json.dumps({'hooks': {'PostToolUse': [
        {'hooks': [{'type': 'command', 'command': _FOREIGN}]},
    ]}}))
    _, out = env.run('status')
    assert '/other/regin' in out
    assert 'regin hooks adopt' in out


def test_adopt_takes_over_another_checkouts_entry(env):
    env.path.write_text(json.dumps({'hooks': {'PostToolUse': [
        {'hooks': [{'type': 'command', 'command': _FOREIGN}]},
    ]}}))
    result, out = env.run('adopt', '--all')
    assert result.exit_code == 0
    assert 'Adopted 1 entry' in out
    status = hooks_wiring.wiring_status(env.provider, str(env.path))
    assert status['hook_manager']['foreign_events'] == []
    assert status['hook_manager']['installed'] is True


def test_adopt_requires_explicit_scope(env):
    result, out = env.run('adopt')
    assert result.exit_code == 2
    assert '--all' in out
    assert not env.path.exists()


def test_repair_survives_a_malformed_config(env):
    """One hand-mangled event must not abort the run — `regin doctor` prints
    this command, so a traceback there strands the user."""
    env.path.write_text(json.dumps({'hooks': {
        'PostToolUse': [{'hooks': [{'type': 'command',
                                    'command': f'{_PREFIX} -m hook_manager PostToolUse'}]}],
        'PreToolUse': 'hand-edited-typo',
    }}))
    result, out = env.run('repair')
    assert result.exit_code == 0
    assert 'Traceback' not in out
    status = hooks_wiring.wiring_status(env.provider, str(env.path))
    assert status['hook_manager']['stale_events'] == []
    assert status['hook_manager']['malformed_events'] == ['PreToolUse']
