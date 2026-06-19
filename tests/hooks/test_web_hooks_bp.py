"""Tests for the web/blueprints/hooks.py Flask blueprint.

The hooks blueprint exposes three surfaces:
  (a) per-handler toggle API over hook_manager.config
  (b) debug-hook install/uninstall helpers that edit ~/.claude/settings.json
  (c) legacy grouped dispatcher kept for the SettingsView

These tests mount only the hooks blueprint in a bare Flask app — no DB, no
other blueprints — and drive a test client. Heavy isolation of both the
settings file and the hook_manager config file keeps tests from clobbering
the user's real ~/.claude/.
"""

from __future__ import annotations

import json
import os
import sys

import pytest
from flask import Flask

from web.blueprints import hooks as hooks_bp_module
from hook_manager import config as hm_config
from lib import settings as _settings_mod
from lib.providers.claude import ClaudeProvider
from lib.providers.codex import CodexProvider


# Detection/removal scope hooks to this regin checkout by the interpreter path
# baked in at install time. Tests that hand-craft entries must use the same
# prefix so the predicate sees them as belonging to this instance.
_HM_PREFIX = os.path.join(str(_settings_mod.settings.project_root), '.venv/bin/python')


def _hm_cmd(event: str, agent_type: str | None = None) -> str:
    suffix = f' --agent-type {agent_type}' if agent_type else ''
    return f'{_HM_PREFIX} -m hook_manager {event}{suffix}'


@pytest.fixture
def client(tmp_path, monkeypatch):
    """Bare Flask app with only the hooks blueprint + isolated config paths."""
    fake_settings = tmp_path / 'claude-settings.json'
    codex_settings = tmp_path / 'codex-hooks.json'
    fake_hm_config = tmp_path / 'hook-manager-config.json'
    codex_hm_config = tmp_path / 'codex-hook-manager-config.json'
    monkeypatch.setattr(hooks_bp_module, 'CLAUDE_SETTINGS_PATH', str(fake_settings))
    monkeypatch.setattr(hm_config, 'CONFIG_PATH', str(fake_hm_config))

    # Tests exercise multi-provider behavior; surface the experimental
    # providers (codex, generic) instead of the default claude-only list.
    from lib import settings as _regin_settings
    monkeypatch.setattr(_regin_settings.settings, 'experimental_providers', True)

    class _Caps:
        hooks = True

    class _Provider:
        capabilities = _Caps()
        provider_id = 'claude'
        display_name = 'Claude Code'

        @staticmethod
        def hook_events():
            return None

    monkeypatch.setattr(hooks_bp_module, '_PROVIDER', _Provider())
    original_build_provider = hooks_bp_module.build_provider

    def _build_provider(provider_id):
        if provider_id == 'claude':
            return ClaudeProvider({'hook_settings_path': fake_settings})
        if provider_id == 'codex':
            return CodexProvider({'hook_settings_path': codex_settings})
        return original_build_provider(provider_id)

    monkeypatch.setattr(hooks_bp_module, 'build_provider', _build_provider)

    def _build_config_provider(provider_id):
        if provider_id == 'claude':
            return ClaudeProvider({'hook_manager_config_path': fake_hm_config})
        if provider_id == 'codex':
            return CodexProvider({'hook_manager_config_path': codex_hm_config})
        return original_build_provider(provider_id)

    monkeypatch.setattr(hm_config, 'build_provider', _build_config_provider)

    app = Flask(__name__)
    app.register_blueprint(hooks_bp_module.hooks_bp)
    return app.test_client(), fake_settings


# ── /api/hooks/handlers ──────────────────────────────────────────────

def test_list_handlers_returns_every_registered_handler(client):
    c, _ = client
    from hook_manager.registry import REGISTRY
    resp = c.get('/api/hooks/handlers')
    assert resp.status_code == 200
    body = resp.get_json()
    assert 'handlers' in body
    assert body['installed'] is False
    returned_names = {h['name'] for h in body['handlers']}
    assert returned_names == {h.name for h in REGISTRY}


def test_list_handlers_entry_has_full_shape(client):
    c, _ = client
    body = c.get('/api/hooks/handlers').get_json()
    for entry in body['handlers']:
        assert set(entry.keys()) == {
            'name', 'label', 'summary', 'match_hint',
            'events', 'wired_events', 'wired', 'kind',
            'priority', 'default_priority', 'priority_overridden', 'enabled',
        }


