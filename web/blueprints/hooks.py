"""Claude Code hook install/uninstall + per-handler toggle endpoints.

Post-migration to `hook_manager` (commits 3348de0, fa3922e), every Claude
event is handled by a single unified router in `~/.claude/settings.json`
(`python -m hook_manager <Event>`) that dispatches to handlers defined in
`hook_manager/handlers/`. Individual handlers are enabled/disabled via
`hook_manager.config` (persisted at `~/.claude/hook-manager-config.json`),
not by editing settings.json — that's what the web UI Settings page drives.

Surface:

- `GET  /api/hooks/handlers`                 — list every registered handler
- `POST /api/hooks/handlers/<name>/enable`   — enable one handler
- `POST /api/hooks/handlers/<name>/disable`  — disable one handler
- `POST /api/hooks/handlers/<name>/toggle`   — flip current state

Debug hook (separate mechanism — writes settings.json directly because it
predates hook_manager and stays opt-in):
- `GET  /api/debug-hook-status`
- `POST /api/debug-hook-install` / `/api/debug-hook-uninstall`
- `GET  /api/debug-hook-payloads`

Legacy aggregated dashboard (kept for backward-compat with the SettingsView):
- `GET  /api/hooks`                 — aggregate status (debug only today)
- `POST /api/hooks/<name>/install`  — dispatcher (debug only today)
- `POST /api/hooks/<name>/uninstall`
"""

from __future__ import annotations

import json
import os
import re
import shlex

from flask import Blueprint, request, jsonify

from lib.settings import settings
from lib.providers import (
    active_provider_id,
    build_provider,
    get_active_provider,
    is_provider_id,
    list_visible_provider_ids,
)
from lib.providers import kimi_hooks
from hook_manager.core import SPEC_EVENTS


hooks_bp = Blueprint('hooks', __name__)


# ── Shared path helpers + settings helpers ──────────────────

_HOOK_MANAGER_CMD_RE = re.compile(r'(^|\s)-m\s+hook_manager(?:\s|$)')
_PROVIDER = None
CLAUDE_SETTINGS_PATH: str | None = None
HOOK_PAYLOAD_LOG_PATH: str | None = None


def _provider():
    return _PROVIDER or get_active_provider()


def _provider_from_request():
    raw = request.args.get('provider') or request.headers.get('X-Regin-Test-Provider')
    if isinstance(raw, str) and raw.strip():
        pid = raw.strip().lower()
        if is_provider_id(pid):
            return build_provider(pid)
        return None
    return _provider()


def _provider_or_error():
    provider = _provider_from_request()
    if provider is None:
        return None, (jsonify({
            'ok': False,
            'msg': f"Unknown provider: {request.args.get('provider')}",
        }), 404)
    return provider, None


def _hook_settings_path(provider=None) -> str:
    if CLAUDE_SETTINGS_PATH and (
        provider is None
        or getattr(provider, 'provider_id', None) == getattr(_provider(), 'provider_id', None)
    ):
        return CLAUDE_SETTINGS_PATH
    provider = provider or _provider()
    return str(provider.hook_settings_path())


def _hook_payload_log_path(provider=None) -> str:
    if HOOK_PAYLOAD_LOG_PATH and (
        provider is None
        or getattr(provider, 'provider_id', None) == getattr(_provider(), 'provider_id', None)
    ):
        return HOOK_PAYLOAD_LOG_PATH
    provider = provider or _provider()
    return str(provider.hook_payload_log_path())


def _cmd(script_name: str) -> str:
    return (
        f"{os.path.join(str(settings.project_root), '.venv/bin/python')} "
        f"{os.path.join(str(settings.project_root), 'scripts', script_name)}"
    )


def _read_claude_settings(provider=None) -> dict:
    try:
        with open(_hook_settings_path(provider), 'r') as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _write_claude_settings(settings: dict, provider=None) -> None:
    path = _hook_settings_path(provider)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w') as f:
        json.dump(settings, f, indent=2)


