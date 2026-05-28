"""Post spans + turn_usage events for one transcript's worth of data.

The transcript parser (`lib.trace.transcript_usage.read_usage`) returns
four lists of rows:

  * `turns` — assistant turns with usage + per-call attribution
  * `attachments` — task_reminder / skill_listing / deferred_tools_delta
  * `system_events` — stop_hook_summary / turn_duration
  * `local_commands` — /add-dir, /clear, /usage, `!ls`, etc.

This module turns each list into ingest calls. Idempotency comes from
deterministic span IDs (`resp-<uuid[:13]>`, `srvtool-<id[:13]>`,
`sys-<uuid[:13]>`, `att-<uuid[:13]>`, `cmd-<uuid[:13]>`) so a replayed
transcript only UPSERTs. The seen-uuid cache in `cache.py` is the
client-side throttle that keeps PostToolUse from re-sending every turn.
"""

from __future__ import annotations

from .cache import _mark_seen
from .deny_detection import (
    _build_deny_attrs,
    _build_tool_use_error_attrs,
    _is_permission_deny,
    _is_tool_use_error,
)
from .timestamps import _normalise_attachment_ts, _to_naive_datetime

# Skill-listing payloads can be ~7 KiB. Cap to keep span attrs lean.
_SKILL_LISTING_MAX_BYTES = 16 * 1024

# Local-command stdout payloads are usually tiny (a one-line message
# like "Added /path as a working directory"); cap so a hypothetically
# verbose command doesn't bloat span attributes.
_LOCAL_COMMAND_STDOUT_MAX_BYTES = 8 * 1024


# ─────────────────────────── system events ─────────────────────────────


def _build_hook_breakdown(payload: dict) -> list[dict]:
    """Flatten `hookInfos` into a list of {command, duration_ms} pairs.
    Skips malformed entries silently."""
    hooks = payload.get('hook_infos') or payload.get('hookInfos') or []
    if not isinstance(hooks, list):
        return []
    out: list[dict] = []
    for h in hooks:
        if not isinstance(h, dict):
            continue
        try:
            out.append({
                'command': str(h.get('command') or '')[:200],
                'duration_ms': int(h.get('duration_ms') or h.get('durationMs') or 0),
            })
        except (TypeError, ValueError):
            continue
    return out


def _emit_stop_summary_span(trace_id: str, ev) -> bool:
    from lib.hook_plugin import post_span  # type: ignore
    payload = ev.payload or {}
    hook_breakdown = _build_hook_breakdown(payload)
    total_ms = sum(h['duration_ms'] for h in hook_breakdown)
    ts = _normalise_attachment_ts(ev.timestamp)
    attrs = {
        'subtype': 'stop_hook_summary',
        'turn_uuid': ev.turn_uuid,
        'hook_count': int(payload.get('hook_count') or payload.get('hookCount') or 0),
        'hook_errors': payload.get('hook_errors') or payload.get('hookErrors') or [],
        'prevented_continuation': bool(
            payload.get('prevented_continuation')
            or payload.get('preventedContinuation')
        ),
        'hooks': hook_breakdown,
    }
    return post_span(
        trace_id=trace_id,
        span_id=f'sys-{ev.uuid[:13]}',
        name='hook.stop_summary',
        start_time=ts,
        end_time=ts,
        duration_ms=total_ms,
        attributes=attrs,
    )


def _post_system_event_spans(trace_id: str, events, seen: set[str]) -> None:
    """Emit `hook.stop_summary` spans for `system: stop_hook_summary`
    entries. The `turn_duration` siblings are folded into the matching
    `assistant_response` span's `duration_ms` and don't need their own
    span. Idempotent via `sys-<uuid[:13]>` span_id."""
    new_uuids: list[str] = []
    for ev in events:
        if ev.uuid in seen:
            continue
        if ev.subtype != 'stop_hook_summary':
            # No span needed for this subtype — record the uuid so we
            # don't keep re-scanning it.
            new_uuids.append(ev.uuid)
            continue
        if _emit_stop_summary_span(trace_id, ev):
            new_uuids.append(ev.uuid)
    _mark_seen(trace_id, new_uuids)


