"""Handlers: SessionStart / SessionEnd lifecycle markers.

Emits `session` spans to the trace DB so downstream projections can graft
prompts and tool calls under the session root (see `lib/trace/projection.py`
which looks for `conversation` / session-level spans to parent orphan
prompts).

No `additional_context` emitted — the session start/end are obvious to the
model without a hook breadcrumb (silent-trace policy, commit `fa3922e`).
"""

from __future__ import annotations

import os

from ..core import HookPayload, HookResponse


def handle_start(payload: HookPayload) -> HookResponse | None:
    try:
        # SessionStart's spec field is `source` (startup | resume | clear | compact).
        # `model` is also on the payload — capture it so the Sessions UI can
        # show which model each session used (and what a /model switch
        # mid-session produced on resume).
        agent_type = _session_agent_type(payload)
        _emit_span(payload, 'session.start',
                   key_aliases=(('source', 'source'), ('model', 'model')),
                   default_key='source', default_value='startup',
                   extra_attrs={'agent_type': agent_type} if agent_type else None)
    except Exception:
        pass
    return HookResponse(suppress_output=True)


def handle_end(payload: HookPayload) -> HookResponse | None:
    try:
        # SessionEnd's spec field is `reason` (logout | clear | prompt_input_exit | …).
        _emit_span(payload, 'session.end',
                   key_aliases=(('reason', 'reason'),))
    except Exception:
        pass
    return HookResponse(suppress_output=True)


def handle_stop_fallback(payload: HookPayload) -> HookResponse | None:
    """Codex fallback: synthesize session.end from Stop.

    Codex currently does not emit SessionEnd in real runs, so we map Stop
    to a synthetic end marker for dashboard/session lifecycle continuity.
    Caveat: Stop is per-turn, so this may mark a long-lived interactive
    session as ended before the user truly exits.
    """
    if payload.event != 'Stop':
        return None
    if not _codex_stop_end_fallback_enabled():
        return None

    # Use the session's stored agent_type — set at SessionStart — rather
    # than the globally configured provider. This prevents incorrectly
    # ending Claude sessions when regin happens to be configured as codex.
    agent_type = _session_agent_type_from_db(payload.session_id)
    if agent_type != 'codex':
        return None

    try:
        _emit_span(
            payload,
            'session.end',
            default_key='reason',
            default_value='stop_fallback',
            extra_attrs={
                'synthetic': True,
                'source_event': 'Stop',
            },
        )
    except Exception:
        pass
    return HookResponse(suppress_output=True)


def _codex_stop_end_fallback_enabled() -> bool:
    """Feature flag for Codex Stop->session.end synthesis.

    Defaults to enabled. Set `REGIN_CODEX_STOP_END_FALLBACK=0` to disable.
    """
    raw = (os.environ.get('REGIN_CODEX_STOP_END_FALLBACK') or '').strip().lower()
    return raw not in {'0', 'false', 'off', 'no'}


def _session_agent_type_from_db(trace_id: str | None) -> str | None:
    """Return the stored agent_type for a session, or None if not found."""
    if not trace_id:
        return None
    try:
        from lib.orm.engine import get_connection
    except Exception:
        return None
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT agent_type FROM sessions WHERE trace_id = ?",
            (trace_id,),
        ).fetchone()
        return row['agent_type'] if row else None
    except Exception:
        return None
    finally:
        conn.close()


def _session_agent_type(payload: HookPayload) -> str | None:
    provider_id = payload.raw.get('provider_id')
    if isinstance(provider_id, str) and provider_id.strip():
        return provider_id.strip()

    raw = payload.raw.get('agent_type')
    if isinstance(raw, str) and raw.strip():
        return raw.strip()

    # Defensive fallback for legacy hook commands installed before
    # `--agent-type` was required.
    model = payload.raw.get('model')
    inferred = _agent_type_from_model(model)
    if inferred:
        return inferred

    resolved_id = getattr(payload.resolved_provider, 'provider_id', None)
    return resolved_id if isinstance(resolved_id, str) and resolved_id else None


def _agent_type_from_model(model: object) -> str | None:
    if not isinstance(model, str):
        return None
    normalized = model.strip().lower()
    if normalized.startswith('claude-'):
        return 'claude'
    if (
        normalized.startswith('gpt-')
        or normalized.startswith('o1')
        or normalized.startswith('o3')
        or normalized.startswith('o4')
        or normalized.startswith('o5')
    ):
        return 'codex'
    return None


def _emit_span(
    payload: HookPayload,
    name: str,
    key_aliases: tuple[tuple[str, str], ...] = (),
    default_key: str | None = None,
    default_value: str | None = None,
    extra_attrs: dict | None = None,
) -> None:
    from lib.hook_plugin import post_span  # type: ignore
    attrs: dict = {}
    if payload.cwd:
        attrs['cwd'] = payload.cwd
    for raw_key, attr_key in key_aliases:
        v = payload.raw.get(raw_key)
        if v:
            attrs[attr_key] = v
    if default_key and default_key not in attrs and default_value:
        attrs[default_key] = default_value
    if extra_attrs:
        attrs.update(extra_attrs)
    post_span(
        trace_id=payload.session_id,
        name=name,
        attributes=attrs,
    )
