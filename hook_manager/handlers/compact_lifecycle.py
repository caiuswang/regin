"""Handlers: PreCompact / PostCompact → compaction boundary spans.

Mark context-compaction boundaries in the trace DB so the session trace
view can show "context compacted at T (trigger=auto)" markers.

No `additional_context` — compaction is orchestration-level; the model
gets a fresh context anyway (silent-trace policy, commit `fa3922e`).
"""

from __future__ import annotations

from ..core import HookPayload, HookResponse

# `custom_instructions` is the user's free-form `/compact <text>` prompt —
# cap it to protect the row from a runaway paste. The model-generated
# `compact_summary` is stored in full: it's the whole point of capturing
# the compaction (one row per `/compact`, recoverable conversation state).
_INSTRUCTIONS_MAX = 2000


def handle_pre(payload: HookPayload) -> HookResponse | None:
    try:
        _emit_span(payload, 'compact.pre')
    except Exception:
        pass
    return HookResponse(suppress_output=True)


def handle_post(payload: HookPayload) -> HookResponse | None:
    try:
        _emit_span(payload, 'compact.post')
    except Exception:
        pass
    return HookResponse(suppress_output=True)


def _truncate(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit] + '…'


def _emit_span(payload: HookPayload, name: str) -> None:
    from lib.hook_plugin import post_span  # type: ignore
    attrs: dict = {}
    trigger = payload.raw.get('compaction_trigger') or payload.raw.get('trigger')
    if trigger:
        attrs['trigger'] = trigger
    instructions = payload.raw.get('custom_instructions')
    if isinstance(instructions, str) and instructions.strip():
        attrs['custom_instructions'] = _truncate(instructions.strip(), _INSTRUCTIONS_MAX)
    summary = payload.raw.get('compact_summary')
    if isinstance(summary, str) and summary.strip():
        attrs['summary'] = summary.strip()
        attrs['summary_chars'] = len(summary)
    post_span(
        trace_id=payload.session_id,
        name=name,
        attributes=attrs,
    )