# ───────────────────────────── attachments ─────────────────────────────


def _truncate_utf8_with_marker(text: str, max_bytes: int) -> tuple[str, bool]:
    encoded = text.encode('utf-8')
    if len(encoded) <= max_bytes:
        return text, False
    head = encoded[:max_bytes].decode('utf-8', errors='ignore')
    return head + '\n…[truncated]', True


def _post_task_reminder_span(trace_id: str, att, ts) -> bool:
    from lib.hook_plugin import post_span  # type: ignore
    payload = att.payload or {}
    attrs = {
        'kind': 'task_reminder',
        'item_count': int(payload.get('item_count') or payload.get('itemCount') or 0),
        'content': payload.get('content'),
    }
    return post_span(
        trace_id=trace_id,
        span_id=f'att-{att.uuid[:13]}',
        name='harness.task_reminder',
        start_time=ts, end_time=ts, duration_ms=0,
        attributes=attrs,
    )


def _post_skill_listing_span(trace_id: str, att, ts) -> bool:
    from lib.hook_plugin import post_span  # type: ignore
    payload = att.payload or {}
    is_initial = bool(payload.get('is_initial') or payload.get('isInitial'))
    content = payload.get('content') or ''
    truncated = False
    if isinstance(content, str):
        content, truncated = _truncate_utf8_with_marker(content, _SKILL_LISTING_MAX_BYTES)
    attrs = {
        'kind': 'skill_listing',
        'is_initial': is_initial,
        'skill_count': int(payload.get('skill_count') or payload.get('skillCount') or 0),
        'content': content,
        'content_truncated': truncated,
    }
    # Initial listing: one stable span per session (collapse repeated
    # session-start re-scans onto a single row). Non-initial delta:
    # per-attachment span.
    span_id = (
        f'skill-init-{trace_id[:24]}' if is_initial else f'att-{att.uuid[:13]}'
    )
    return post_span(
        trace_id=trace_id,
        span_id=span_id,
        name='harness.skill_listing',
        start_time=ts, end_time=ts, duration_ms=0,
        attributes=attrs,
    )


def _post_tools_delta_span(trace_id: str, att, ts) -> bool:
    from lib.hook_plugin import post_span  # type: ignore
    payload = att.payload or {}
    attrs = {
        'kind': 'deferred_tools_delta',
        'added_names': list(payload.get('added_names') or payload.get('addedNames') or []),
        'removed_names': list(payload.get('removed_names') or payload.get('removedNames') or []),
        'readded_names': list(payload.get('readded_names') or payload.get('readdedNames') or []),
        'pending_mcp_servers': list(
            payload.get('pending_mcp_servers') or payload.get('pendingMcpServers') or []
        ),
    }
    return post_span(
        trace_id=trace_id,
        span_id=f'att-{att.uuid[:13]}',
        name='harness.tools_delta',
        start_time=ts, end_time=ts, duration_ms=0,
        attributes=attrs,
    )


def _post_queued_command_span(trace_id: str, att, ts) -> bool:
    """Emit a `prompt` span for a queued user prompt.

    When the user types while the agent is mid-turn, Claude Code queues
    the input and, on dequeue, injects it as a `queued_command`
    attachment rather than firing UserPromptSubmit. `prompt_trace.py`
    (the only producer of `prompt` spans) therefore never sees it, so
    the trace UI loses the prompt and the assistant turns that follow
    orphan at the session root. Recover it here from the transcript.

    span_id mirrors the UserPromptSubmit scheme (`prompt-<uuid[:13]>`,
    keyed on the attachment uuid) so `_graft_orphans` anchors the
    following assistant_response spans to it chronologically, and so a
    replay UPSERTs the same row. Only prompt-mode queues become prompt
    spans; a queued slash command (a different `command_mode`) is not a
    model prompt — mark it seen without a span. Returning True in the
    skip cases records the uuid so the scan doesn't re-walk it.
    """
    from lib.hook_plugin import post_span  # type: ignore
    payload = att.payload or {}
    mode = payload.get('command_mode')
    if mode is not None and mode != 'prompt':
        return True
    text = payload.get('prompt')
    if not isinstance(text, str) or not text:
        return True
    attrs = {'text': text, 'chars': len(text), 'queued': True}
    return post_span(
        trace_id=trace_id,
        span_id=f'prompt-{att.uuid[:13]}',
        name='prompt',
        start_time=ts, end_time=ts, duration_ms=0,
        attributes=attrs,
    )


