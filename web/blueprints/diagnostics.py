"""Diagnostics surface: payload log browser + master switch.

The schema-drift blueprint already covers per-schema findings; this
blueprint adds the sibling tools that share the Diagnostics nav group:

  GET  /api/diagnostics/state           – is the master switch on?
  POST /api/diagnostics/state           – flip it (writes settings.local.json)
  GET  /api/diagnostics/payload-log     – tail recent entries from
                                          ~/.claude/hook-payloads.jsonl

The master switch gates `trace_payload.handle()`: when off, neither
the JSONL append nor the schema validation runs, so the diagnostics
pipeline costs nothing on the hot path.
"""

from __future__ import annotations

import json
from pathlib import Path

from flask import Blueprint, jsonify, request

from lib import audit
from lib.auth import get_current_user, require_editor
from lib.settings import save_settings
from lib.providers import (
    build_provider,
    get_active_provider,
    is_provider_id,
    list_provider_ids,
)


diagnostics_bp = Blueprint('diagnostics', __name__)


def _diagnostics_enabled() -> bool:
    """Re-read on every request. `save_settings()` rebinds
    `lib.settings.settings` after writes, so a module-level alias
    captured at import time goes stale — the function-local import
    sees the current binding each call."""
    from lib.settings import settings
    return bool(settings.diagnostics_enabled)


# ── Master switch ──────────────────────────────────────────────────


@diagnostics_bp.route('/api/diagnostics/state', methods=['GET'])
def api_diagnostics_state():
    return jsonify({'enabled': _diagnostics_enabled()})


@diagnostics_bp.route('/api/diagnostics/state', methods=['POST'])
@require_editor
def api_diagnostics_state_set():
    body = request.get_json(silent=True) or {}
    if 'enabled' not in body:
        return jsonify({'error': '`enabled` (bool) required'}), 400
    new = bool(body['enabled'])
    save_settings({'diagnostics_enabled': new}, scope='local')
    user = get_current_user()
    audit.log_action(
        user['id'] if user else None,
        user['username'] if user else 'anon',
        'diagnostics_toggle',
        f'diagnostics:{new}',
        {'enabled': new},
    )
    return jsonify({'enabled': new})


# ── Payload log browser ────────────────────────────────────────────


def _payload_log_agents() -> list[str]:
    """Provider ids worth an agent tab on the payload-log page.

    Each provider writes to its own hook-payloads.jsonl (Claude →
    ~/.claude, Kimi → ~/.kimi-code, …), so "switch by agent" is a switch
    between log files. Show a tab for any provider whose log exists on
    disk, plus the active provider (always shown so the page opens to a
    real tab even before that provider has captured anything)."""
    found = {get_active_provider().provider_id}
    for pid in list_provider_ids():
        path = Path(str(build_provider(pid).hook_payload_log_path()))
        if path.is_file():
            found.add(pid)
    return sorted(found)


@diagnostics_bp.route('/api/diagnostics/payload-log', methods=['GET'])
def api_payload_log():
    """Tail recent entries from a provider's hook-payloads.jsonl.

    Filters:
      ?agent=kimi          - which provider's log to read (default: active)
      ?event=PostToolUse   - hook_event_name filter (exact match)
      ?tool=Bash           - tool_name filter inside the payload
      ?limit=200           - max entries returned (default 200, cap 1000)

    `agents` lists every provider with a log to switch between; the UI
    renders these as tabs.
    """
    event_filter = request.args.get('event')
    tool_filter = request.args.get('tool')
    agent = request.args.get('agent')
    try:
        limit = min(int(request.args.get('limit', 200)), 1000)
    except ValueError:
        limit = 200

    # Read the requested agent's log when it names a known provider;
    # otherwise fall back to the active provider.
    provider = build_provider(agent) if is_provider_id(agent) else get_active_provider()
    agents = _payload_log_agents()
    path = Path(str(provider.hook_payload_log_path()))
    if not path.is_file():
        return jsonify({
            'path': str(path), 'exists': False, 'entries': [],
            'total_scanned': 0,
            'agent': provider.provider_id, 'agents': agents,
            'diagnostics_enabled': _diagnostics_enabled(),
        })

    entries = _scan_payload_log(path, event_filter, tool_filter, limit)
    size_bytes = path.stat().st_size
    return jsonify({
        'path': str(path),
        'exists': True,
        'size_bytes': size_bytes,
        'entries': entries,
        'returned': len(entries),
        'agent': provider.provider_id,
        'agents': agents,
        'diagnostics_enabled': _diagnostics_enabled(),
    })


def _scan_payload_log(
    path: Path, event_filter: str | None, tool_filter: str | None, limit: int,
) -> list[dict]:
    """Read the whole file (capped at 50 MB so this is fine), filter,
    take the last `limit`. Trades a bit of memory for the simplicity of
    not having to reverse-tail or maintain an index."""
    matched: list[dict] = []
    try:
        with path.open() as fh:
            for raw_line in fh:
                entry = json.loads(raw_line)
                payload = entry.get('payload') or {}
                if event_filter and entry.get('hook_event') != event_filter:
                    continue
                if tool_filter and payload.get('tool_name') != tool_filter:
                    continue
                matched.append({
                    'received_at': entry.get('received_at'),
                    'hook_event': entry.get('hook_event'),
                    'session_id': entry.get('session_id'),
                    'tool_name': payload.get('tool_name'),
                    'payload': payload,
                })
    except (OSError, json.JSONDecodeError, ValueError):
        return matched[-limit:]
    return matched[-limit:]
