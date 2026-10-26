"""Fail-closed guards (feature 181, A5): a registry handler declaring
`fail_closed` gets its own hook entry with `if` + `onFailure: "block"`, while
the shared trace entry stays fail-open and no longer runs it.
"""

from __future__ import annotations

import io
import json

import pytest

from cli.commands import hooks as hooks_cmd
from hook_manager.core import Handler, HookResponse, match_tool
from hook_manager.runner import main, run
from lib import hooks_wiring
from lib.providers.claude import ClaudeProvider
from lib.providers.codex import CodexProvider

_IF = 'Bash(git commit*)'


def _guard(calls: list, *, raises: bool = False) -> Handler:
    def fn(payload):
        calls.append(payload.event)
        if raises:
            raise RuntimeError('guard exploded')
        return HookResponse(decision='block', decision_reason='nope',
                            permission_decision='deny', permission_reason='nope', exit_code=2)
    return Handler(name='commit_guard', events=['PreToolUse'], kind='gate', priority=20,
                   predicate=match_tool('Bash'), fail_closed=True, matcher=_IF, fn=fn)


def _tracer(calls: list) -> Handler:
    return Handler(name='tracer', events=['*'], kind='trace',
                   fn=lambda p: calls.append(p.event) or None)


@pytest.fixture
def claude(tmp_path, monkeypatch):
    calls: list = []
    monkeypatch.setattr(hooks_wiring, '_guard_handlers', lambda: [_guard(calls)])
    path = tmp_path / 'claude-settings.json'
    return ClaudeProvider({'hook_settings_path': path}), str(path)


def _pre_tool_entries(path: str) -> list[dict]:
    with open(path) as f:
        return json.load(f)['hooks']['PreToolUse']


def _bash_payload(command: str = 'git commit -m x') -> str:
    return json.dumps({'hook_event_name': 'PreToolUse', 'session_id': 's-a5', 'cwd': '/tmp',
                       'tool_name': 'Bash', 'tool_input': {'command': command}})


def test_guard_hook_entry_fails_closed(claude):
    provider, path = claude
    hooks_wiring.install_hook_manager(provider, path)

    guard_entries = [e for e in _pre_tool_entries(path)
                     if any(hooks_wiring.is_guard_hook_command(h['command']) for h in e['hooks'])]
    assert len(guard_entries) == 1
    (hook,) = guard_entries[0]['hooks']
    assert hook['if'] == _IF
    assert hook['onFailure'] == 'block'
    assert hook['command'].endswith('--guard commit_guard')


def test_guard_entry_runs_only_its_guard_and_blocks_on_crash():
    guard_calls, trace_calls = [], []
    handlers = [_guard(guard_calls, raises=True), _tracer(trace_calls)]
    out = io.StringIO()
    rc = run('PreToolUse', handlers, _bash_payload(), out, agent_type='claude', guard='commit_guard')

    assert guard_calls == ['PreToolUse']
    assert trace_calls == []
    assert rc == 2
    decision = json.loads(out.getvalue())['hookSpecificOutput']['permissionDecision']
    assert decision == 'deny'


def test_guard_entry_blocks_when_predicate_raises():
    def boom(_payload):
        raise RuntimeError('predicate exploded')
    guard = _guard([])
    guard.predicate = boom
    out = io.StringIO()
    rc = run('PreToolUse', [guard], _bash_payload(), out, agent_type='claude', guard='commit_guard')
    assert rc == 2
    assert json.loads(out.getvalue())['hookSpecificOutput']['permissionDecision'] == 'deny'


def test_guard_entry_lets_through_a_call_the_predicate_rejects():
    calls: list = []
    out = io.StringIO()
    payload = json.dumps({'hook_event_name': 'PreToolUse', 'session_id': 's', 'cwd': '/tmp',
                          'tool_name': 'Read', 'tool_input': {}})
    rc = run('PreToolUse', [_guard(calls)], payload, out, agent_type='claude', guard='commit_guard')
    assert (rc, calls) == (0, [])


def test_inline_guard_crash_stays_fail_open_without_guard_entries():
    out = io.StringIO()
    rc = run('PreToolUse', [_guard([], raises=True)], _bash_payload(), out, agent_type='codex')
    assert rc == 0