def _hook_manager_interpreter_prefix() -> str:
    """The exact interpreter token that install bakes into commands.

    Used to scope detection/removal to *this* regin checkout — two regin
    instances on one machine share a Claude settings.json, and matching by
    a bare `-m hook_manager` substring would let one instance's uninstall
    clobber another's entries. Trailing space anchors to a token boundary
    so `/foo/regin` doesn't match `/foo/regin-fork`.
    """
    return os.path.join(str(settings.project_root), '.venv/bin/python') + ' '


# Matches a stale `env KEY=VAL …` prefix from an earlier fix iteration. Kept
# only for detection so uninstall/reinstall still sees those entries as ours
# and replaces them; new installs no longer emit any env prefix.
_LEADING_ENV_RE = re.compile(r'^(?:/usr/bin/)?env(?:\s+[A-Za-z_][A-Za-z0-9_]*=\S*)+\s+')


def _is_hook_manager_command(command: str) -> bool:
    if not isinstance(command, str):
        return False
    if not _HOOK_MANAGER_CMD_RE.search(command):
        return False
    interpreter = _hook_manager_interpreter_prefix()
    idx = command.find(interpreter)
    if idx < 0:
        return False
    prefix = command[:idx]
    return prefix == '' or _LEADING_ENV_RE.match(prefix) is not None


def _hook_manager_routed_events(settings: dict) -> set[str]:
    routed: set[str] = set()
    hooks = settings.get('hooks', {})
    if not isinstance(hooks, dict):
        return routed

    for event_name, entries in hooks.items():
        if not isinstance(entries, list):
            continue
        for entry in entries:
            for hook in entry.get('hooks', []):
                if _is_hook_manager_command(hook.get('command', '')):
                    routed.add(event_name)
                    break
            if event_name in routed:
                break
    return routed


# ── Debug hook (multi-event fan-out) ──────────────────────────

DEBUG_HOOK_COMMAND = _cmd('hook_payload_debug.py')
_DEBUG_EVENTS = ('UserPromptSubmit', 'PostToolUse', 'PreToolUse')


def _debug_hook_command(provider=None) -> str:
    """Per-provider debug-hook command.

    Claude keeps the bare command (logs to ``~/.claude`` and emits Claude-style
    stdout). Other agents get their own log path appended — Kimi logs to
    ``~/.kimi-code`` not ``~/.claude``, so without this its debug payloads never
    reach the viewer — plus ``--silent`` when the agent renders raw hook stdout
    (Kimi), so we never print a Claude-only response into its UI.
    """
    provider = provider or _provider()
    if provider.provider_id == 'claude':
        return DEBUG_HOOK_COMMAND
    parts = [DEBUG_HOOK_COMMAND, shlex.quote(str(provider.hook_payload_log_path()))]
    if getattr(provider, 'hook_output_format', 'claude') != 'claude':
        parts.append('--silent')
    return ' '.join(parts)
# Delimited-block label for the debug hook in a TOML config (Kimi), kept
# separate from the hook_manager block so the two never clobber each other.
_DEBUG_LABEL = 'debug'
_HOOK_MANAGER_TIMEOUT = 60


def _hook_manager_command(event_name: str, provider=None) -> str:
    # `-P` is the CLI form of PYTHONSAFEPATH=1: it stops `python -m` from
    # injecting `sys.path[0] = cwd`. Without it, a hook installed from one
    # regin checkout would silently import another checkout's `hook_manager`
    # when Claude ran from there, sending spans to the wrong DB.
    provider = provider or _provider()
    agent_type = getattr(provider, 'provider_id', None) or 'generic'
    return (
        f"{os.path.join(str(settings.project_root), '.venv/bin/python')} -P "
        f"-m hook_manager {shlex.quote(event_name)} "
        f"--agent-type {shlex.quote(agent_type)}"
    )


def _debug_hook_installed(settings: dict) -> bool:
    for entry in settings.get('hooks', {}).values():
        for e in (entry if isinstance(entry, list) else []):
            for h in e.get('hooks', []):
                if 'hook_payload_debug' in h.get('command', ''):
                    return True
    return False