_ATTACHMENT_HANDLERS = {
    'task_reminder': _post_task_reminder_span,
    'skill_listing': _post_skill_listing_span,
    'deferred_tools_delta': _post_tools_delta_span,
    'queued_command': _post_queued_command_span,
}


def _post_attachment_spans(trace_id: str, attachments, seen: set[str]) -> None:
    """Emit one `harness.*` span per unseen Claude Code attachment of
    interest. Idempotent via `att-<uuid[:13]>` (or
    `skill-init-<trace_id[:24]>` for the initial skill-listing row).

    Skill-listing's `isInitial=True` row is folded into a single span
    per session via a deterministic span_id; non-initial deltas get
    their own per-uuid span so the trace shows when new skills came
    on/off line mid-session.
    """
    new_uuids: list[str] = []
    for att in attachments:
        if att.uuid in seen:
            continue
        ts = _normalise_attachment_ts(att.timestamp)
        handler = _ATTACHMENT_HANDLERS.get(att.kind)
        # An attachment kind we don't trace still gets marked seen so we
        # don't re-walk it on the next scan.
        post_ok = handler(trace_id, att, ts) if handler else True
        if post_ok:
            new_uuids.append(att.uuid)
    _mark_seen(trace_id, new_uuids)


# ─────────────────────────── local commands ────────────────────────────


def _post_local_command_spans(
    trace_id: str,
    local_commands,
    seen: set[str],
) -> None:
    """Emit one `harness.local_command` span per detected local-command
    invocation — both slash commands (/add-dir, /clear, /usage, …) and
    bang/bash commands (`!ls`). Neither fires UserPromptSubmit, so they
    leave no `prompt` span behind — the transcript scan is the only way
    to surface them in the trace UI.

    Idempotent via `cmd-<command_uuid[:13]>`. The related entry uuids
    (caveat + command-name + stdout for slash; bash-input + stdout for
    bash) are marked seen together so a later transcript pass doesn't
    reprocess any of them.
    """
    from lib.hook_plugin import post_span  # type: ignore

    new_uuids: list[str] = []
    for lc in local_commands:
        if lc.command_uuid in seen:
            continue
        ts = _normalise_attachment_ts(lc.timestamp)
        raw_stdout = lc.stdout_text or ''
        if isinstance(raw_stdout, str):
            stdout_text, stdout_truncated = _truncate_utf8_with_marker(
                raw_stdout, _LOCAL_COMMAND_STDOUT_MAX_BYTES,
            )
        else:
            stdout_text, stdout_truncated = raw_stdout, False
        attrs = {
            'kind': 'local_command',
            'command_name': lc.command_name,
            'args': lc.args,
            'stdout': stdout_text,
            'stdout_truncated': stdout_truncated,
        }
        if lc.stderr_text:
            stderr_text, stderr_truncated = _truncate_utf8_with_marker(
                lc.stderr_text, _LOCAL_COMMAND_STDOUT_MAX_BYTES,
            )
            attrs['stderr'] = stderr_text
            attrs['stderr_truncated'] = stderr_truncated
        if post_span(
            trace_id=trace_id,
            span_id=f'cmd-{lc.command_uuid[:13]}',
            name='harness.local_command',
            start_time=ts, end_time=ts, duration_ms=0,
            attributes=attrs,
        ):
            new_uuids.append(lc.command_uuid)
            if lc.stdout_uuid:
                new_uuids.append(lc.stdout_uuid)
            if lc.caveat_uuid:
                new_uuids.append(lc.caveat_uuid)
    _mark_seen(trace_id, new_uuids)