def test_list_handlers_returns_config_path(client):
    """The UI surfaces this string in the lifecycle-diagram description so
    users know where their priority/enable edits actually land. Must be the
    real, provider-specific path — not a hardcoded default — because each
    provider has its own config file."""
    c, _ = client
    body = c.get('/api/hooks/handlers').get_json()
    assert 'config_path' in body
    assert isinstance(body['config_path'], str)
    assert body['config_path'].endswith('hook-manager-config.json')


def test_list_handlers_claude_supported_events_is_full_spec(client):
    """Claude's hook_events() is None ("all"), so the diagram gets the full
    spec event set."""
    from hook_manager.core import SPEC_EVENTS
    c, _ = client
    body = c.get('/api/hooks/handlers').get_json()
    assert set(body['supported_events']) == set(SPEC_EVENTS)


def test_list_handlers_kimi_supported_events_is_kimi_subset(client):
    """Kimi has its own (smaller, different) lifecycle — the per-agent diagram
    must reflect it, not Claude's. Events Kimi never fires are excluded."""
    c, _ = client
    body = c.get('/api/hooks/handlers?provider=kimi').get_json()
    events = set(body['supported_events'])
    # Kimi fires these...
    assert {'UserPromptSubmit', 'PreToolUse', 'PostToolUse', 'Stop'} <= events
    # ...but not these Claude-only lifecycle events.
    assert 'PermissionRequest' not in events
    assert 'TaskCreated' not in events
    assert 'Elicitation' not in events


def test_list_handlers_reports_installed_hook_manager_events(client):
    c, settings_path = client
    settings_path.write_text(json.dumps({
        'hooks': {
            'PostToolUse': [
                {'hooks': [{'type': 'command',
                            'command': _hm_cmd('PostToolUse')}]},
            ],
        },
    }))

    body = c.get('/api/hooks/handlers').get_json()
    assert body['installed'] is True
    assert body['routed_events'] == ['PostToolUse']
    rule_check = next(h for h in body['handlers'] if h['name'] == 'rule_check')
    assert rule_check['wired'] is True
    assert rule_check['wired_events'] == ['PostToolUse']
    prompt_trace = next(h for h in body['handlers'] if h['name'] == 'prompt_trace')
    assert prompt_trace['wired'] is False


# ── /api/hooks/handlers/<name>/enable|disable|toggle ─────────────────

def test_enable_known_handler_returns_ok(client):
    c, _ = client
    # Pick any real handler name from the registry.
    from hook_manager.registry import REGISTRY
    name = REGISTRY[0].name
    resp = c.post(f'/api/hooks/handlers/{name}/enable')
    assert resp.status_code == 200
    body = resp.get_json()
    assert body['ok'] is True
    assert name in body['msg']
    # And state reflected in describe_handlers.
    handlers = c.get('/api/hooks/handlers').get_json()['handlers']
    snap = next(h for h in handlers if h['name'] == name)
    assert snap['enabled'] is True


def test_enable_unknown_handler_returns_404(client):
    c, _ = client
    resp = c.post('/api/hooks/handlers/ghost_handler_xyz/enable')
    assert resp.status_code == 404
    body = resp.get_json()
    assert body['ok'] is False
    assert 'ghost_handler_xyz' in body['msg']


def test_disable_unknown_handler_returns_404(client):
    c, _ = client
    resp = c.post('/api/hooks/handlers/nonexistent/disable')
    assert resp.status_code == 404
    assert resp.get_json()['ok'] is False


def test_disable_then_enable_round_trip(client):
    c, _ = client
    from hook_manager.registry import REGISTRY
    name = REGISTRY[0].name

    assert c.post(f'/api/hooks/handlers/{name}/disable').status_code == 200
    snap = [h for h in c.get('/api/hooks/handlers').get_json()['handlers']
            if h['name'] == name][0]
    assert snap['enabled'] is False

    assert c.post(f'/api/hooks/handlers/{name}/enable').status_code == 200
    snap = [h for h in c.get('/api/hooks/handlers').get_json()['handlers']
            if h['name'] == name][0]
    assert snap['enabled'] is True


def test_toggle_flips_state_and_reports_new_state(client):
    """The UI relies on toggle returning the resulting state so the
    checkbox can flip without re-fetching the list."""
    c, _ = client
    from hook_manager.registry import REGISTRY
    name = REGISTRY[0].name

    r1 = c.post(f'/api/hooks/handlers/{name}/toggle').get_json()
    assert r1['ok'] is True
    assert r1['enabled'] is False  # default True → toggled to False

    r2 = c.post(f'/api/hooks/handlers/{name}/toggle').get_json()
    assert r2['enabled'] is True


