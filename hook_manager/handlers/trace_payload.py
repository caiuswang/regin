"""Handler: append every payload to ~/.claude/hook-payloads.jsonl (size-capped).

Replaces the old `hook_payload_debug.py` with two improvements:
  1. No `additionalContext` — the debug log does not leak into the model's
     transcript the way the old version did on every single tool call.
  2. Size-capped: rotates the jsonl when it exceeds 50 MB.
"""

from __future__ import annotations

import json
import os
from datetime import datetime

from lib.providers import get_active_provider

from ..core import HookPayload, HookResponse

_MAX_BYTES = 50 * 1024 * 1024


def _log_path(provider=None) -> str:
    if provider is None:
        provider = get_active_provider()
    return str(provider.hook_payload_log_path())


def _rotate_if_needed(path: str) -> None:
    try:
        if os.path.exists(path) and os.path.getsize(path) > _MAX_BYTES:
            rotated = path + '.1'
            if os.path.exists(rotated):
                os.remove(rotated)
            os.rename(path, rotated)
    except OSError:
        pass


def handle(payload: HookPayload) -> HookResponse | None:
    # Master switch — when Diagnostics is off, skip both the JSONL
    # append and the validate/drift-record pipeline. Read settings
    # fresh per call so users can toggle without restarting hook
    # subprocesses (settings re-parses on import per new process).
    if not _diagnostics_enabled():
        return HookResponse(suppress_output=True)

    path = _log_path(payload.resolved_provider)
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        _rotate_if_needed(path)
        entry = {
            'received_at': datetime.now().isoformat(),
            'hook_event': payload.event,
            'session_id': payload.session_id,
            'payload': payload.raw,
        }
        with open(path, 'a') as f:
            f.write(json.dumps(entry, ensure_ascii=False) + '\n')
    except OSError:
        pass

    _record_drift(payload)

    # Quiet: no additional_context, no blocking. Only request suppress_output
    # so we don't appear in the debug log.
    return HookResponse(suppress_output=True)


def _diagnostics_enabled() -> bool:
    try:
        from lib.settings import settings
        return bool(settings.diagnostics_enabled)
    except Exception:
        # Default is OFF — fail-closed so a settings glitch can't silently
        # impose the diagnostics overhead on users who opted out.
        return False


def _record_drift(payload: HookPayload) -> None:
    """Validate payloads + persist any drift findings.

    Wrapped wholesale in try/except: drift tracking is observability,
    not a gate — must never break the trace pipeline. PostToolUse goes
    through the tool validator; every other event goes through the
    hook-event validator.
    """
    try:
        from lib.trace.payload_validation import validate, validate_event
        from lib.trace.payload_drift_store import record_findings
        agent = getattr(payload.resolved_provider, 'provider_id', 'claude')
        if payload.event == 'PostToolUse':
            findings = validate(payload.tool_name, payload.raw, agent=agent)
        else:
            findings = validate_event(payload.event, payload.raw, agent=agent)
        if findings:
            record_findings(findings, payload.raw)
    except Exception:
        pass