# ───────────────────────────── live turns ──────────────────────────────


def _build_usage_row(
    trace_id: str, turn, idx: int, fallback_model: str | None,
    effort_level: str | None = None,
) -> dict:
    return {
        'trace_id': trace_id,
        'turn_uuid': turn.uuid,
        'turn_index': idx,
        'timestamp': turn.timestamp,
        'model': turn.model or fallback_model,
        'input_tokens': turn.input_tokens,
        'output_tokens': turn.output_tokens,
        'cache_read_tokens': turn.cache_read_tokens,
        'cache_creation_tokens': turn.cache_creation_tokens,
        'context_used_tokens': turn.context_used,
        'request_id': turn.request_id,
        'effort_level': effort_level,
    }


def _resolve_server_parent_id(turn, capture_text: bool) -> str | None:
    """Server-tool spans nest under the turn's `assistant_response` (or
    `assistant.thinking` when the turn carries no text). The model emits
    text-blocks-then-tool_use within one turn, so the call is
    semantically triggered by the response. A parent_id link encodes
    that directly: render order falls out of the tree without depending
    on the +1 ms transcript-time stagger, which JS Date.getTime() rounds
    away anyway. When the turn has neither text nor thinking_blocks,
    the server-tool stays an orphan and `_graft_orphans` falls back to
    the prompt parent."""
    if not capture_text:
        return None
    if turn.text:
        return f'resp-{turn.uuid[:13]}'
    if turn.thinking_blocks:
        return f'think-{turn.uuid[:13]}'
    return None


def _truncate_response_text(
    resp: str,
    attrs: dict,
    capture_text: bool,
    max_text_bytes: int,
) -> str:
    if not capture_text or not max_text_bytes or max_text_bytes <= 0:
        return resp
    encoded = resp.encode('utf-8')
    if len(encoded) <= max_text_bytes:
        return resp
    attrs['response_truncated'] = True
    return encoded[:max_text_bytes].decode('utf-8', errors='ignore') + '\n\n…[truncated]'


def _build_server_tool_attrs(
    tool_name: str,
    tu_id: str,
    turn_uuid: str,
    tc: dict,
    capture_text: bool,
    max_text_bytes: int,
) -> dict:
    attrs: dict = {
        'tool_name': tool_name,
        'tool_use_id': tu_id,
        'server_side': True,
        'turn_uuid': turn_uuid,
    }
    advisor_model = tc.get('advisor_model')
    if advisor_model:
        attrs['advisor_model'] = advisor_model
    resp = tc.get('response_text')
    if isinstance(resp, str) and resp:
        # Server-tool response text (e.g. advisor's reply). Same byte
        # cap as assistant_response so a long advisor reply doesn't blow
        # up the span attributes blob.
        resp = _truncate_response_text(resp, attrs, capture_text, max_text_bytes)
        attrs['response_text'] = resp
        attrs['response_chars'] = len(resp)
    return attrs


def _emit_server_tool_spans(
    trace_id: str,
    turn,
    server_parent_id: str | None,
    capture_text: bool,
    max_text_bytes: int,
) -> None:
    """Server-side tools (e.g. `advisor`) never fire a local PostToolUse
    hook, so `post_tool_trace.py` never creates a `tool.<name>` row for
    them. Synthesize one here so the session-trace view shows the call
    and the subsequent tool_attribution UPDATE has a row to land tokens
    on. Idempotent via deterministic span_id."""
    from datetime import timedelta
    from lib.hook_plugin import post_span  # type: ignore

    # Resolve the turn timestamp once as a datetime so we can stagger
    # sibling server-tool spans a few milliseconds apart within the
    # same parent — keeps invocation order stable when one assistant
    # turn calls multiple server tools.
    base_dt = _to_naive_datetime(turn.timestamp)
    server_idx = 0
    for tc in turn.tool_calls:
        if not tc.get('server_side'):
            continue
        tu_id = tc.get('id')
        tool_name = tc.get('name')
        if not isinstance(tu_id, str) or not isinstance(tool_name, str):
            continue
        server_idx += 1
        attrs = _build_server_tool_attrs(
            tool_name, tu_id, turn.uuid, tc, capture_text, max_text_bytes,
        )
        srv_ts = (
            (base_dt + timedelta(milliseconds=server_idx)).isoformat()
            if base_dt is not None else turn.timestamp
        )
        post_span(
            trace_id=trace_id,
            span_id=f'srvtool-{tu_id[:13]}',
            name=f'tool.{tool_name}',
            parent_id=server_parent_id,
            start_time=srv_ts, end_time=srv_ts, duration_ms=0,
            attributes=attrs,
        )