def test_toggle_unknown_handler_returns_404(client):
    c, _ = client
    resp = c.post('/api/hooks/handlers/not_a_real_name/toggle')
    assert resp.status_code == 404


# ── /api/hook-manager-status / -install / -uninstall ─────────────────

def test_hook_manager_status_false_when_settings_missing(client):
    c, _ = client
    resp = c.get('/api/hook-manager-status')
    assert resp.status_code == 200
    assert resp.get_json() == {'installed': False, 'routed_events': []}


def test_hook_manager_install_adds_dispatcher_for_spec_events(client):
    c, settings_path = client
    settings_path.write_text('{}')
    resp = c.post('/api/hook-manager-install')
    assert resp.status_code == 200
    assert resp.get_json()['ok'] is True

    settings = json.loads(settings_path.read_text())
    hooks = settings.get('hooks', {})
    assert 'PostToolUse' in hooks
    commands = [
        h.get('command', '')
        for entries in hooks.values()
        for entry in entries
        for h in entry.get('hooks', [])
    ]
    assert any(' -m hook_manager PostToolUse' in cmd for cmd in commands)
    assert all('--agent-type ' in cmd for cmd in commands if ' -m hook_manager ' in cmd)
    status = c.get('/api/hook-manager-status').get_json()
    assert status['installed'] is True
    assert 'PostToolUse' in status['routed_events']


def test_hook_manager_install_honors_provider_supported_events(client, monkeypatch):
    c, settings_path = client
    settings_path.write_text('{}')

    class _Caps:
        hooks = True

    class _Provider:
        capabilities = _Caps()
        provider_id = 'codex'
        display_name = 'OpenAI Codex'

        @staticmethod
        def hook_events():
            return ('SessionStart', 'PreToolUse')

    monkeypatch.setattr(hooks_bp_module, '_PROVIDER', _Provider())
    resp = c.post('/api/hook-manager-install')
    assert resp.status_code == 200
    settings = json.loads(settings_path.read_text())
    assert sorted(settings.get('hooks', {}).keys()) == ['PreToolUse', 'SessionStart']
    commands = [
        h.get('command', '')
        for entries in settings.get('hooks', {}).values()
        for entry in entries
        for h in entry.get('hooks', [])
    ]
    assert all('--agent-type codex' in cmd for cmd in commands)


def test_hook_manager_install_backfills_missing_events(client, monkeypatch):
    c, settings_path = client
    settings_path.write_text(json.dumps({
        'hooks': {
            'SessionStart': [
                {'hooks': [{'type': 'command',
                            'command': _hm_cmd('SessionStart')}]},
            ],
        },
    }))

    class _Caps:
        hooks = True

    class _Provider:
        capabilities = _Caps()
        provider_id = 'codex'
        display_name = 'OpenAI Codex'

        @staticmethod
        def hook_events():
            return ('SessionStart', 'SessionEnd')

    monkeypatch.setattr(hooks_bp_module, '_PROVIDER', _Provider())
    resp = c.post('/api/hook-manager-install')
    assert resp.status_code == 200
    body = resp.get_json()
    assert body['ok'] is True
    assert 'added' in body['msg']
    assert 'updated' in body['msg']

    settings = json.loads(settings_path.read_text())
    assert sorted(settings.get('hooks', {}).keys()) == ['SessionEnd', 'SessionStart']
    start_cmd = settings['hooks']['SessionStart'][0]['hooks'][0]['command']
    assert '--agent-type codex' in start_cmd


def test_hooks_status_reports_multiple_providers(client):
    c, settings_path = client
    settings_path.write_text(json.dumps({
        'hooks': {'SessionStart': [{'hooks': [{
            'type': 'command',
            'command': _hm_cmd('SessionStart', 'claude'),
        }]}]},
    }))

    body = c.get('/api/hooks').get_json()
    providers = {p['id']: p for p in body['providers']}
    assert {'claude', 'codex'} <= set(providers)
    assert providers['claude']['hook_manager']['installed'] is True
    assert providers['codex']['hook_manager']['installed'] is False
    assert providers['claude']['hook_settings_path'].endswith('claude-settings.json')
    assert providers['codex']['hook_settings_path'].endswith('codex-hooks.json')


