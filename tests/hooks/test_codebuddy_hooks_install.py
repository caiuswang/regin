"""Feature #168 S3 — `regin hooks` wires the codebuddy family like Claude.

Every case runs under a tmp HOME, so `~/.codebuddy` / `~/.workbuddy` resolve
into tmp_path and the developer's real configs are never touched.
"""

from __future__ import annotations

import io
import json
import shlex

import pytest
from typer.testing import CliRunner

from cli import output as cli_output
from cli.commands import hooks as hooks_cmd
from lib import hooks_wiring
from lib import settings as settings_mod
from lib.providers import build_provider


USER_PRE_TOOL = {
    'matcher': 'Bash',
    'hooks': [{'type': 'command', 'command': 'echo "用户自己的 hook" >> ~/audit.log'}],
}
USER_SETTINGS = {
    'model': 'fast-model',
    'permissions': {'allow': ['Bash(ls:*)']},
    'hooks': {'PreToolUse': [USER_PRE_TOOL]},
    'env': {'NOTE': '保留'},
}


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv('HOME', str(tmp_path))
    monkeypatch.setattr(settings_mod.settings, 'codebuddy_config_dir', None)
    monkeypatch.setattr(settings_mod.settings, 'workbuddy_config_dir', None)
    monkeypatch.setattr(settings_mod.settings, 'active_provider', 'claude')
    monkeypatch.setattr(settings_mod.settings, 'experimental_providers', False)
    monkeypatch.setattr(settings_mod.settings, 'providers', {})
    return tmp_path


@pytest.fixture
def run(monkeypatch):
    sink = io.StringIO()
    monkeypatch.setattr(cli_output, '_stdout', sink)
    monkeypatch.setattr(cli_output, '_stderr', sink)
    runner = CliRunner()

    def _run(*args):
        sink.truncate(0)
        sink.seek(0)
        result = runner.invoke(hooks_cmd.hooks_app, list(args))
        return result, sink.getvalue()
    return _run


def _user_entries(data: dict) -> list:
    return [e for e in data['hooks']['PreToolUse']
            if not any(hooks_wiring.is_hook_manager_command(h.get('command', ''))
                       for h in e.get('hooks', []))]


def _routed_commands(data: dict, event: str) -> list[str]:
    return [h['command'] for h in hooks_wiring.hook_entries(data['hooks'][event])
            if hooks_wiring.is_hook_manager_command(h['command'])]


def _assert_one_claude_shaped_command_per_event(data: dict, provider) -> None:
    events = hooks_wiring.hook_manager_events(provider)
    assert events
    for event in events:
        commands = _routed_commands(data, event)
        assert len(commands) == 1, event
        assert shlex.split(commands[0])[1:] == [
            '-P', '-m', 'hook_manager', event, '--agent-type', provider.provider_id]


def _assert_user_config_untouched(data: dict, text: str) -> None:
    for key in ('model', 'permissions', 'env'):
        assert data[key] == USER_SETTINGS[key]
    assert _user_entries(data) == [USER_PRE_TOOL]
    for key in ('permissions', 'env'):
        nested = json.dumps(USER_SETTINGS[key], indent=2, ensure_ascii=False)
        assert nested.replace('\n', '\n  ') in text
    assert json.dumps(USER_PRE_TOOL['hooks'][0]['command'], ensure_ascii=False) in text


@pytest.mark.parametrize('provider_id,root', [
    ('codebuddy', '.codebuddy'),
    ('workbuddy', '.workbuddy'),
])
def test_codebuddy_family_hooks_install_is_claude_shaped_and_merge_safe(
        home, run, provider_id, root):
    settings_path = home / root / 'settings.json'
    settings_path.parent.mkdir()
    settings_path.write_text(json.dumps(USER_SETTINGS, indent=2, ensure_ascii=False),
                             encoding='utf-8')
    provider = build_provider(provider_id)
    assert provider.hook_settings_path() == settings_path

    result, out = run('install', '--provider', provider_id)
    assert result.exit_code == 0, out

    text = settings_path.read_text(encoding='utf-8')
    data = json.loads(text)
    _assert_one_claude_shaped_command_per_event(data, provider)
    _assert_user_config_untouched(data, text)
    _assert_status_reports_no_drift(run, provider_id)
    _assert_repair_is_a_noop(run, provider_id, settings_path)


def _assert_status_reports_no_drift(run, provider_id: str) -> None:
    result, out = run('status', '--provider', provider_id, '--json')
    assert result.exit_code == 0, out
    manager = {r['hook']: r for r in json.loads(out)}['hook_manager']
    assert (manager['provider'], manager['installed'], manager['stale']) == \
        (provider_id, True, False)
    assert manager['missing_events'] == manager['stale_events'] == []


def _assert_repair_is_a_noop(run, provider_id: str, settings_path) -> None:
    before = settings_path.read_bytes()
    result, out = run('repair', '--provider', provider_id)
    assert result.exit_code == 0, out
    assert 'nothing to repair' in out
    assert settings_path.read_bytes() == before


def test_hookless_provider_is_never_written(home, run, monkeypatch):
    provider = build_provider('workbuddy')
    monkeypatch.setattr(type(provider), 'capabilities',
                        provider.capabilities.__class__(skills=True, hooks=False,
                                                        sessions=True, transcript_usage=True))
    result, out = run('install', '--provider', 'workbuddy')
    assert result.exit_code == 0, out
    assert 'hooks not supported' in out
    assert not (home / '.workbuddy').exists()