def _hook_manager_installed(settings: dict) -> bool:
    return bool(_hook_manager_routed_events(settings))


def _hook_manager_block(event_name: str, provider=None) -> dict:
    return {
        'hooks': [{
            'type': 'command',
            'command': _hook_manager_command(event_name, provider),
            'timeout': _HOOK_MANAGER_TIMEOUT,
        }]
    }


def _require_hooks_capability(provider):
    if provider.capabilities.hooks:
        return None
    return jsonify({
        'ok': False,
        'msg': f'hooks are not supported for provider {provider.display_name}',
    }), 400


@hooks_bp.route('/api/debug-hook-status')
def api_debug_hook_status():
    provider, error = _provider_or_error()
    if error is not None:
        return error
    return jsonify({'installed': bool(_debug_hook_routed(provider))})


@hooks_bp.route('/api/hook-manager-status')
def api_hook_manager_status():
    provider, error = _provider_or_error()
    if error is not None:
        return error
    routed_events = _routed_events(provider)
    return jsonify({'installed': bool(routed_events), 'routed_events': sorted(routed_events)})


def _is_toml_provider(provider) -> bool:
    return getattr(provider, 'hook_config_format', 'json') == 'toml'


def _routed_events(provider) -> set[str]:
    """Events routed to this regin checkout's hook_manager, format-agnostic."""
    if _is_toml_provider(provider):
        return kimi_hooks.routed_events(_hook_settings_path(provider), _is_hook_manager_command)
    return _hook_manager_routed_events(_read_claude_settings(provider))


def _merge_hook_manager_blocks(hooks: dict, events, provider) -> tuple[int, int]:
    """Add/refresh hook_manager command blocks in a settings.json `hooks` map."""
    added = 0
    updated = 0
    for event_name in sorted(events):
        event_hooks = hooks.setdefault(event_name, [])
        command = _hook_manager_command(event_name, provider)
        already_present = False
        for entry in event_hooks:
            for h in entry.get('hooks', []):
                if not _is_hook_manager_command(h.get('command', '')):
                    continue
                already_present = True
                if h.get('command') != command:
                    h['command'] = command
                    updated += 1
        if not already_present:
            event_hooks.append(_hook_manager_block(event_name, provider))
            added += 1
    return added, updated


def _json_install_hook_manager(provider):
    settings = _read_claude_settings(provider)
    hooks = settings.setdefault('hooks', {})
    supported_events = provider.hook_events() or tuple(sorted(SPEC_EVENTS))
    added, updated = _merge_hook_manager_blocks(hooks, supported_events, provider)
    if added == 0 and updated == 0:
        return jsonify({'ok': True, 'msg': 'Hook manager already installed'})
    _write_claude_settings(settings, provider)
    parts = []
    if added:
        parts.append(f'{added} added')
    if updated:
        parts.append(f'{updated} updated')
    return jsonify({'ok': True, 'msg': f"Hook manager installed for {provider.display_name} events ({', '.join(parts)})"})


def _toml_install_hook_manager(provider):
    path = _hook_settings_path(provider)
    before = kimi_hooks.routed_events(path, _is_hook_manager_command)
    events = provider.hook_events() or tuple(sorted(SPEC_EVENTS))
    kimi_hooks.install(
        path,
        list(events),
        lambda event_name: _hook_manager_command(event_name, provider),
        timeout=_HOOK_MANAGER_TIMEOUT,
    )
    after = kimi_hooks.routed_events(path, _is_hook_manager_command)
    if before == after:
        return jsonify({'ok': True, 'msg': 'Hook manager already installed'})
    return jsonify({
        'ok': True,
        'msg': f"Hook manager installed for {provider.display_name} events ({len(after)} routed)",
    })


