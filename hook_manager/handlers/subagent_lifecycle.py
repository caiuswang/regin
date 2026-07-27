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
    if _needs_parent_scoped_reconcile(payload):
        _reconcile_subagents(payload)
    return HookResponse(suppress_output=True)


def handle_sweep(payload: HookPayload) -> HookResponse | None:
    """Turn/session boundary: re-run the provider's subagent reconciliation.

    Hanging reconciliation off `SubagentStop` alone makes a single missed Stop
    permanent, and Kimi frequently never fires one (the reference session has
    two `subagent.start` markers and no stop at all). `Stop`/`SessionEnd` are
    the two boundaries guaranteed to arrive afterwards, and reconciliation is
    idempotent, so re-running there is the self-heal."""
    if _needs_parent_scoped_reconcile(payload):
        _reconcile_subagents(payload)
    return HookResponse(suppress_output=True)


def _needs_parent_scoped_reconcile(payload: HookPayload) -> bool:
    """Whether this provider's subagent activity lands on the PARENT session
    and so has to be re-nested from outside the `SubagentStop` path.

    Keyed on `transcript_format` — the same discriminator the provider-
    dispatched transcript layer uses (`lib/trace/live_rescan`,
    `lib/trace/repair`). A Claude-shaped session already scopes a subagent's
    hooks and transcript to the subagent itself, and its `reconcile_subagents`
    is a full re-walk of every subagent transcript, so firing it at each start
    and each turn `Stop` would be per-turn work that produces no new spans."""
    try:
        provider = payload.resolved_provider
        return getattr(provider, 'transcript_format', 'claude') != 'claude'
    except Exception:
        return False


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
    _reconcile_subagents(payload)
    return HookResponse(suppress_output=True)


def _reconcile_subagents(payload: HookPayload) -> None:
    """Let the session's provider re-nest a subagent's spans if its CLI needs
    it. No-op for providers that already scope sub-tool hooks to the subagent
    session (Claude — the `agent_transcript_path` replay above covers it); Kimi
    fires them under the parent session_id and overrides
    AgentProvider.reconcile_subagents to trigger the server-side reconciler."""
    try:
        payload.resolved_provider.reconcile_subagents(payload.session_id)
    except Exception:
        # Fires on every Stop/SessionEnd, so a persistent delivery failure
        # would otherwise be indistinguishable from a legitimate no-op.
        from lib.activity_log import get_activity_logger
        get_activity_logger('hooks').error(
            'subagent_reconcile_failed',
            session_id=payload.session_id,
            exc_info=True,
        )


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


def _result_preview(raw: dict) -> str | None:
    """A flattened, capped preview of the subagent's final message. Claude uses
    `last_assistant_message`; Kimi Code reports it as `response`."""
    last = raw.get('last_assistant_message') or raw.get('response')
    if not last:
        return None
    flat = ' '.join(str(last).split())
    return flat[:_RESULT_PREVIEW_MAX] + '…' if len(flat) > _RESULT_PREVIEW_MAX else flat


def _is_provider_tag(value) -> bool:
    """True when a would-be agent_type is really a PROVIDER id ('kimi'), not a
    subagent kind. Decided by the provider registry, the same source of truth
    the reconcile-time check in lib/trace/kimi_subagents consults, so the two
    verdicts can't drift."""
    if not isinstance(value, str) or not value.strip():
        return False
    try:
        from lib.providers.registry import is_provider_id  # type: ignore
        return is_provider_id(value)
    except Exception:
        return False


def _agent_kind(raw: dict) -> str | None:
    """The SUBAGENT's own kind, never the provider tag.

    Kimi Code puts its provider id in `agent_type` ('kimi') and the real
    subagent kind in `agent_name` ('explore'), so taking `agent_type` verbatim
    labels every Kimi subagent card "kimi". Claude sends the subagent's type
    slug in `agent_type` and no `agent_name`, so it never takes this branch.
    """
    kind = raw.get('subagent_type') or raw.get('agent_type')
    name = raw.get('subagent_name') or raw.get('agent_name')
    if _is_provider_tag(kind) and isinstance(name, str) and name.strip():
        return name
    return kind or name