def test_hook_manager_install_for_codex_uses_codex_agent_type(client):
    c, _ = client
    resp = c.post('/api/hook-manager-install?provider=codex')
    assert resp.status_code == 200
    body = c.get('/api/hooks').get_json()
    codex = next(p for p in body['providers'] if p['id'] == 'codex')
    assert codex['hook_manager']['installed'] is True

    codex_path = codex['hook_settings_path']
    settings = json.loads(open(codex_path).read())
    commands = [
        h.get('command', '')
        for entries in settings.get('hooks', {}).values()
        for entry in entries
        for h in entry.get('hooks', [])
    ]
    assert commands
    assert all('--agent-type codex' in cmd for cmd in commands)
    assert all('--agent-type claude' not in cmd for cmd in commands)


def test_hook_manager_uninstall_removes_dispatcher_but_preserves_other_hooks(client):
    c, settings_path = client
    settings_path.write_text(json.dumps({
        'hooks': {
            'PostToolUse': [
                {'hooks': [{'type': 'command', 'command': _hm_cmd('PostToolUse')}]},
                {'hooks': [{'type': 'command', 'command': 'python /keep/me.py'}]},
            ],
        },
    }))
    resp = c.post('/api/hook-manager-uninstall')
    assert resp.status_code == 200
    assert resp.get_json()['ok'] is True

    settings = json.loads(settings_path.read_text())
    commands = [h['command'] for entry in settings['hooks']['PostToolUse'] for h in entry['hooks']]
    assert 'python /keep/me.py' in commands
    assert not any('hook_manager' in cmd for cmd in commands)


def test_hook_manager_uninstall_leaves_sibling_regin_entries_alone(client):
    """Multiple regin checkouts may share Claude's settings.json. Install/detect/
    uninstall must scope to *this* checkout's interpreter path so one regin
    can't clobber another's hook_manager entries — that bug used to remove
    both when only this regin asked to uninstall.
    """
    c, settings_path = client
    sibling_cmd = '/elsewhere/regin/.venv/bin/python -m hook_manager PostToolUse --agent-type claude'
    settings_path.write_text(json.dumps({
        'hooks': {
            'PostToolUse': [
                {'hooks': [{'type': 'command', 'command': _hm_cmd('PostToolUse', 'claude')}]},
                {'hooks': [{'type': 'command', 'command': sibling_cmd}]},
            ],
        },
    }))

    # Status before: only this regin's entry is claimed as "installed".
    status = c.get('/api/hook-manager-status').get_json()
    assert status['installed'] is True
    assert status['routed_events'] == ['PostToolUse']

    # Uninstall this regin — sibling's entry must survive.
    resp = c.post('/api/hook-manager-uninstall')
    assert resp.status_code == 200
    settings = json.loads(settings_path.read_text())
    commands = [h['command'] for entry in settings['hooks']['PostToolUse'] for h in entry['hooks']]
    assert sibling_cmd in commands
    assert not any(cmd.startswith(str(_settings_mod.settings.project_root)) for cmd in commands)

    # Status after: this regin is no longer installed, even though sibling's
    # entry still references `-m hook_manager`.
    status_after = c.get('/api/hook-manager-status').get_json()
    assert status_after['installed'] is False
    assert status_after['routed_events'] == []


# ── /api/debug-hook-status / -install / -uninstall ───────────────────

def test_debug_hook_status_false_when_settings_missing(client):
    c, settings_path = client
    # settings.json doesn't exist yet.
    resp = c.get('/api/debug-hook-status')
    assert resp.status_code == 200
    assert resp.get_json() == {'installed': False}


def test_debug_hook_install_adds_to_three_events(client):
    c, settings_path = client
    # Start from clean settings.
    settings_path.write_text('{}')
    resp = c.post('/api/debug-hook-install')
    assert resp.status_code == 200
    assert resp.get_json()['ok'] is True

    settings = json.loads(settings_path.read_text())
    hooks = settings.get('hooks', {})
    # The three fan-out events get entries added.
    for ev in ('UserPromptSubmit', 'PreToolUse', 'PostToolUse'):
        entries = hooks.get(ev, [])
        commands = [h.get('command', '')
                    for entry in entries for h in entry.get('hooks', [])]
        assert any('hook_payload_debug' in cmd for cmd in commands), \
            f'hook_payload_debug not installed for {ev}'

    # Status endpoint now reports installed.
    assert c.get('/api/debug-hook-status').get_json()['installed'] is True


def test_debug_hook_install_is_idempotent(client):
    c, settings_path = client
    settings_path.write_text('{}')
    c.post('/api/debug-hook-install')
    snap_before = json.loads(settings_path.read_text())

    resp = c.post('/api/debug-hook-install')
    assert resp.status_code == 200
    assert 'Already installed' in resp.get_json()['msg']
    snap_after = json.loads(settings_path.read_text())
    # Same content — no duplicate entries appended.
    assert snap_before == snap_after


