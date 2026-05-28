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
    agent_type = raw.get('subagent_type') or raw.get('agent_type')
    if isinstance(agent_type, str) and agent_type.strip():
        return True
    agent_name = raw.get('subagent_name') or raw.get('agent_name')
    if isinstance(agent_name, str) and agent_name.strip():
        return True
    path = raw.get('agent_transcript_path')
    if isinstance(path, str) and path:
        import os
        if os.path.isfile(path):
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
    """Emit one `assistant_response` span per turn in the subagent's own
    transcript. The dashboard's `_graft_orphans` Pass 5 nests any span
    carrying `agent_id` under the matching `subagent.start`, so we set
    that attribute and leave parent_id unset.
    """
    raw = payload.raw
    transcript_path = raw.get('agent_transcript_path')
    if not isinstance(transcript_path, str) or not transcript_path:
        return
    import os
    if not os.path.isfile(transcript_path):
        return
    agent_id = raw.get('agent_id') or raw.get('subagent_id')
    if not agent_id:
        return

    from lib.hook_plugin import post_span  # type: ignore
    from lib.settings import settings  # type: ignore
    from lib.tokens.token_estimator import estimate_text_tokens  # type: ignore
    from lib.trace.transcript_usage import read_usage  # type: ignore

    if not bool(getattr(settings, 'capture_assistant_response', True)):
        return
    max_text_bytes = int(getattr(settings, 'assistant_response_max_bytes', 50_000) or 0)
    usage = read_usage(
        transcript_path,
        max_text_bytes=max_text_bytes if max_text_bytes > 0 else None,
    )
    if usage is None:
        return

    fallback_model = usage.model
    for idx, turn in enumerate(usage.turns):
        if not turn.uuid or not turn.timestamp:
            continue
        # Mirror the main-agent gate in turn_trace: emit a span when
        # the turn carried user-visible text OR when extended thinking
        # happened, so a reasoning-only subagent turn still leaves a
        # trace row with its thinking metadata.
        if not (turn.text or turn.thinking_blocks):
            continue
        ts = turn.timestamp
        if ts.endswith('Z'):
            from datetime import datetime
            dt = datetime.fromisoformat(ts.replace('Z', '+00:00'))
            ts = dt.astimezone().replace(tzinfo=None).isoformat()
        has_text = bool(turn.text)
        attributes = {
            'turn_uuid': turn.uuid,
            'turn_index': idx,
            'model': turn.model or fallback_model,
            'agent_id': agent_id,
        }
        # Thinking-only turns get an output_tokens estimate so the
        # Tokens-by-tool rollup can attribute subagent thinking instead
        # of bucketing it under "untagged". Prefer the captured-text
        # estimate; fall back to the residual of API-reported output
        # minus tool_use estimates when the thinking text was redacted.
        if not has_text:
            if turn.thinking_text:
                attributes['output_tokens'] = estimate_text_tokens(turn.thinking_text)
            else:
                tool_use_out = sum(
                    int(tc.get('output_token_estimate') or 0)
                    for tc in turn.tool_calls
                )
                residual = max(0, int(turn.output_tokens or 0) - tool_use_out)
                if residual:
                    attributes['output_tokens'] = residual
        if has_text:
            attributes['text'] = turn.text
            attributes['truncated'] = turn.text_truncated
            attributes['response_chars'] = len(turn.text)
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
        # Per-API-call latency surfaces on subagent responses too —
        # the subagent transcript carries the same timestamp shape as
        # the main one. `turn_total_duration_ms` is rarely populated
        # for subagents (no `system: turn_duration` written for a
        # subagent run), so the inference number is usually the only
        # signal available.
        inference_ms = int(turn.inference_duration_ms) if turn.inference_duration_ms else 0
        if turn.inference_duration_ms is not None:
            attributes['inference_duration_ms'] = int(turn.inference_duration_ms)
        if turn.turn_total_duration_ms is not None:
            attributes['turn_total_duration_ms'] = int(turn.turn_total_duration_ms)
        # Distinct span name for thinking-only turns so the conversation
        # view doesn't render an empty assistant_response card.
        span_name = 'assistant_response' if has_text else 'assistant.thinking'
        span_prefix = 'resp-sa' if has_text else 'think-sa'
        post_span(
            trace_id=payload.session_id,
            span_id=f'{span_prefix}-{turn.uuid[:13]}',
            name=span_name,
            start_time=ts,
            end_time=ts,
            duration_ms=inference_ms,
            attributes=attributes,
        )