def test_trace_hook_entry_stays_fail_open(claude):
    provider, path = claude
    hooks_wiring.install_hook_manager(provider, path)
    trace_hooks = [h for e in _pre_tool_entries(path) for h in e['hooks']
                   if hooks_wiring.is_hook_manager_command(h['command'])]
    assert len(trace_hooks) == 1
    assert 'onFailure' not in trace_hooks[0]
    assert 'if' not in trace_hooks[0]

    guard_calls, trace_calls = [], []
    out = io.StringIO()
    rc = run('PreToolUse', [_guard(guard_calls), _tracer(trace_calls)], _bash_payload(), out,
             agent_type='claude')
    assert guard_calls == []
    assert trace_calls == ['PreToolUse']
    assert rc == 0


def test_guard_cli_flag_reaches_the_runner(monkeypatch):
    seen = {}
    monkeypatch.setattr('hook_manager.runner.run',
                        lambda *a, **kw: seen.update(kw) or 0)
    monkeypatch.setattr('sys.stdin', io.StringIO('{}'))
    main(['PreToolUse', '--agent-type', 'claude', '--guard', 'commit_guard'])
    assert seen['guard'] == 'commit_guard'
    assert seen['agent_type'] == 'claude'


def test_provider_without_guard_entries_keeps_guard_inline(tmp_path, monkeypatch):
    calls: list = []
    monkeypatch.setattr(hooks_wiring, '_guard_handlers', lambda: [_guard(calls)])
    provider = CodexProvider({'hook_settings_path': tmp_path / 'codex.json'})
    assert hooks_wiring.expected_guard_hooks(provider) == {}

    out = io.StringIO()
    run('PreToolUse', [_guard(calls)], _bash_payload(), out, agent_type='codex')
    assert calls == ['PreToolUse']


def test_hooks_status_recognizes_guard_entry(claude, monkeypatch):
    provider, path = claude
    monkeypatch.setattr(hooks_cmd, 'list_visible_provider_ids', lambda: ['claude'])
    monkeypatch.setattr(hooks_cmd, 'build_provider', lambda pid: provider)
    hooks_wiring.install_hook_manager(provider, path)
    before = open(path).read()

    status = hooks_wiring.wiring_status(provider, path)['hook_manager']
    assert status['stale'] is False
    assert status['guard_stale_events'] == [] and status['guard_missing_events'] == []
    assert hooks_wiring.install_hook_manager(provider, path)['msg'] == 'Hook manager already installed'

    from typer.testing import CliRunner
    result = CliRunner().invoke(hooks_cmd.hooks_app, ['repair'])
    assert result.exit_code == 0
    assert open(path).read() == before


def test_missing_or_drifted_guard_is_stale_and_repaired(claude):
    provider, path = claude
    hooks_wiring.install_hook_manager(provider, path)
    with open(path) as f:
        data = json.load(f)
    for entry in data['hooks']['PreToolUse']:
        for h in entry['hooks']:
            h.pop('onFailure', None)
    with open(path, 'w') as f:
        json.dump(data, f)

    status = hooks_wiring.wiring_status(provider, path)['hook_manager']
    assert status['stale'] is True
    assert 'PreToolUse' in status['stale_events']

    hooks_wiring.install_hook_manager(provider, path)
    assert hooks_wiring.wiring_status(provider, path)['hook_manager']['stale'] is False


def test_install_retracts_a_guard_no_longer_registered(claude, monkeypatch):
    provider, path = claude
    hooks_wiring.install_hook_manager(provider, path)
    monkeypatch.setattr(hooks_wiring, '_guard_handlers', lambda: [])

    assert hooks_wiring.wiring_status(provider, path)['hook_manager']['stale'] is True
    hooks_wiring.install_hook_manager(provider, path)
    commands = [h['command'] for e in _pre_tool_entries(path) for h in e['hooks']]
    assert not any(hooks_wiring.is_guard_hook_command(c) for c in commands)
    assert hooks_wiring.wiring_status(provider, path)['hook_manager']['stale'] is False


def test_uninstall_removes_guard_entries(claude):
    provider, path = claude
    hooks_wiring.install_hook_manager(provider, path)
    hooks_wiring.uninstall_hook_manager(provider, path)
    with open(path) as f:
        assert 'hooks' not in json.load(f)