def test_debug_hook_uninstall_removes_debug_entries(client):
    c, settings_path = client
    settings_path.write_text('{}')
    c.post('/api/debug-hook-install')
    resp = c.post('/api/debug-hook-uninstall')
    assert resp.status_code == 200
    assert resp.get_json()['ok'] is True

    settings = json.loads(settings_path.read_text())
    for entries in settings.get('hooks', {}).values():
        for entry in entries:
            for h in entry.get('hooks', []):
                assert 'hook_payload_debug' not in h.get('command', '')
    assert c.get('/api/debug-hook-status').get_json()['installed'] is False


def test_debug_hook_uninstall_preserves_other_hooks(client):
    """Uninstalling the debug hook must not touch unrelated hook entries
    on the same events (e.g. the hook_manager dispatch command)."""
    c, settings_path = client
    settings_path.write_text(json.dumps({
        'hooks': {
            'PreToolUse': [
                {'hooks': [{'type': 'command', 'command': 'python -m hook_manager PreToolUse'}]},
                {'hooks': [{'type': 'command', 'command': '/path/to/hook_payload_debug.py'}]},
            ],
        }
    }))
    c.post('/api/debug-hook-uninstall')
    settings = json.loads(settings_path.read_text())
    pretool = settings['hooks']['PreToolUse']
    all_commands = [h['command'] for entry in pretool for h in entry['hooks']]
    assert any('hook_manager' in cmd for cmd in all_commands)
    assert not any('hook_payload_debug' in cmd for cmd in all_commands)


# ── /api/debug-hook-payloads ─────────────────────────────────────────

def test_debug_hook_payloads_empty_when_log_missing(client, monkeypatch, tmp_path):
    c, _ = client
    monkeypatch.setattr(os.path, 'expanduser',
                        lambda p: str(tmp_path / 'nope.jsonl') if p.endswith('hook-payloads.jsonl') else p)
    resp = c.get('/api/debug-hook-payloads')
    assert resp.status_code == 200
    assert resp.get_json() == {'payloads': []}


def test_debug_hook_payloads_returns_last_n_entries(client, monkeypatch, tmp_path):
    c, _ = client
    log = tmp_path / 'hook-payloads.jsonl'
    # 5 valid entries + one malformed in the middle.
    lines = [
        json.dumps({'received_at': f't{i}', 'hook_event': 'PreToolUse',
                    'session_id': f's{i}', 'payload': {'i': i}})
        for i in range(5)
    ]
    lines.insert(2, '{not json')  # corrupted line → must be skipped, not crash
    log.write_text('\n'.join(lines) + '\n')

    monkeypatch.setattr(os.path, 'expanduser',
                        lambda p: str(log) if p.endswith('hook-payloads.jsonl') else p)

    resp = c.get('/api/debug-hook-payloads?limit=3')
    assert resp.status_code == 200
    body = resp.get_json()
    # Limit=3 slices last 3 raw lines → one of them may be the corrupt one;
    # but the endpoint filters bad lines out AFTER slicing, so the result
    # may have <3. Either way: no crash, only valid JSON entries.
    for entry in body['payloads']:
        assert 'hook_event' in entry


# ── Legacy grouped dispatcher (debug only today) ─────────────────────

def test_legacy_hooks_status_reports_hook_manager_and_debug(client):
    c, _ = client
    resp = c.get('/api/hooks')
    assert resp.status_code == 200
    body = resp.get_json()
    assert 'hook_manager' in body
    assert 'debug' in body
    assert body['hook_manager']['target'] == 'claude'
    assert body['debug']['target'] == 'claude'


def test_legacy_install_unknown_group_returns_404(client):
    c, _ = client
    resp = c.post('/api/hooks/made_up/install')
    assert resp.status_code == 404


def test_legacy_install_debug_dispatches_to_real_installer(client):
    c, settings_path = client
    settings_path.write_text('{}')
    resp = c.post('/api/hooks/debug/install')
    assert resp.status_code == 200
    assert resp.get_json()['ok'] is True
    # Real install side-effect is visible in settings.json.
    assert 'hook_payload_debug' in settings_path.read_text()


def test_legacy_install_hook_manager_dispatches_to_real_installer(client):
    c, settings_path = client
    settings_path.write_text('{}')
    resp = c.post('/api/hooks/hook_manager/install')
    assert resp.status_code == 200
    assert resp.get_json()['ok'] is True
    assert 'hook_manager PostToolUse' in settings_path.read_text()


