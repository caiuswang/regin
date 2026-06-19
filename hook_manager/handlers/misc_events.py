"""Small, logging-only handlers for the remaining log-safe events.

Each emits a trace span to the session DB so the trace dashboard has a
record, but returns `HookResponse(suppress_output=True)` with no
`additional_context` — the model doesn't need a transcript breadcrumb for
orchestration events it can't act on (silent-trace policy, commit `fa3922e`).

Wiring note: WorktreeCreate, Elicitation, and ElicitationResult are NOT in
this module. They are *provider* hooks where the spec requires the handler
to emit structured output (worktree path, form action/content) — a
default-no-op handler would break the default Claude Code flow for users
who didn't opt in. Leave them unwired.
"""

from __future__ import annotations

from ..core import HookPayload, HookResponse


def teammate_idle(payload: HookPayload) -> HookResponse | None:
    raw = payload.raw
    _safe_emit(payload, 'teammate.idle', {
        'teammate_name': raw.get('teammate_name') or 'unknown',
    })
    return HookResponse(suppress_output=True)


def instructions_loaded(payload: HookPayload) -> HookResponse | None:
    raw = payload.raw
    attrs: dict = {}
    for key in ('file_path', 'memory_type', 'load_reason', 'parent_file_path'):
        v = raw.get(key)
        if v:
            attrs[key] = v
    _safe_emit(payload, 'instructions.loaded', attrs)
    return HookResponse(suppress_output=True)


def config_change(payload: HookPayload) -> HookResponse | None:
    raw = payload.raw
    attrs: dict = {}
    src = raw.get('config_source') or raw.get('source')
    if src:
        attrs['source'] = src
    file_path = raw.get('file_path')
    if file_path:
        attrs['file_path'] = file_path
    _safe_emit(payload, 'config.change', attrs)
    return HookResponse(suppress_output=True)


def worktree_remove(payload: HookPayload) -> HookResponse | None:
    raw = payload.raw
    attrs: dict = {}
    path = raw.get('worktree_path') or raw.get('path')
    if path:
        attrs['path'] = path
    _safe_emit(payload, 'worktree.remove', attrs)
    return HookResponse(suppress_output=True)


def _safe_emit(payload: HookPayload, name: str, attrs: dict) -> None:
    try:
        from lib.hook_plugin import post_span  # type: ignore
        post_span(
            trace_id=payload.session_id,
            name=name,
            attributes=attrs,
        )
    except Exception:
        pass