def _classify_error_kind(result_text) -> str | None:
    if _is_permission_deny(result_text):
        return 'deny'
    if _is_tool_use_error(result_text):
        return 'tool_use_error'
    return None


def _build_error_span_args(
    kind: str,
    tool_name: str,
    tu_id: str,
    turn,
    tc: dict,
) -> tuple[dict, str, str]:
    """Return (attrs, span_id, timestamp) for the synth error span."""
    result_text = tc.get('result_text')
    if kind == 'deny':
        attrs = _build_deny_attrs(tool_name, tu_id, turn.uuid, tc, result_text)
        prefix = 'askdeny' if tool_name == 'AskUserQuestion' else 'tooldeny'
        return attrs, f'{prefix}-{tu_id[:13]}', turn.timestamp
    # kind == 'tool_use_error' — Claude Code wraps these in
    # `<tool_use_error>…</tool_use_error>`. PostToolUse never fires
    # (the tool body never ran), so the trace UI loses the call
    # entirely without this synth. Distinct from permission denies
    # (different sentinel, different prefix), distinct from runtime
    # failures (those carry no envelope and reach us via
    # PostToolUseFailure as `tool.failure`).
    attrs = _build_tool_use_error_attrs(tool_name, tu_id, turn.uuid, tc, result_text)
    ts = _normalise_attachment_ts(turn.timestamp) or turn.timestamp
    return attrs, f'toolerr-{tu_id[:13]}', ts


def _emit_deny_and_error_spans(
    trace_id: str,
    turn,
    server_parent_id: str | None,
) -> None:
    """Synthesize tool.* spans for the two cases where PostToolUse never
    fires: permission denies and pre-execution tool_use_error envelopes.
    The transcript IS the ground truth: the tool_use entry holds the
    input, the paired tool_result entry has is_error=true and a deny
    sentinel."""
    from lib.hook_plugin import post_span  # type: ignore
    for tc in turn.tool_calls:
        if tc.get('server_side') or not tc.get('is_error'):
            continue
        kind = _classify_error_kind(tc.get('result_text'))
        if kind is None:
            continue
        tu_id = tc.get('id')
        tool_name = tc.get('name')
        if not isinstance(tu_id, str) or not isinstance(tool_name, str):
            continue
        attrs, span_id, ts = _build_error_span_args(kind, tool_name, tu_id, turn, tc)
        post_span(
            trace_id=trace_id,
            span_id=span_id,
            name=f'tool.{tool_name}',
            parent_id=server_parent_id,
            start_time=ts, end_time=ts, duration_ms=0,
            attributes=attrs,
            status_code='ERROR',
        )


def _post_tool_attribution_event(trace_id: str, turn) -> None:
    """Per-tool token attribution. transcript_usage carries token
    estimates on each tool_call; flatten them into one payload per turn
    so the ingest endpoint can UPDATE matching session_spans.
    Anthropic's API only emits one usage block per turn, so these are
    derived locally. The "Tokens by tool" rollup in the trace UI reads
    these columns directly, so this needs to fire live (not just on
    UserPromptSubmit/Stop)."""
    from lib.hook_plugin import post_event  # type: ignore
    calls = [
        {
            'tool_use_id': tc.get('id'),
            'name': tc.get('name'),
            'output_tokens': tc.get('output_token_estimate'),
            'input_tokens': tc.get('input_token_estimate'),
            'image_tokens': tc.get('image_token_estimate'),
        }
        for tc in turn.tool_calls
        if isinstance(tc.get('id'), str)
    ]
    if not calls:
        return
    post_event('tool_attribution', {
        'trace_id': trace_id,
        'turn_uuid': turn.uuid,
        'tool_calls': calls,
    })