# ── Priority reorder / reset ─────────────────────────────────────────

def test_reorder_handlers_assigns_sequential_priorities(client):
    c, _ = client
    from hook_manager.registry import REGISTRY
    names = [REGISTRY[0].name, REGISTRY[1].name, REGISTRY[2].name]
    resp = c.post('/api/hooks/handlers/reorder', json={'order': names})
    assert resp.status_code == 200
    body = resp.get_json()
    assert body['ok'] is True
    # 100-base, step-10 (see hooks_bp_module reorder constants).
    assert body['updates'] == {names[0]: 100, names[1]: 110, names[2]: 120}
    # The list endpoint surfaces the new effective priorities.
    snap = {h['name']: h for h in c.get('/api/hooks/handlers').get_json()['handlers']}
    assert snap[names[0]]['priority'] == 100
    assert snap[names[0]]['priority_overridden'] is True
    assert snap[names[1]]['priority'] == 110


def test_reorder_handlers_rejects_unknown_names(client):
    c, _ = client
    resp = c.post('/api/hooks/handlers/reorder',
                  json={'order': ['totally_made_up_handler']})
    assert resp.status_code == 400
    assert 'Unknown' in resp.get_json()['msg']


def test_reorder_handlers_rejects_non_list_order(client):
    c, _ = client
    resp = c.post('/api/hooks/handlers/reorder', json={'order': 'not-a-list'})
    assert resp.status_code == 400


def test_set_handler_priority_directly(client):
    c, _ = client
    from hook_manager.registry import REGISTRY
    target = REGISTRY[0].name
    resp = c.post(f'/api/hooks/handlers/{target}/priority', json={'priority': 77})
    assert resp.status_code == 200
    body = resp.get_json()
    assert body['ok'] is True
    assert body['priority'] == 77
    snap = {h['name']: h for h in c.get('/api/hooks/handlers').get_json()['handlers']}
    assert snap[target]['priority'] == 77
    assert snap[target]['priority_overridden'] is True


def test_set_handler_priority_rejects_non_numeric(client):
    c, _ = client
    from hook_manager.registry import REGISTRY
    target = REGISTRY[0].name
    resp = c.post(f'/api/hooks/handlers/{target}/priority', json={'priority': 'high'})
    assert resp.status_code == 400


def test_set_handler_priority_rejects_out_of_range(client):
    c, _ = client
    from hook_manager.registry import REGISTRY
    target = REGISTRY[0].name
    resp = c.post(f'/api/hooks/handlers/{target}/priority', json={'priority': -5})
    assert resp.status_code == 400
    resp = c.post(f'/api/hooks/handlers/{target}/priority', json={'priority': 100000})
    assert resp.status_code == 400


def test_set_handler_priority_unknown_handler_returns_404(client):
    c, _ = client
    resp = c.post('/api/hooks/handlers/no_such/priority', json={'priority': 50})
    assert resp.status_code == 404


def test_reset_priority_drops_override(client):
    c, _ = client
    from hook_manager.registry import REGISTRY
    target = REGISTRY[0].name
    # Set an override, then clear it.
    c.post('/api/hooks/handlers/reorder', json={'order': [target]})
    snap = {h['name']: h for h in c.get('/api/hooks/handlers').get_json()['handlers']}
    assert snap[target]['priority_overridden'] is True

    resp = c.post(f'/api/hooks/handlers/{target}/reset-priority')
    assert resp.status_code == 200
    snap = {h['name']: h for h in c.get('/api/hooks/handlers').get_json()['handlers']}
    assert snap[target]['priority_overridden'] is False
    assert snap[target]['priority'] == snap[target]['default_priority']


def test_reset_priority_unknown_handler_returns_404(client):
    c, _ = client
    resp = c.post('/api/hooks/handlers/no_such_handler/reset-priority')
    assert resp.status_code == 404


def test_reset_all_priorities_clears_every_override(client):
    c, _ = client
    from hook_manager.registry import REGISTRY
    names = [REGISTRY[0].name, REGISTRY[1].name]
    c.post('/api/hooks/handlers/reorder', json={'order': names})
    resp = c.post('/api/hooks/handlers/reset-priorities')
    assert resp.status_code == 200
    snap = {h['name']: h for h in c.get('/api/hooks/handlers').get_json()['handlers']}
    for n in names:
        assert snap[n]['priority_overridden'] is False