@hooks_bp.route('/api/hook-manager-install', methods=['POST'])
def api_hook_manager_install():
    provider, error = _provider_or_error()
    if error is not None:
        return error
    unsupported = _require_hooks_capability(provider)
    if unsupported is not None:
        return unsupported
    if _is_toml_provider(provider):
        return _toml_install_hook_manager(provider)
    return _json_install_hook_manager(provider)


def _strip_hook_manager_blocks(hooks: dict) -> int:
    """Remove hook_manager command blocks from a settings.json `hooks` map."""
    removed = 0
    for event_name in list(hooks.keys()):
        entries = hooks[event_name]
        if not isinstance(entries, list):
            continue
        filtered = []
        for entry in entries:
            entry_hooks = [h for h in entry.get('hooks', [])
                           if not _is_hook_manager_command(h.get('command', ''))]
            removed += len(entry.get('hooks', [])) - len(entry_hooks)
            if entry_hooks:
                next_entry = dict(entry)
                next_entry['hooks'] = entry_hooks
                filtered.append(next_entry)
        if filtered:
            hooks[event_name] = filtered
        else:
            del hooks[event_name]
    return removed


def _json_uninstall_hook_manager(provider):
    settings = _read_claude_settings(provider)
    hooks = settings.get('hooks', {})
    removed = _strip_hook_manager_blocks(hooks) if isinstance(hooks, dict) else 0
    if not hooks:
        settings.pop('hooks', None)
    _write_claude_settings(settings, provider)
    return jsonify({'ok': True, 'msg': 'Hook manager removed' if removed else 'Hook manager was not installed'})


@hooks_bp.route('/api/hook-manager-uninstall', methods=['POST'])
def api_hook_manager_uninstall():
    provider, error = _provider_or_error()
    if error is not None:
        return error
    unsupported = _require_hooks_capability(provider)
    if unsupported is not None:
        return unsupported
    if _is_toml_provider(provider):
        removed = kimi_hooks.uninstall(_hook_settings_path(provider))
        return jsonify({'ok': True, 'msg': 'Hook manager removed' if removed else 'Hook manager was not installed'})
    return _json_uninstall_hook_manager(provider)


def _is_debug_hook_command(command: str) -> bool:
    return isinstance(command, str) and 'hook_payload_debug' in command


def _toml_debug_routed(provider) -> set[str]:
    """Debug events routed via the TOML config (Kimi)."""
    return kimi_hooks.routed_events(
        _hook_settings_path(provider), _is_debug_hook_command)


def _debug_hook_routed(provider) -> set[str]:
    """Events the debug hook is installed for, format-agnostic."""
    if _is_toml_provider(provider):
        return _toml_debug_routed(provider)
    if _debug_hook_installed(_read_claude_settings(provider)):
        return set(_DEBUG_EVENTS)
    return set()


def _toml_debug_install(provider):
    path = _hook_settings_path(provider)
    if _toml_debug_routed(provider):
        return jsonify({'ok': True, 'msg': 'Already installed'})
    # Owns its own `debug`-labelled block; the hook_manager block (if any)
    # is left untouched.
    command = _debug_hook_command(provider)
    kimi_hooks.install(
        path, list(_DEBUG_EVENTS), lambda _event: command,
        timeout=10, label=_DEBUG_LABEL,
    )
    return jsonify({'ok': True, 'msg': 'Debug hook installed for all events'})


@hooks_bp.route('/api/debug-hook-install', methods=['POST'])
def api_debug_hook_install():
    provider, error = _provider_or_error()
    if error is not None:
        return error
    unsupported = _require_hooks_capability(provider)
    if unsupported is not None:
        return unsupported
    if _is_toml_provider(provider):
        return _toml_debug_install(provider)
    settings = _read_claude_settings(provider)
    if _debug_hook_installed(settings):
        return jsonify({'ok': True, 'msg': 'Already installed'})
    hooks = settings.setdefault('hooks', {})
    command = _debug_hook_command(provider)
    for event_name in _DEBUG_EVENTS:
        event_hooks = hooks.setdefault(event_name, [])
        event_hooks.append({
            'hooks': [{
                'type': 'command',
                'command': command,
                'timeout': 10,
            }]
        })
    _write_claude_settings(settings, provider)
    return jsonify({'ok': True, 'msg': 'Debug hook installed for all events'})