def _compute_response_output_tokens(turn, has_text: bool) -> int:
    """Estimate output_tokens for the assistant_response (or
    assistant.thinking) span so the "Tokens by tool" rollup can show an
    "assistant text" bucket. The API's per-turn output_tokens count
    covers text + tool_use blocks combined; tool_use blocks already get
    attributed to tool.* spans via tool_attribution, so this estimate is
    the text-only remainder. ingest_session_spans promotes it from
    attributes into the output_tokens column for assistant_response
    spans so the rollup query can sum it directly."""
    from lib.tokens.token_estimator import estimate_text_tokens  # type: ignore
    if has_text:
        return estimate_text_tokens(turn.text) if turn.text else 0
    thinking_text_tokens = (
        estimate_text_tokens(turn.thinking_text) if turn.thinking_text else 0
    )
    if thinking_text_tokens:
        return thinking_text_tokens
    # The API frequently redacts thinking text (the transcript carries
    # only an opaque signature). Fall back to the residual of the turn's
    # API-reported output minus the tool-use serialization estimates so
    # redacted thinking still surfaces in the Tokens-by-tool rollup.
    # Clamped to >= 0 since both terms are approximate.
    tool_use_out = sum(
        int(tc.get('output_token_estimate') or 0) for tc in turn.tool_calls
    )
    return max(0, int(turn.output_tokens or 0) - tool_use_out)


def _build_response_attrs(
    turn,
    idx: int,
    fallback_model: str | None,
    has_text: bool,
) -> dict:
    attrs: dict = {
        'turn_uuid': turn.uuid,
        'turn_index': idx,
        'model': turn.model or fallback_model,
        'output_tokens': _compute_response_output_tokens(turn, has_text),
    }
    if has_text:
        attrs['text'] = turn.text
        attrs['truncated'] = turn.text_truncated
        attrs['response_chars'] = len(turn.text)
    if turn.tool_calls:
        attrs['tool_calls'] = [
            {'name': t['name'], 'is_error': t['is_error']}
            for t in turn.tool_calls
        ]
    # Surface extended-thinking metadata even when the text is redacted —
    # `thinking_blocks` and `thinking_signature_bytes` are still non-zero,
    # so downstream queries can ask "did thinking happen on this turn?"
    # without inspecting blobs.
    if turn.thinking_blocks:
        attrs['thinking_blocks'] = turn.thinking_blocks
        attrs['thinking_signature_bytes'] = turn.thinking_signature_bytes
        if turn.thinking_text:
            attrs['thinking_text'] = turn.thinking_text
            attrs['thinking_truncated'] = turn.thinking_text_truncated
    if turn.turn_total_duration_ms is not None:
        attrs['turn_total_duration_ms'] = int(turn.turn_total_duration_ms)
    if turn.inference_duration_ms is not None:
        attrs['inference_duration_ms'] = int(turn.inference_duration_ms)
    return attrs


def _normalise_turn_ts(ts: str) -> str:
    """Transcript timestamps are offset-aware UTC; the server's
    _widen_envelopes mixes them with offset-naive timestamps, so
    convert to local-naive before posting."""
    if not ts.endswith('Z'):
        return ts
    from datetime import datetime
    dt = datetime.fromisoformat(ts.replace('Z', '+00:00'))
    return dt.astimezone().replace(tzinfo=None).isoformat()


