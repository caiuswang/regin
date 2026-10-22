"""Post spans + turn_usage events for one transcript's worth of data.

The transcript parser (`lib.trace.transcript_usage.read_usage`) returns
four lists of rows:

  * `turns` — assistant turns with usage + per-call attribution
  * `attachments` — task_reminder / skill_listing / deferred_tools_delta
  * `system_events` — stop_hook_summary / turn_duration / away_summary
  * `local_commands` — /add-dir, /clear, /usage, `!ls`, etc.

This module turns the turns into ingest calls; `event_spans` and
`prompt_anchor_spans` handle the other lists. Idempotency comes from
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
    build_interrupt_attrs,
    build_recorded_deny_attrs,
)
from .event_spans import (
    _post_attachment_spans,
    _post_local_command_spans,
    _post_rewind_spans,
    _post_system_event_spans,
)
from .prompt_anchor_spans import (
    _cap_images,
    _normalise_turn_ts,
    _post_prompt_anchor_spans,
)
from .timestamps import _normalise_attachment_ts, _to_naive_datetime

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
        'reasoning_tokens': turn.reasoning_tokens,
    }


def _prompt_parent_id(turn) -> str | None:
    """Write-time parent for an `assistant_response` / `assistant.thinking`
    span: the turn's prompt anchor (`prompt-<prompt_uuid[:13]>`), where
    `prompt_uuid` was resolved by the parentUuid walk in
    `transcript_usage._resolve_prompt_anchors`. Returns None (→ root) when
    the turn has no resolvable real prompt (e.g. a workflow-resume turn).
    Setting this at write time is what lets the read path be pure tree
    assembly instead of chronological grafting."""
    return f'prompt-{turn.prompt_uuid[:13]}' if turn.prompt_uuid else None


def _resolve_server_parent_id(turn, capture_text: bool) -> str | None:
    """Parent for a turn's tool spans (live, server, and deny/error
    synth). They nest under the turn's `assistant_response` (or
    `assistant.thinking` when the turn carries no text) — the model emits
    text-blocks-then-tool_use within one turn, so the call is
    semantically triggered by the response, and a parent_id link encodes
    that directly without depending on the +1 ms transcript-time stagger.

    When no `resp-`/`think-` span exists for the turn (a silent
    tool-only turn, or text capture disabled), fall back to the turn's
    prompt anchor so the tool gets a deterministic parent at write time
    instead of orphaning — `prompt-<prompt_uuid>`, or None (→ root) when
    the turn has no resolvable real prompt."""
    if capture_text and turn.text:
        return f'resp-{turn.uuid[:13]}'
    if capture_text and turn.thinking_blocks:
        return f'think-{turn.uuid[:13]}'
    return _prompt_parent_id(turn)


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
    """Synthesize tool.* spans for the three cases where PostToolUse never
    fires: permission denies, pre-execution tool_use_error envelopes, and a
    user interrupt of an in-flight tool. The transcript IS the ground truth:
    the tool_use entry holds the input; a deny/error carries a paired
    tool_result (is_error=true + sentinel); an interrupt is a standalone user
    *text* entry (`interrupted` flagged onto the call by
    transcript_usage._flag_interrupted_tool_calls)."""
    from lib.hook_plugin import post_span  # type: ignore
    for tc in turn.tool_calls:
        if tc.get('server_side'):
            continue
        spec = _synth_span_spec(turn, tc)
        if spec is None:
            continue
        span_id, attrs, ts = spec
        # Normalise to the offset-naive local shape every other poster uses
        # (_normalise_attachment_ts). A lone tz-aware `…Z` synth span in an
        # otherwise-naive session crashes the ingest-time re-projection
        # (`datetime` delta of mixed naive/aware), so the whole batch 500s
        # and the span is silently lost — exactly why deny/interrupt synths
        # never persisted. Idempotent on the tool_use_error path, which
        # already normalised.
        ts = _normalise_attachment_ts(ts) or ts
        post_span(
            trace_id=trace_id,
            span_id=span_id,
            name=f'tool.{attrs["tool_name"]}',
            parent_id=server_parent_id,
            start_time=ts, end_time=ts, duration_ms=0,
            attributes=attrs,
            status_code='ERROR',
        )


def _synth_span_spec(turn, tc: dict) -> tuple[str, dict, str] | None:
    """Resolve (span_id, attrs, timestamp) for a tool_call that needs a synth
    span, or None when it doesn't. Covers the user-interrupt case (no
    tool_result, `interrupted` flagged) and the deny / tool_use_error cases
    (is_error=true with a recognized sentinel)."""
    tu_id = tc.get('id')
    tool_name = tc.get('name')
    if not isinstance(tu_id, str) or not isinstance(tool_name, str):
        return None
    # Interrupt: no paired tool_result, so is_error stays None — handled
    # before the is_error gate. The is_error-delivered interrupt variant
    # keeps is_error=True and is left to its live `tool.failure` span.
    if tc.get('interrupted') and not tc.get('is_error'):
        attrs = build_interrupt_attrs(tool_name, tu_id, turn.uuid, tc)
        return f'toolintr-{tu_id[:13]}', attrs, turn.timestamp
    if not tc.get('is_error'):
        return None
    kind = _classify_error_kind(tc.get('result_text'))
    if kind is None:
        return None
    attrs, span_id, ts = _build_error_span_args(kind, tool_name, tu_id, turn, tc)
    return span_id, attrs, ts


def _post_permission_denial_spans(trace_id: str, denials) -> None:
    """Materialise a `tool.<name>` deny span for each transcript-recorded
    permission denial (Kimi). The denied call fires no PostToolUse, so its
    PENDING tool span is never resolved and the serve-time merge drops it —
    without this the rejected call vanishes from the trace entirely. Attrs come
    from `build_recorded_deny_attrs` (the same deny contract as the Claude
    transcript-deny path) so the UI renders it identically. Idempotent via
    `tooldeny-<id>`."""
    from lib.hook_plugin import post_span  # type: ignore
    for d in denials:
        tu_id = d.get('tool_use_id')
        tool_name = d.get('tool_name')
        if not isinstance(tu_id, str) or not tu_id or not isinstance(tool_name, str):
            continue
        attrs = build_recorded_deny_attrs(
            tool_name, tu_id, d.get('denial_reason'), d.get('tool_input'),
        )
        ts = d.get('timestamp')
        post_span(
            trace_id=trace_id,
            span_id=f'tooldeny-{tu_id[:13]}',
            name=f'tool.{tool_name}',
            start_time=ts, end_time=ts, duration_ms=0,
            attributes=attrs,
            status_code='ERROR',
        )


def _post_tool_attribution_event(
    trace_id: str, turn, server_parent_id: str | None = None,
) -> None:
    """Per-tool token attribution. transcript_usage carries token
    estimates on each tool_call; flatten them into one payload per turn
    so the ingest endpoint can UPDATE matching session_spans.
    Anthropic's API only emits one usage block per turn, so these are
    derived locally. The "Tokens by tool" rollup in the trace UI reads
    these columns directly, so this needs to fire live (not just on
    UserPromptSubmit/Stop).

    `server_parent_id` (the issuing turn's `resp-`/`think-` span, or None)
    rides along so ingest can backfill the live `tool.*` span's parent_id
    by tool_use_id — the live PostToolUse span is posted parent-less
    because at PostToolUse time the issuing turn isn't yet known. This is
    a parent UPDATE on the existing row, never a re-post, so the rich
    PostToolUse attributes (diff/stdout) are preserved.

    `duration_ms` / `source_prompt_id` ride along for the same reason: a
    provider whose CLI reports neither on the hook payload (Kimi) can only
    recover them from the transcript, and ingest fills them in fill-only
    (a hook-measured duration is never clobbered)."""
    from lib.hook_plugin import post_event  # type: ignore
    calls = [
        {
            'tool_use_id': tc.get('id'),
            'name': tc.get('name'),
            'output_tokens': tc.get('output_token_estimate'),
            'input_tokens': tc.get('input_token_estimate'),
            'image_tokens': tc.get('image_token_estimate'),
            'duration_ms': tc.get('duration_ms'),
            'source_prompt_id': tc.get('source_prompt_id'),
        }
        for tc in turn.tool_calls
        if isinstance(tc.get('id'), str)
    ]
    if not calls:
        return
    post_event('tool_attribution', {
        'trace_id': trace_id,
        'turn_uuid': turn.uuid,
        'parent_span_id': server_parent_id,
        'tool_calls': calls,
    })


def _compute_response_output_tokens(turn) -> int:
    """output_tokens for the `assistant_response` span: the user-visible
    prose only. The API's per-turn output_tokens covers text + thinking +
    tool_use combined; thinking goes to `assistant.thinking` and tool_use
    to `tool.*`, so the response span carries just the text estimate.
    ingest_session_spans promotes it from attributes into the
    output_tokens column so the rollup can sum it as the
    'assistant_text' bucket.

    A failed API call is the one response whose text was never generated by
    a model — estimating its error string would invent output tokens the
    request was not billed for."""
    from lib.tokens.token_estimator import estimate_text_tokens  # type: ignore
    if getattr(turn, 'is_api_error', False):
        return 0
    return estimate_text_tokens(turn.text) if turn.text else 0


def _compute_thinking_output(turn) -> int:
    """output_tokens for the `assistant.thinking` span.

    Captured plaintext thinking is tokenized directly. Encrypted thinking
    (the API returned only an opaque `signature` — see
    `transcript_models.has_encrypted_thinking`) carries no tokenizable
    text, so its cost is recovered by subtraction: the turn's
    API-reported output minus what we *can* estimate — the response text
    and the raw, non-server tool_use blocks. Redistribution was skipped
    for that shape (same predicate), so those tool estimates are still
    raw and this subtraction is self-consistent. The small cl100k framing
    undershoot lands here too — honestly grouped with the un-tokenizable
    reasoning rather than smeared onto a tool. Server-side tools (advisor)
    are excluded: they bill via a separate `iterations` entry, NOT this
    turn's output_tokens. Clamped >= 0 since the terms are approximate."""
    from lib.tokens.token_estimator import estimate_text_tokens  # type: ignore
    if turn.thinking_text:
        return estimate_text_tokens(turn.thinking_text)
    text_est = estimate_text_tokens(turn.text) if turn.text else 0
    raw_tool_use = sum(
        int(tc.get('output_token_estimate') or 0)
        for tc in turn.tool_calls if not tc.get('server_side')
    )
    return max(0, int(turn.output_tokens or 0) - text_est - raw_tool_use)


def _add_tool_summary(turn, attrs: dict) -> None:
    """Attach the turn's tool-call summary to the span that tool spans
    nest under (the primary span — see `_resolve_server_parent_id`)."""
    if turn.tool_calls:
        attrs['tool_calls'] = [
            {'name': t['name'], 'is_error': t['is_error']}
            for t in turn.tool_calls
        ]


def _add_durations(turn, attrs: dict) -> None:
    if turn.turn_total_duration_ms is not None:
        attrs['turn_total_duration_ms'] = int(turn.turn_total_duration_ms)
    if turn.inference_duration_ms is not None:
        attrs['inference_duration_ms'] = int(turn.inference_duration_ms)


def _add_message_id(turn, attrs: dict) -> None:
    """The API `message.id` this turn was built from.

    Cross-writer identity, not display: an SDK-launched run is traced twice and
    this is the only id both writers see for one assistant message
    (`lib.trace.merge_cross_source._row_identity`). Absent on turns the transcript keyed by
    request-id or entry-uuid instead. Every hook-side writer of an assistant
    span must stamp it — including `subagent_lifecycle` — because the merge
    reads a missing id as "no opinion", so a pair where only one side names
    itself falls back to matching text against the clock."""
    if getattr(turn, 'message_id', None):
        attrs['message_id'] = turn.message_id


def _build_response_attrs(turn, idx: int, fallback_model: str | None) -> dict:
    attrs: dict = {
        'turn_uuid': turn.uuid,
        'turn_index': idx,
        'model': turn.model or fallback_model,
        'output_tokens': _compute_response_output_tokens(turn),
        'text': turn.text,
        'truncated': turn.text_truncated,
        'response_chars': len(turn.text),
    }
    if getattr(turn, 'is_api_error', False):
        attrs['is_api_error'] = True
    _add_message_id(turn, attrs)
    _add_tool_summary(turn, attrs)
    _add_durations(turn, attrs)
    return attrs


def _build_thinking_attrs(
    turn, idx: int, fallback_model: str | None, *, is_primary: bool,
) -> dict:
    """Attributes for the turn's `assistant.thinking` span. On a
    thinking-only turn (`is_primary`) it owns the tool summary + durations
    because tool spans nest under it; when the turn also has text the
    response span is primary and this stays a lean leaf carrying just the
    reasoning's token cost and the extended-thinking presence metadata
    (`thinking_blocks`/`thinking_signature_bytes` prove reasoning happened
    even when the text is an encrypted signature)."""
    attrs: dict = {
        'turn_uuid': turn.uuid,
        'turn_index': idx,
        'model': turn.model or fallback_model,
        'output_tokens': _compute_thinking_output(turn),
        'thinking_blocks': turn.thinking_blocks,
        'thinking_signature_bytes': turn.thinking_signature_bytes,
    }
    _add_message_id(turn, attrs)
    if turn.thinking_text:
        attrs['thinking_text'] = turn.thinking_text
        attrs['thinking_truncated'] = turn.thinking_text_truncated
    if is_primary:
        _add_tool_summary(turn, attrs)
        _add_durations(turn, attrs)
    return attrs


def _stagger_before(ts: str) -> str:
    """`ts` minus 1 ms — so a sibling span sorts immediately ahead of the
    one at `ts` in the start_time-ordered conversation tree. Matches the
    existing +1 ms transcript-time stagger idiom."""
    from datetime import datetime, timedelta
    try:
        return (datetime.fromisoformat(ts) - timedelta(milliseconds=1)).isoformat()
    except (ValueError, TypeError):
        return ts


def _post_assistant_span(
    trace_id: str, turn, name: str, prefix: str, ts: str, attrs: dict,
    *, primary: bool,
) -> bool:
    """Post one assistant span (`resp-`/`think-<uuid[:13]>`). Only the
    primary span carries the per-API-call latency: span.duration_ms is
    the inference time, and `estimated_start_time` (completion −
    inference) gives consumers the inference window without us reordering
    the timeline by moving start_time itself."""
    from lib.hook_plugin import post_span  # type: ignore
    inference_ms = (
        int(turn.inference_duration_ms)
        if primary and turn.inference_duration_ms else 0
    )
    if inference_ms > 0:
        from datetime import datetime, timedelta
        attrs['estimated_start_time'] = (
            datetime.fromisoformat(ts) - timedelta(milliseconds=inference_ms)
        ).isoformat()
    return post_span(
        trace_id=trace_id,
        span_id=f'{prefix}-{turn.uuid[:13]}',
        name=name,
        parent_id=_prompt_parent_id(turn),
        start_time=ts, end_time=ts,
        duration_ms=inference_ms,
        attributes=attrs,
    )


def _maybe_emit_assistant_span(
    trace_id: str,
    turn,
    idx: int,
    fallback_model: str | None,
    capture_text: bool,
) -> bool:
    """Per-turn assistant span(s). A turn can carry text, extended
    thinking, or both:
      * text          → `assistant_response` (renders as a card).
      * thinking only → `assistant.thinking` (separate name so the
                        conversation view doesn't render empty "response"
                        rows; it also owns the turn's tool summary).
      * both          → BOTH spans. The thinking span is staggered 1 ms
                        earlier so the start_time-ordered tree renders the
                        thinking card ahead of the response, and it
                        carries the reasoning's token cost so that lands
                        in the `assistant_thinking` rollup bucket instead
                        of inflating whichever tool happened to follow the
                        reasoning.

    parent_id is the turn's prompt anchor for both, set at write time so
    the read path nests without chronological grafting. Returns True iff
    every needed post succeeded (default True = nothing postable). A
    partial failure leaves the turn un-cached so the next scan retries;
    the deterministic `resp-`/`think-` span ids make re-posts idempotent.
    See docs/trace/assistant_response_capture_vs_claudecodeui.md.
    """
    if not capture_text or not (turn.text or turn.thinking_blocks):
        return True
    ts = _normalise_turn_ts(turn.timestamp)
    has_text = bool(turn.text)
    ok = True
    if turn.thinking_blocks:
        think_ts = _stagger_before(ts) if has_text else ts
        think_attrs = _build_thinking_attrs(
            turn, idx, fallback_model, is_primary=not has_text,
        )
        ok = _post_assistant_span(
            trace_id, turn, 'assistant.thinking', 'think', think_ts,
            think_attrs, primary=not has_text,
        ) and ok
    if has_text:
        resp_attrs = _build_response_attrs(turn, idx, fallback_model)
        ok = _post_assistant_span(
            trace_id, turn, 'assistant_response', 'resp', ts,
            resp_attrs, primary=True,
        ) and ok
    return ok


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
    this turn's uuid should be cached so subsequent scans skip it — only
    once the turn's text posted AND every tool call reached a terminal
    state. Caching a turn whose tool is still pending would lock out the
    later scan that finally sees a deny/error/interrupt status, dropping
    its synth span (the `seen`-gate skips the whole turn). All posts here
    are idempotent (deterministic span ids / UPSERTs), so reprocessing an
    unresolved turn until it resolves is safe."""
    if turn.tool_calls:
        server_parent_id = _resolve_server_parent_id(turn, capture_text)
        _emit_server_tool_spans(
            trace_id, turn, server_parent_id, capture_text, max_text_bytes,
        )
        _emit_deny_and_error_spans(trace_id, turn, server_parent_id)
        _post_tool_attribution_event(trace_id, turn, server_parent_id)
    posted = _maybe_emit_assistant_span(trace_id, turn, idx, fallback_model, capture_text)
    return posted and _turn_tools_resolved(turn)


def _turn_tools_resolved(turn) -> bool:
    """True when every non-server tool_call has reached a terminal state in
    the transcript: a paired tool_result (`is_error` set — success, deny, or
    error) or a user interrupt (`interrupted`). A turn with a still-pending
    tool stays uncached so a denial / interrupt that lands after the turn's
    text first posts still drives its synth span on a later scan."""
    for tc in turn.tool_calls:
        if tc.get('server_side'):
            continue
        if tc.get('is_error') is None and not tc.get('interrupted'):
            return False
    return True


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
    # Gate the seen-cache on the usage post landing. post_event returns
    # False (never raises) on a transient ingest outage; marking these
    # turns seen anyway would lock them out of re-processing forever and
    # permanently lose their turn_usage rows (and, for silent tool-only
    # turns, their only DB footprint). Leaving them unseen lets the next
    # scan retry — every span/event re-post is an idempotent UPSERT, so a
    # redundant re-post of the rows that did land is harmless.
    if usage_rows and not post_event('turn_usage', usage_rows):
        return
    _mark_seen(trace_id, new_uuids)