@hooks_bp.route('/api/debug-hook-uninstall', methods=['POST'])
def api_debug_hook_uninstall():
    provider, error = _provider_or_error()
    if error is not None:
        return error
    unsupported = _require_hooks_capability(provider)
    if unsupported is not None:
        return unsupported
    if _is_toml_provider(provider):
        kimi_hooks.uninstall(_hook_settings_path(provider), label=_DEBUG_LABEL)
        return jsonify({'ok': True, 'msg': 'Debug hook removed'})
    settings = _read_claude_settings(provider)
    for event_name in list(settings.get('hooks', {}).keys()):
        event_hooks = settings['hooks'][event_name]
        if not isinstance(event_hooks, list):
            continue
        filtered = []
        for entry in event_hooks:
            entry_hooks = [h for h in entry.get('hooks', [])
                           if 'hook_payload_debug' not in h.get('command', '')]
            if entry_hooks:
                entry['hooks'] = entry_hooks
                filtered.append(entry)
        settings['hooks'][event_name] = filtered
    _write_claude_settings(settings, provider)
    return jsonify({'ok': True, 'msg': 'Debug hook removed'})


@hooks_bp.route('/api/debug-hook-payloads')
def api_debug_hook_payloads():
    provider, error = _provider_or_error()
    if error is not None:
        return error
    if provider.provider_id == "claude":
        # Keep HOME-sensitive behavior for existing tests/setups.
        log_path = os.path.expanduser('~/.claude/hook-payloads.jsonl')
    else:
        log_path = _hook_payload_log_path(provider)
    if not os.path.exists(log_path):
        return jsonify({'payloads': []})
    limit = min(int(request.args.get('limit', 100)), 500)
    lines = []
    with open(log_path, 'r') as f:
        for line in f:
            lines.append(line.strip())
    payloads = []
    for line in lines[-limit:]:
        try:
            payloads.append(json.loads(line))
        except (json.JSONDecodeError, ValueError):
            continue
    return jsonify({'payloads': payloads})


# ── Per-handler toggle API (hook_manager handlers) ───────────

@hooks_bp.route('/api/hooks/handlers')
def api_list_handlers():
    """Return every registered hook_manager handler with its enabled state.

    `config_path` is the absolute path of the JSON file where enable/disable
    flags and priority overrides are persisted. The UI surfaces it so users
    know exactly which file their edits land in (and can hand-edit if the UI
    is unreachable). It varies per provider because each provider gets its
    own config file.
    """
    from hook_manager.config import config_path
    from hook_manager.registry import describe_handlers
    provider, error = _provider_or_error()
    if error is not None:
        return error
    routed_events = _routed_events(provider)
    handlers = describe_handlers(
        routed_events=routed_events,
        agent_type=provider.provider_id,
    )
    return jsonify({
        'installed': bool(routed_events),
        'routed_events': sorted(routed_events),
        # The events this agent's hook system actually fires. Drives the
        # per-agent lifecycle diagram so Kimi doesn't show Claude-only events
        # (PermissionRequest, TaskCreated, Elicitation, …) it never emits.
        'supported_events': _supported_events(provider),
        'provider': provider.provider_id,
        'config_path': config_path(provider.provider_id),
        'handlers': handlers,
    })


def _supported_events(provider) -> list[str]:
    """Events this provider's hook system can fire. `hook_events()` returning
    None means "the full spec" (Claude), so fall back to every SPEC event."""
    events = provider.hook_events()
    return sorted(events) if events else sorted(SPEC_EVENTS)


@hooks_bp.route('/api/hooks/handlers/<name>/enable', methods=['POST'])
def api_enable_handler(name):
    from hook_manager.config import set_enabled
    from hook_manager.registry import REGISTRY
    provider, error = _provider_or_error()
    if error is not None:
        return error
    if name not in {h.name for h in REGISTRY}:
        return jsonify({'ok': False, 'msg': f'Unknown handler: {name}'}), 404
    set_enabled(name, True, agent_type=provider.provider_id)
    return jsonify({'ok': True, 'msg': f'Handler "{name}" enabled'})


