"""Handlers: SubagentStart / SubagentStop → trace spans.

The old design had no visibility into subagents at all. We emit
`subagent.start` / `subagent.stop` spans so downstream trace viewers can
show "X subagents, Y total runtime" for a parent session and thread
sub-spans under the correct subagent.

No `additional_context` — the parent transcript already shows the Agent
tool call and final message (silent-trace policy, commit `fa3922e`).
"""

from __future__ import annotations

from ..core import HookPayload, HookResponse


def handle_start(payload: HookPayload) -> HookResponse | None:
    try:
        _emit_span(payload, 'subagent.start')
    except Exception:
        pass
    return HookResponse(suppress_output=True)


def handle_stop(payload: HookPayload) -> HookResponse | None:
    if not _is_real_subagent(payload):
        # Claude Code occasionally fires SubagentStop for a non-existent
        # subagent — agent_id present but agent_type empty, no agent_name,
        # and the agent_transcript_path points to a file that was never
        # written. Emitting a span for those produces a ghost
        # "subagent ran" entry whose `last_assistant_message` text isn't
        # tied to anything in this session. Skip emission for those.
        return HookResponse(suppress_output=True)
    try:
        _emit_span(payload, 'subagent.stop')
    except Exception:
        pass
    # Workflow-tool subagents are captured in full as the run's own wf_ session
    # (lib.trace.workflow_ingest reads their transcripts from disk). Mirroring
    # their turns here too would duplicate the whole run into the launching
    # conversation, so keep only the lightweight start/stop markers and skip the
    # response replay (see HookPayload.is_workflow_subagent).
    if not payload.is_workflow_subagent:
        try:
            _emit_subagent_responses(payload)
        except Exception:
            pass
    return HookResponse(suppress_output=True)


_RESULT_PREVIEW_MAX = 200


def _is_real_subagent(payload: HookPayload) -> bool:
    """Return True if this SubagentStop carries enough identity to be a
    real subagent.

    Discriminator: at least one of (a) `agent_type` non-empty, (b)
    `agent_name` non-empty, or (c) `agent_transcript_path` resolves to a
    real file. `agent_id` alone is insufficient — the ghost
    SubagentStop events that motivated this gate carry an agent_id but
    no type/name and a phantom transcript path.
    """
    raw = payload.raw or {}
    if _nonempty_str(raw, 'subagent_type', 'agent_type'):
        return True
    if _nonempty_str(raw, 'subagent_name', 'agent_name'):
        return True
    import os
    path = raw.get('agent_transcript_path')
    return isinstance(path, str) and bool(path) and os.path.isfile(path)


def _nonempty_str(raw: dict, *keys: str) -> bool:
    """True if any of `keys` maps to a non-blank string in `raw`."""
    for key in keys:
        value = raw.get(key)
        if isinstance(value, str) and value.strip():
            return True
    return False


def _emit_span(payload: HookPayload, name: str) -> None:
    from lib.hook_plugin import post_span  # type: ignore
    attrs: dict = {}
    raw = payload.raw
    # Claude Code has used both `agent_*` and `subagent_*` field names across
    # versions — accept either.
    agent_type = raw.get('subagent_type') or raw.get('agent_type')
    agent_id = raw.get('subagent_id') or raw.get('agent_id')
    agent_name = raw.get('subagent_name') or raw.get('agent_name')
    if agent_type:
        attrs['agent_type'] = agent_type
    if agent_id:
        attrs['agent_id'] = agent_id
    if agent_name:
        attrs['agent_name'] = agent_name
    desc = raw.get('description')
    if desc:
        attrs['description'] = desc
    # On stop, capture a preview of the final message (newline-stripped).
    last = raw.get('last_assistant_message')
    if last:
        flat = ' '.join(str(last).split())
        if len(flat) > _RESULT_PREVIEW_MAX:
            flat = flat[:_RESULT_PREVIEW_MAX] + '…'
        attrs['result_preview'] = flat
    post_span(
        trace_id=payload.session_id,
        name=name,
        attributes=attrs,
    )


def _emit_subagent_responses(payload: HookPayload) -> None:
    """SubagentStop entry: emit the subagent's assistant turns from its own
    transcript (final catch-up; the live rescan posts most of them earlier)."""
    raw = payload.raw
    emit_subagent_responses(
        payload.session_id,
        raw.get('agent_transcript_path'),
        raw.get('agent_id') or raw.get('subagent_id'),
    )


def _subagent_capture(transcript_path, agent_id) -> tuple[bool, int | None]:
    """`(should_emit, read_cap)`. `should_emit` is False when the guards fail
    (missing path/file/agent_id) or assistant-response capture is disabled.
    `read_cap` is the per-turn byte cap passed to the parser (None = no cap)."""
    import os

    from lib.settings import settings  # type: ignore
    if not (isinstance(transcript_path, str) and transcript_path
            and os.path.isfile(transcript_path) and agent_id):
        return False, None
    if not bool(getattr(settings, 'capture_assistant_response', True)):
        return False, None
    mb = int(getattr(settings, 'assistant_response_max_bytes', 50_000) or 0)
    return True, (mb if mb > 0 else None)


def emit_subagent_responses(trace_id, transcript_path, agent_id, *, seen=None) -> None:
    """Emit one `assistant_response`/`assistant.thinking` span per turn in the
    subagent's own transcript, tagged `agent_id` (the dashboard's `_graft_orphans`
    Pass 5 nests them under the matching `subagent.start`). The one-shot
    (full-read) path used at SubagentStop; the live rescan uses the resumable
    variant below. `seen` (a turn-uuid set) gates re-posts; None posts all
    (idempotent via the `resp-sa-`/`think-sa-` span_id)."""
    ok, read_cap = _subagent_capture(transcript_path, agent_id)
    if not ok:
        return
    from lib.trace.transcript_usage import read_usage  # type: ignore
    usage = read_usage(transcript_path, max_text_bytes=read_cap)
    if usage is not None:
        _post_subagent_turns(trace_id, usage, agent_id, seen)


