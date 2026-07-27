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

Command construction, ownership detection, and the install/uninstall writers
live in `lib/hooks_wiring.py` so `regin hooks` and `regin doctor` share one
implementation with this blueprint; only request plumbing lives here.
"""

from __future__ import annotations

import json
import os

from flask import Blueprint, request, jsonify

from lib import hooks_wiring
from lib.providers import (
    active_provider_id,
    build_provider,
    get_active_provider,
    is_provider_id,
    list_visible_provider_ids,
)
from hook_manager.core import SPEC_EVENTS


hooks_bp = Blueprint('hooks', __name__)


# ── Shared path helpers + settings helpers ──────────────────

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


# Names below are thin aliases over `lib.hooks_wiring` so the blueprint (and
# its tests) keep one vocabulary while the implementation stays shared.
DEBUG_HOOK_COMMAND = hooks_wiring.debug_base_command()
_DEBUG_EVENTS = hooks_wiring.DEBUG_EVENTS
_is_hook_manager_command = hooks_wiring.is_hook_manager_command
_is_debug_hook_command = hooks_wiring.is_debug_hook_command


def _debug_hook_command(provider=None) -> str:
    return hooks_wiring.debug_hook_command(provider or _provider())


def _hook_manager_command(event_name: str, provider=None) -> str:
    return hooks_wiring.hook_manager_command(event_name, provider or _provider())


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
    return hooks_wiring.is_toml_provider(provider)


def _routed_events(provider) -> set[str]:
    """Events routed to this regin checkout's hook_manager, format-agnostic."""
    return hooks_wiring.routed_events(provider, _hook_settings_path(provider))


def _debug_hook_routed(provider) -> set[str]:
    """Events the debug hook is installed for, format-agnostic."""
    return hooks_wiring.debug_routed_events(provider, _hook_settings_path(provider))


def _wiring(provider) -> dict:
    return hooks_wiring.wiring_status(provider, _hook_settings_path(provider))


def _run_wiring(action, provider):
    """Apply one hooks_wiring writer to `provider`, or 400 if it has no hooks."""
    unsupported = _require_hooks_capability(provider)
    if unsupported is not None:
        return unsupported
    return jsonify(action(provider, _hook_settings_path(provider)))


@hooks_bp.route('/api/hook-manager-install', methods=['POST'])
def api_hook_manager_install():
    provider, error = _provider_or_error()
    if error is not None:
        return error
    return _run_wiring(hooks_wiring.install_hook_manager, provider)


@hooks_bp.route('/api/hook-manager-uninstall', methods=['POST'])
def api_hook_manager_uninstall():
    provider, error = _provider_or_error()
    if error is not None:
        return error
    return _run_wiring(hooks_wiring.uninstall_hook_manager, provider)


@hooks_bp.route('/api/debug-hook-install', methods=['POST'])
def api_debug_hook_install():
    provider, error = _provider_or_error()
    if error is not None:
        return error
    return _run_wiring(hooks_wiring.install_debug_hook, provider)


@hooks_bp.route('/api/debug-hook-uninstall', methods=['POST'])
def api_debug_hook_uninstall():
    provider, error = _provider_or_error()
    if error is not None:
        return error
    return _run_wiring(hooks_wiring.uninstall_debug_hook, provider)


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


def _provider_hooks_entry(provider) -> dict:
    """One provider's row for `/api/hooks`, carrying enough to render a repair.

    `stale`, `commands`, and `expected_commands` are what let the Settings
    panel show *Refresh* (and what it would rewrite) instead of only
    Install/Remove — an install whose command drifted looks identical to a
    healthy one through `installed` alone.
    """
    wiring = _wiring(provider)
    return {
        'id': provider.provider_id,
        'name': provider.display_name,
        'active': provider.provider_id == active_provider_id(),
        'hooks_supported': bool(provider.capabilities.hooks),
        'hook_settings_path': str(provider.hook_settings_path()),
        'hook_manager': {**wiring['hook_manager'], 'target': provider.provider_id},
        'debug': {**wiring['debug'], 'target': provider.provider_id},
    }


@hooks_bp.route('/api/hooks')
def api_hooks_status():
    providers = [_provider_hooks_entry(build_provider(pid))
                 for pid in list_visible_provider_ids()]
    current = _provider()
    wiring = _wiring(current)
    return jsonify({
        'providers': providers,
        'hook_manager': {'installed': wiring['hook_manager']['installed'],
                         'stale': wiring['hook_manager']['stale'],
                         'target': current.provider_id},
        'debug': {'installed': wiring['debug']['installed'],
                  'stale': wiring['debug']['stale'],
                  'target': current.provider_id},
    })


@hooks_bp.route('/api/hooks/wiring')
def api_hooks_wiring():
    """Install-vs-disk report for one provider: routed events, the command
    written for each, and the command install would write today."""
    provider, error = _provider_or_error()
    if error is not None:
        return error
    return jsonify(_wiring(provider))


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