def _maybe_emit_assistant_span(
    trace_id: str,
    turn,
    idx: int,
    fallback_model: str | None,
    capture_text: bool,
) -> bool:
    """Per-turn span. Two cases:
      * turn carried user-visible text → emit `assistant_response`
        (renders as a card in the conversation view).
      * turn carried only thinking blocks → emit `assistant.thinking`
        (separate name so the conversation view doesn't render empty
        "response" rows; the timeline can still surface it).

    parent_id is intentionally left unset; `_graft_orphans()` in the
    web layer attaches it to the current prompt based on chronological
    order. Tracks whether persistence actually happened: default True
    means "no post needed" (turn carried nothing postable). When a post
    IS needed, the cache should only land on success — see
    docs/trace/assistant_response_capture_vs_claudecodeui.md.
    """
    if not capture_text or not (turn.text or turn.thinking_blocks):
        return True
    from lib.hook_plugin import post_span  # type: ignore
    has_text = bool(turn.text)
    ts = _normalise_turn_ts(turn.timestamp)
    attrs = _build_response_attrs(turn, idx, fallback_model, has_text)
    span_name = 'assistant_response' if has_text else 'assistant.thinking'
    span_prefix = 'resp' if has_text else 'think'
    # span.duration_ms = per-API-call latency (current entry's
    # timestamp minus the prior content entry's timestamp). This is
    # the "how long did this specific Anthropic call take" metric.
    # The full prompt-cycle duration (every API call + tools + hooks)
    # lands on `attributes.turn_total_duration_ms`.
    inference_ms = int(turn.inference_duration_ms) if turn.inference_duration_ms else 0
    # `ts` is the transcript flush time ≈ inference completion, and is
    # stored as both start_time and end_time (the transcript carries no
    # API-start timestamp). Surface the estimated start as completion −
    # inference latency so consumers have the inference window without
    # us reordering the timeline by moving start_time itself.
    if inference_ms > 0:
        from datetime import datetime, timedelta
        attrs['estimated_start_time'] = (
            datetime.fromisoformat(ts) - timedelta(milliseconds=inference_ms)
        ).isoformat()
    return post_span(
        trace_id=trace_id,
        span_id=f'{span_prefix}-{turn.uuid[:13]}',
        name=span_name,
        start_time=ts, end_time=ts,
        duration_ms=inference_ms,
        attributes=attrs,
    )


def _process_one_turn(
    trace_id: str,
    turn,
    idx: int,
    fallback_model: str | None,
    *,
    capture_text: bool,
    max_text_bytes: int,
) -> bool:
    """Emit every span/event derived from one turn. Returns True iff
    this turn's uuid should be cached so subsequent scans skip it."""
    if turn.tool_calls:
        server_parent_id = _resolve_server_parent_id(turn, capture_text)
        _emit_server_tool_spans(
            trace_id, turn, server_parent_id, capture_text, max_text_bytes,
        )
        _emit_deny_and_error_spans(trace_id, turn, server_parent_id)
        _post_tool_attribution_event(trace_id, turn)
    return _maybe_emit_assistant_span(trace_id, turn, idx, fallback_model, capture_text)


def _post_live_turn_data(
    trace_id: str,
    turns,
    fallback_model: str | None,
    *,
    capture_text: bool,
    seen: set[str],
    max_text_bytes: int = 0,
    effort_level: str | None = None,
) -> None:
    """Post per-turn data (assistant_response + tool_attribution) and a
    batched turn_usage event for every turn not in `seen`. Updates the
    per-session seen-uuid cache so the next invocation skips them.

    Server-side dedup keys (`resp-<uuid[:13]>` for spans, (trace_id,
    turn_uuid) for events) make repeated posts safe — the cache is the
    client-side throttle that keeps PostToolUse from spamming a fresh
    HTTP call per turn per tool invocation.
    """
    from lib.hook_plugin import post_event  # type: ignore
    new_uuids: list[str] = []
    usage_rows: list[dict] = []
    for idx, turn in enumerate(turns):
        if not turn.uuid or not turn.timestamp or turn.uuid in seen:
            continue
        usage_rows.append(_build_usage_row(trace_id, turn, idx, fallback_model,
                                           effort_level))
        if _process_one_turn(
            trace_id, turn, idx, fallback_model,
            capture_text=capture_text, max_text_bytes=max_text_bytes,
        ):
            new_uuids.append(turn.uuid)
    if usage_rows:
        post_event('turn_usage', usage_rows)
    _mark_seen(trace_id, new_uuids)