def emit_subagent_responses_resumable(
    trace_id, transcript_path, agent_id, state, *, seen=None,
):
    """Resumable variant for the live rescan: parse only bytes appended to the
    subagent transcript since the last poll (reusing the accumulator in
    `state`), then post the same spans. Returns the updated
    `ResumableScanState` to thread back into the next poll."""
    ok, read_cap = _subagent_capture(transcript_path, agent_id)
    if not ok:
        return state
    from lib.trace.transcript_usage import read_usage_resumable  # type: ignore
    usage, state = read_usage_resumable(
        transcript_path, state, max_text_bytes=read_cap,
    )
    if usage is not None:
        _post_subagent_turns(trace_id, usage, agent_id, seen)
    return state


def _normalize_subagent_ts(ts: str) -> str:
    """UTC `...Z` timestamps → naive local ISO, matching the main-agent
    span timestamps (the subagent transcript carries the same shape)."""
    if not ts.endswith('Z'):
        return ts
    from datetime import datetime
    dt = datetime.fromisoformat(ts.replace('Z', '+00:00'))
    return dt.astimezone().replace(tzinfo=None).isoformat()


def _subagent_thinking_output_tokens(turn) -> int | None:
    """Output-token estimate for a thinking-only turn so the Tokens-by-tool
    rollup attributes subagent thinking instead of bucketing it under
    "untagged". Prefer the captured-text estimate; fall back to the residual
    of API-reported output minus tool_use estimates (redacted thinking)."""
    from lib.tokens.token_estimator import estimate_text_tokens  # type: ignore
    if turn.thinking_text:
        return estimate_text_tokens(turn.thinking_text)
    tool_use_out = sum(
        int(tc.get('output_token_estimate') or 0) for tc in turn.tool_calls
    )
    return max(0, int(turn.output_tokens or 0) - tool_use_out) or None


def _subagent_turn_attributes(turn, idx, agent_id, fallback_model) -> dict:
    """Build the span attributes for one subagent turn."""
    has_text = bool(turn.text)
    attributes = {
        'turn_uuid': turn.uuid,
        'turn_index': idx,
        'model': turn.model or fallback_model,
        'agent_id': agent_id,
    }
    if has_text:
        attributes['text'] = turn.text
        attributes['truncated'] = turn.text_truncated
        attributes['response_chars'] = len(turn.text)
    else:
        out = _subagent_thinking_output_tokens(turn)
        if out:
            attributes['output_tokens'] = out
    if turn.tool_calls:
        attributes['tool_calls'] = [
            {'name': t['name'], 'is_error': t['is_error']}
            for t in turn.tool_calls
        ]
    if turn.thinking_blocks:
        attributes['thinking_blocks'] = turn.thinking_blocks
        attributes['thinking_signature_bytes'] = turn.thinking_signature_bytes
        if turn.thinking_text:
            attributes['thinking_text'] = turn.thinking_text
            attributes['thinking_truncated'] = turn.thinking_text_truncated
    if turn.inference_duration_ms is not None:
        attributes['inference_duration_ms'] = int(turn.inference_duration_ms)
    if turn.turn_total_duration_ms is not None:
        attributes['turn_total_duration_ms'] = int(turn.turn_total_duration_ms)
    return attributes


def _post_one_subagent_turn(trace_id, turn, idx, agent_id, fallback_model) -> None:
    """Emit the single assistant_response / assistant.thinking span for one
    subagent turn. Thinking-only turns get the distinct `assistant.thinking`
    name so the conversation view doesn't render an empty response card."""
    from lib.hook_plugin import post_span  # type: ignore
    has_text = bool(turn.text)
    ts = _normalize_subagent_ts(turn.timestamp)
    post_span(
        trace_id=trace_id,
        span_id=f'{"resp-sa" if has_text else "think-sa"}-{turn.uuid[:13]}',
        name='assistant_response' if has_text else 'assistant.thinking',
        start_time=ts,
        end_time=ts,
        duration_ms=int(turn.inference_duration_ms or 0),
        attributes=_subagent_turn_attributes(turn, idx, agent_id, fallback_model),
    )


def _subagent_turn_emittable(turn, seen) -> bool:
    """A turn is emitted when it has a uuid + timestamp, isn't already seen,
    and carried user-visible text OR extended thinking (mirrors the main-agent
    gate in turn_trace — a reasoning-only turn still leaves a trace row)."""
    if not turn.uuid or not turn.timestamp:
        return False
    if seen is not None and turn.uuid in seen:
        return False
    return bool(turn.text or turn.thinking_blocks)


def _post_subagent_turns(trace_id, usage, agent_id, seen) -> None:
    """Post the assistant_response / assistant.thinking spans for a subagent's
    turns. Shared by the one-shot and resumable entry points."""
    from hook_manager.handlers.turn_trace.cache import _mark_seen  # type: ignore

    newly_seen: list = []
    fallback_model = usage.model
    for idx, turn in enumerate(usage.turns):
        if not _subagent_turn_emittable(turn, seen):
            continue
        _post_one_subagent_turn(trace_id, turn, idx, agent_id, fallback_model)
        newly_seen.append(turn.uuid)
    if seen is not None and newly_seen:
        _mark_seen(trace_id, newly_seen)