def _span_attributes(raw: dict) -> dict:
    """Identity + preview attributes for a subagent marker span. Claude Code has
    used both `agent_*` and `subagent_*` field names across versions — accept
    either. `prompt_preview` (SubagentStart) and `result_preview` (SubagentStop)
    let the Kimi reconciler bind a marker to its wire by content."""
    attrs: dict = {}
    fields = {
        'agent_type': _agent_kind(raw),
        'agent_id': raw.get('subagent_id') or raw.get('agent_id'),
        'agent_name': raw.get('subagent_name') or raw.get('agent_name'),
        'description': raw.get('description'),
        'result_preview': _result_preview(raw),
    }
    attrs.update({k: v for k, v in fields.items() if v})
    prompt = raw.get('prompt')
    if isinstance(prompt, str) and prompt.strip():
        attrs['prompt_preview'] = prompt[:500]
    return attrs


def _emit_span(payload: HookPayload, name: str) -> None:
    from lib.hook_plugin import post_span  # type: ignore
    post_span(
        trace_id=payload.session_id,
        name=name,
        attributes=_span_attributes(payload.raw),
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


# Prompt-span text cap — mirrors the main-agent anchor
# (`span_posters._PROMPT_ANCHOR_TEXT_MAX_BYTES`) so a scoped subagent view opens
# with the same clamp as a normal prompt card. The full launch prompt already
# lives on the `tool.Agent` span; this is the scoped-view fallback.
_SUBAGENT_PROMPT_MAX_BYTES = 8 * 1024


def _first_user_prompt(transcript_path) -> tuple[str, str | None] | None:
    """`(text, timestamp)` of a subagent transcript's first user message — its
    launch prompt — or None. Accepts either a bare-string content or a
    text-block list; a tool_result-only first entry (shouldn't happen for a
    subagent) yields no text."""
    import json
    try:
        with open(transcript_path) as f:
            for line in f:
                line = line.strip()
                if not line or '"user"' not in line:
                    continue
                try:
                    e = json.loads(line)
                except ValueError:
                    continue
                if e.get('type') != 'user':
                    continue
                text = _user_entry_text(e.get('message', {}).get('content'))
                if text:
                    return text, e.get('timestamp')
                return None
    except OSError:
        return None
    return None


def _user_entry_text(content) -> str:
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        for b in content:
            if (isinstance(b, dict) and b.get('type') == 'text'
                    and isinstance(b.get('text'), str) and b['text'].strip()):
                return b['text'].strip()
    return ''


def _emit_subagent_prompt(trace_id, transcript_path, agent_id) -> None:
    """Emit one `prompt` span (`prompt-sa-<agent_id>`) from the subagent's
    launch prompt, agent_id-tagged so a scoped view opens with the task
    statement. Idempotent via the deterministic span_id."""
    import os

    from lib.hook_plugin import post_span  # type: ignore
    if not (isinstance(transcript_path, str) and transcript_path
            and os.path.isfile(transcript_path) and agent_id):
        return
    first = _first_user_prompt(transcript_path)
    if first is None:
        return
    text, ts = first
    capped = text.encode('utf-8')[:_SUBAGENT_PROMPT_MAX_BYTES].decode(
        'utf-8', 'ignore')
    attrs = {'text': capped, 'chars': len(text), 'agent_id': agent_id}
    if len(capped) < len(text):
        attrs['text_truncated'] = True
    ts_norm = _normalize_subagent_ts(ts) if isinstance(ts, str) and ts else None
    post_span(
        trace_id=trace_id, span_id=f'prompt-sa-{agent_id}', name='prompt',
        start_time=ts_norm, end_time=ts_norm, attributes=attrs,
    )


def emit_subagent_responses(trace_id, transcript_path, agent_id, *, seen=None) -> None:
    """Emit one `assistant_response`/`assistant.thinking` span per turn in the
    subagent's own transcript, tagged `agent_id` (the dashboard's `_graft_orphans`
    Pass 5 nests them under the matching `subagent.start`). The one-shot
    (full-read) path used at SubagentStop; the live rescan uses the resumable
    variant below. `seen` (a turn-uuid set) gates re-posts; None posts all
    (idempotent via the `resp-sa-`/`think-sa-` span_id)."""
    _emit_subagent_prompt(trace_id, transcript_path, agent_id)
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
    `ResumableScanState` to thread back into the next poll. The launch-prompt
    span is emitted once, on the first scan (`state is None`) — the first user
    message sits before the committed byte offset on every later poll."""
    if state is None:
        _emit_subagent_prompt(trace_id, transcript_path, agent_id)
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


def _subagent_turn_base_attributes(turn, idx, agent_id, fallback_model) -> dict:
    """The attributes both spans of one subagent turn carry."""
    attributes = {
        'turn_uuid': turn.uuid,
        'turn_index': idx,
        'model': turn.model or fallback_model,
        'agent_id': agent_id,
    }
    if turn.tool_calls:
        attributes['tool_calls'] = [
            {'name': t['name'], 'is_error': t['is_error']}
            for t in turn.tool_calls
        ]
    if turn.inference_duration_ms is not None:
        attributes['inference_duration_ms'] = int(turn.inference_duration_ms)
    if turn.turn_total_duration_ms is not None:
        attributes['turn_total_duration_ms'] = int(turn.turn_total_duration_ms)
    return attributes


def _subagent_thinking_attributes(turn, idx, agent_id, fallback_model) -> dict:
    attributes = _subagent_turn_base_attributes(turn, idx, agent_id, fallback_model)
    attributes['thinking_blocks'] = turn.thinking_blocks
    attributes['thinking_signature_bytes'] = turn.thinking_signature_bytes
    if turn.thinking_text:
        attributes['thinking_text'] = turn.thinking_text
        attributes['thinking_truncated'] = turn.thinking_text_truncated
    out = _subagent_thinking_output_tokens(turn)
    if out:
        attributes['output_tokens'] = out
    return attributes


def _subagent_response_attributes(turn, idx, agent_id, fallback_model) -> dict:
    attributes = _subagent_turn_base_attributes(turn, idx, agent_id, fallback_model)
    attributes['text'] = turn.text
    attributes['truncated'] = turn.text_truncated
    attributes['response_chars'] = len(turn.text)
    return attributes


def _stagger_before(ts: str) -> str:
    """1 ms earlier, so a start_time-ordered tree renders the turn's thinking
    card ahead of its response (same idiom as the main-agent path)."""
    from datetime import datetime, timedelta
    try:
        return (datetime.fromisoformat(ts) - timedelta(milliseconds=1)).isoformat()
    except (ValueError, TypeError):
        return ts


def _thinking_worth_a_span(turn, has_text: bool) -> bool:
    """Drop a content-free thinking block only when the turn also spoke: Kimi
    records an empty `think` part at the tail of most steps, and pairing one
    with a response would render an empty thinking card next to real text.
    A thinking-ONLY turn keeps its span either way — it is the turn's sole
    anchor for the tool summary and token attribution, and Claude's redacted
    reasoning (signature bytes, no text) has to survive here too."""
    if not has_text:
        return True
    return bool(turn.thinking_text or turn.thinking_signature_bytes)


def subagent_turn_spans(turn, idx, agent_id, fallback_model) -> list[dict]:
    """The span row(s) for one subagent turn, mirroring the main-agent pairing
    in `turn_trace.span_posters._maybe_emit_assistant_span`: text →
    `assistant_response`, extended thinking → `assistant.thinking`, and BOTH
    when the turn carried both. Naming the thinking span separately keeps the
    conversation view from rendering an empty response card; emitting it
    alongside the response is what keeps a subagent's reasoning from being
    dropped whenever it spoke in the same turn.

    Shared with `lib.trace.kimi_subagents`, which writes the same rows straight
    to the DB during reconciliation — the two paths must not drift."""
    ts = _normalize_subagent_ts(turn.timestamp)
    has_text = bool(turn.text)
    duration_ms = int(turn.inference_duration_ms or 0)
    spans: list[dict] = []
    if turn.thinking_blocks and _thinking_worth_a_span(turn, has_text):
        spans.append({
            'span_id': f'think-sa-{turn.uuid[:13]}',
            'name': 'assistant.thinking',
            'start_time': _stagger_before(ts) if has_text else ts,
            'duration_ms': 0 if has_text else duration_ms,
            'attributes': _subagent_thinking_attributes(
                turn, idx, agent_id, fallback_model),
        })
    if has_text:
        spans.append({
            'span_id': f'resp-sa-{turn.uuid[:13]}',
            'name': 'assistant_response',
            'start_time': ts,
            'duration_ms': duration_ms,
            'attributes': _subagent_response_attributes(
                turn, idx, agent_id, fallback_model),
        })
    return spans


def _post_one_subagent_turn(trace_id, turn, idx, agent_id, fallback_model) -> None:
    """Emit the assistant_response / assistant.thinking span(s) for one
    subagent turn."""
    from lib.hook_plugin import post_span  # type: ignore
    for span in subagent_turn_spans(turn, idx, agent_id, fallback_model):
        post_span(
            trace_id=trace_id,
            span_id=span['span_id'],
            name=span['name'],
            start_time=span['start_time'],
            end_time=span['start_time'],
            duration_ms=span['duration_ms'],
            attributes=span['attributes'],
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