@hooks_bp.route('/api/hooks/handlers/<name>/disable', methods=['POST'])
def api_disable_handler(name):
    from hook_manager.config import set_enabled
    from hook_manager.registry import REGISTRY
    provider, error = _provider_or_error()
    if error is not None:
        return error
    if name not in {h.name for h in REGISTRY}:
        return jsonify({'ok': False, 'msg': f'Unknown handler: {name}'}), 404
    set_enabled(name, False, agent_type=provider.provider_id)
    return jsonify({'ok': True, 'msg': f'Handler "{name}" disabled'})


@hooks_bp.route('/api/hooks/handlers/<name>/toggle', methods=['POST'])
def api_toggle_handler(name):
    from hook_manager.config import is_enabled, set_enabled
    from hook_manager.registry import REGISTRY
    provider, error = _provider_or_error()
    if error is not None:
        return error
    if name not in {h.name for h in REGISTRY}:
        return jsonify({'ok': False, 'msg': f'Unknown handler: {name}'}), 404
    new_state = not is_enabled(name, agent_type=provider.provider_id)
    set_enabled(name, new_state, agent_type=provider.provider_id)
    return jsonify({'ok': True, 'enabled': new_state,
                    'msg': f'Handler "{name}" {"enabled" if new_state else "disabled"}'})


# Reorder algorithm: when the user drags within an event group in the UI,
# the frontend POSTs the full ordered list of handler names for that event.
# Backend rewrites priorities for every name in the list using a 100-base +
# step-10 scheme (100, 110, 120, ...) so the resulting numbers sit in the
# same range as existing defaults (50, 80, 100, 110, 150) and stay
# debuggable on disk. Handlers wired to multiple events share one global
# priority — see registry.py:300-306 for the load-bearing turn_trace case.
_REORDER_BASE = 100
_REORDER_STEP = 10


@hooks_bp.route('/api/hooks/handlers/reorder', methods=['POST'])
def api_reorder_handlers():
    """Accept `{event, order: [name, ...]}` and assign sequential priorities.

    Unknown handler names in `order` are rejected (400) so a stale UI
    submission can't silently clobber the override map. Missing `event`
    is allowed — the field is informational only; we just iterate `order`.
    """
    from hook_manager.config import set_priorities
    from hook_manager.registry import REGISTRY
    provider, error = _provider_or_error()
    if error is not None:
        return error
    body = request.get_json(silent=True) or {}
    order = body.get('order')
    invalid = _validate_reorder_order(order, {h.name for h in REGISTRY})
    if invalid is not None:
        return invalid
    if not order:
        return jsonify({'ok': True, 'msg': 'No changes', 'updates': {}})
    updates = {name: _REORDER_BASE + i * _REORDER_STEP for i, name in enumerate(order)}
    set_priorities(updates, agent_type=provider.provider_id)
    return jsonify({'ok': True, 'msg': f'Reordered {len(order)} handler(s)', 'updates': updates})


def _validate_reorder_order(order, known: set[str]):
    """Return a 400 response when `order` is not a list of known names, else None."""
    if not isinstance(order, list) or not all(isinstance(n, str) for n in order):
        return jsonify({'ok': False, 'msg': '`order` must be a list of handler names'}), 400
    unknown = [n for n in order if n not in known]
    if unknown:
        return jsonify({'ok': False, 'msg': f'Unknown handler(s): {", ".join(unknown)}'}), 400
    return None


_PRIORITY_MIN = 0
_PRIORITY_MAX = 9999


@hooks_bp.route('/api/hooks/handlers/<name>/priority', methods=['POST'])
def api_set_handler_priority(name):
    """Set one handler's priority override directly.

    Body: `{"priority": <int>}`. Bounded to a sane range so a typo can't
    push a value into territory that breaks span ordering for everyone.
    """
    from hook_manager.config import set_priorities
    from hook_manager.registry import REGISTRY
    provider, error = _provider_or_error()
    if error is not None:
        return error
    if name not in {h.name for h in REGISTRY}:
        return jsonify({'ok': False, 'msg': f'Unknown handler: {name}'}), 404
    body = request.get_json(silent=True) or {}
    value = body.get('priority')
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return jsonify({'ok': False, 'msg': '`priority` must be a number'}), 400
    priority = int(value)
    if priority < _PRIORITY_MIN or priority > _PRIORITY_MAX:
        return jsonify({
            'ok': False,
            'msg': f'`priority` must be between {_PRIORITY_MIN} and {_PRIORITY_MAX}',
        }), 400
    set_priorities({name: priority}, agent_type=provider.provider_id)
    return jsonify({
        'ok': True,
        'priority': priority,
        'msg': f'Handler "{name}" priority set to {priority}',
    })


@hooks_bp.route('/api/hooks/handlers/<name>/reset-priority', methods=['POST'])
def api_reset_priority(name):
    """Drop the override for one handler — it reverts to its registry default."""
    from hook_manager.config import set_priorities
    from hook_manager.registry import REGISTRY
    provider, error = _provider_or_error()
    if error is not None:
        return error
    if name not in {h.name for h in REGISTRY}:
        return jsonify({'ok': False, 'msg': f'Unknown handler: {name}'}), 404
    set_priorities({name: None}, agent_type=provider.provider_id)
    return jsonify({'ok': True, 'msg': f'Handler "{name}" priority reset'})


@hooks_bp.route('/api/hooks/handlers/reset-priorities', methods=['POST'])
def api_reset_all_priorities():
    """Drop every priority override — bulk escape hatch for the UI."""
    from hook_manager.config import clear_priorities
    provider, error = _provider_or_error()
    if error is not None:
        return error
    clear_priorities(agent_type=provider.provider_id)
    return jsonify({'ok': True, 'msg': 'All handler priorities reset to defaults'})


# ── Legacy grouped dispatcher (debug only — kept for UI compat) ─

_INSTALLERS = {
    'hook_manager': api_hook_manager_install,
    'debug': api_debug_hook_install,
}
_UNINSTALLERS = {
    'hook_manager': api_hook_manager_uninstall,
    'debug': api_debug_hook_uninstall,
}


@hooks_bp.route('/api/hooks')
def api_hooks_status():
    providers = []
    for pid in list_visible_provider_ids():
        provider = build_provider(pid)
        routed = _routed_events(provider)
        providers.append({
            'id': provider.provider_id,
            'name': provider.display_name,
            'active': provider.provider_id == active_provider_id(),
            'hooks_supported': bool(provider.capabilities.hooks),
            'hook_settings_path': str(provider.hook_settings_path()),
            'hook_manager': {
                'installed': bool(routed),
                'target': provider.provider_id,
                'routed_events': sorted(routed),
            },
            'debug': {
                'installed': bool(_debug_hook_routed(provider)),
                'target': provider.provider_id,
            },
        })
    current = _provider()
    return jsonify({
        'providers': providers,
        'hook_manager': {'installed': bool(_routed_events(current)), 'target': current.provider_id},
        'debug': {'installed': bool(_debug_hook_routed(current)), 'target': current.provider_id},
    })


@hooks_bp.route('/api/hooks/<name>/install', methods=['POST'])
def api_hook_group_install(name):
    installer = _INSTALLERS.get(name)
    if not installer:
        return jsonify({'ok': False, 'msg': f'Unknown hook: {name}'}), 404
    return installer()


@hooks_bp.route('/api/hooks/<name>/uninstall', methods=['POST'])
def api_hook_group_uninstall(name):
    uninstaller = _UNINSTALLERS.get(name)
    if not uninstaller:
        return jsonify({'ok': False, 'msg': f'Unknown hook: {name}'}), 404
    return uninstaller()
