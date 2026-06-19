"""Hook dispatch entry points + global model + title emitters.

The handler runs on every UserPromptSubmit, SessionEnd, Stop, and
PostToolUse event. UserPromptSubmit/SessionEnd/Stop take the full path
(`_emit_span`): emit the `turn` model span, refresh the `session.title`
span if the title changed, then ingest the whole transcript's turn /
attachment / system-event / local-command rows.

PostToolUse takes the lean fast path (`_emit_assistant_response_only`):
the transcript scan still runs, but the global `turn` model span is
skipped — a /model switch can only happen on a user prompt, not while
tools are mid-flight. The seen-uuid cache makes the typical case (no
new turn since the last tool call) cost one transcript scan plus zero
HTTP calls.
"""

from __future__ import annotations

import json
import os

from ...core import HookPayload, HookResponse
from .cache import (
    _load_ai_title,
    _load_seen,
    _read_session_title,
    _save_ai_title,
)
from .span_posters import (
    _post_attachment_spans,
    _post_live_turn_data,
    _post_local_command_spans,
    _post_permission_denial_spans,
    _post_prompt_anchor_spans,
    _post_rewind_spans,
    _post_system_event_spans,
)

_TAIL_BYTES = 64 * 1024  # cap the read at 64 KiB — only need the last turn


def handle(payload: HookPayload) -> HookResponse | None:
    try:
        if payload.event in ('PostToolUse', 'PreToolUse'):
            # Lean fast path on both tool boundaries. PreToolUse fires when a
            # tool is *proposed* — before any permission prompt resolves — so
            # the thinking/response the agent just wrote lands immediately
            # instead of waiting (potentially minutes) for the user to approve
            # and PostToolUse to fire. Throttled by the seen-uuid cache.
            _emit_assistant_response_only(payload)
        else:
            _emit_span(payload)
    except Exception:
        pass
    return HookResponse(suppress_output=True)


def _payload_effort_level(payload: HookPayload) -> str | None:
    """The `effort.level` carried on most assistant-activity payloads
    (PostToolUse / Stop / …). UserPromptSubmit omits it; returns None
    there so the ingest COALESCE preserves any value a later event sets."""
    effort = payload.raw.get('effort')
    if isinstance(effort, dict):
        level = effort.get('level')
        if isinstance(level, str) and level:
            return level
    return None


def _post_turn_model_span(trace_id: str, model: str) -> None:
    from lib.hook_plugin import post_span  # type: ignore
    post_span(
        trace_id=trace_id,
        name='turn',
        attributes={'model': model},
    )


def _emit_session_title_if_changed(trace_id: str, transcript_path: str) -> None:
    """Refresh the `session.title` span only when the (source, text)
    pair differs from the last cached emit. Sentinel keys on both
    source and text — a /rename that swaps source from `claude_ai_title`
    to `user_rename` must re-emit even if the text happened to match.
    Stable span_id so re-emits on later Stop ticks UPSERT the same row
    rather than spamming new spans."""
    from lib.hook_plugin import post_span  # type: ignore
    title, title_source = _read_session_title(transcript_path)
    if not title:
        return
    cache_key = f'{title_source}:{title}'
    if cache_key == _load_ai_title(trace_id):
        return
    post_span(
        trace_id=trace_id,
        name='session.title',
        span_id=f'sttl-{trace_id[:24]}',
        attributes={'text': title, 'source': title_source},
    )
    _save_ai_title(trace_id, cache_key)


def _sorted_turn_timestamps(turns) -> list:
    """Sorted raw turn timestamps. A recovered queued-prompt anchor is re-timed
    to the first turn at-or-after its `queued_command` attachment timestamp —
    the attachment is contiguous with its first response in the transcript
    (possibly via intermediate attachments), so that turn is its response."""
    return sorted(t.timestamp for t in turns if t.timestamp)


def _text_capture_config() -> tuple[bool, int, int | None]:
    """Returns `(capture_text, max_text_bytes, read_cap)`.

    `read_cap` is the per-turn byte cap handed to the parser (None when
    capture is off or the cap is 0); `max_text_bytes` is the raw setting
    handed to the span poster — the two intentionally differ, so they're
    derived together here for both the full and resumable ingest paths."""
    from lib.settings import settings  # type: ignore

    capture_text = bool(getattr(settings, 'capture_assistant_response', True))
    max_text_bytes = int(getattr(settings, 'assistant_response_max_bytes', 50_000) or 0)
    read_cap = max_text_bytes if capture_text and max_text_bytes > 0 else None
    return capture_text, max_text_bytes, read_cap


def _post_transcript_usage(
    trace_id: str,
    usage,
    fallback_model: str | None,
    *,
    capture_text: bool,
    max_text_bytes: int,
    effort_level: str | None,
) -> None:
    """Emit every derived span/event for rows the seen-uuid cache hasn't
    already accepted. Shared by the full (`read_usage`) and resumable
    (`read_usage_resumable`) ingest paths.

    Stray `prompt` placeholders the live UserPromptSubmit hook leaves for
    turn-less submissions (client-only slash commands like `/workflows`) are
    not reconciled away here — the append-only store keeps them and the
    serve-time merge drops them once a newer prompt lands
    (lib/trace/merge.py:_drop_stale_blockers)."""
    if usage is None:
        return
    seen = _load_seen(trace_id)
    _post_live_turn_data(
        trace_id, usage.turns, usage.model or fallback_model,
        capture_text=capture_text,
        max_text_bytes=max_text_bytes,
        seen=seen,
        effort_level=effort_level,
    )
    # sorted turn timestamps let a recovered queued prompt anchor sort at its
    # first response instead of its type-time (which falls mid the interrupted
    # turn) — see _post_queued_command_span.
    _post_attachment_spans(trace_id, usage.attachments, seen,
                           _sorted_turn_timestamps(usage.turns))
    _post_system_event_spans(trace_id, usage.system_events, seen)
    _post_local_command_spans(trace_id, usage.local_commands, seen)
    # Provider-recorded permission denials (Kimi). Empty for Claude, whose
    # denials arrive live via the PermissionDenied hook.
    _post_permission_denial_spans(trace_id, usage.permission_denials)
    _post_prompt_anchor_spans(trace_id, usage.turns, usage.prompt_texts,
                              usage.prompt_timestamps, usage.prompt_image_parts,
                              seen)
    # `/rewind` markers last: their attributes reference the abandoned spans
    # the posters above just emitted (the projection collapses them at read).
    _post_rewind_spans(trace_id, usage.rewinds, seen)


def _ingest_transcript_usage(
    trace_id: str,
    transcript_path: str,
    fallback_model: str | None,
    provider,
    effort_level: str | None = None,
) -> str | None:
    """Parse the transcript (via the provider's parser) and emit every derived
    span/event for rows the seen-uuid cache hasn't already accepted. Returns
    the model the transcript reported, so callers without a cheaper model read
    (non-Claude formats) can post the `turn` model span from the same parse."""
    capture_text, max_text_bytes, read_cap = _text_capture_config()
    usage = provider.parse_transcript(transcript_path, max_text_bytes=read_cap)
    _post_transcript_usage(
        trace_id, usage, fallback_model,
        capture_text=capture_text, max_text_bytes=max_text_bytes,
        effort_level=effort_level,
    )
    return usage.model if usage else None


def ingest_transcript_usage_resumable(
    trace_id: str,
    transcript_path: str,
    state,
    *,
    fallback_model: str | None = None,
    effort_level: str | None = None,
):
    """Resumable variant for the long-lived server poll: parse only bytes
    appended since the last call (reusing the accumulator in `state`), then
    post the same spans. Returns the updated `ResumableScanState` to thread
    back into the next call. Only safe in the server process — hook
    subprocesses can't share the in-memory accumulator, so they keep using
    `_ingest_transcript_usage`."""
    from lib.trace.transcript_usage import read_usage_resumable  # type: ignore

    capture_text, max_text_bytes, read_cap = _text_capture_config()
    usage, state = read_usage_resumable(
        transcript_path, state, max_text_bytes=read_cap,
    )
    _post_transcript_usage(
        trace_id, usage, fallback_model,
        capture_text=capture_text, max_text_bytes=max_text_bytes,
        effort_level=effort_level,
    )
    return state


def _emit_span(payload: HookPayload) -> None:
    provider = payload.resolved_provider
    transcript_path = provider.resolve_transcript_path(payload)
    if not transcript_path:
        return
    effort = _payload_effort_level(payload)
    if getattr(provider, 'transcript_format', 'claude') == 'claude':
        # Claude-only enrichment: a cheap tail-read of the latest model (so the
        # `turn` span lands before the full parse) plus the session-title span.
        model = _latest_turn_model(transcript_path)
        if model:
            _post_turn_model_span(payload.session_id, model)
        _emit_session_title_if_changed(payload.session_id, transcript_path)
        _ingest_transcript_usage(payload.session_id, transcript_path, model, provider,
                                 effort_level=effort)
    else:
        # Other formats (Kimi) carry the model in the same parse, so derive the
        # `turn` model span from the ingest rather than scanning the file twice
        # (Kimi's SessionStart payload carries no model).
        model = _ingest_transcript_usage(payload.session_id, transcript_path, None,
                                         provider, effort_level=effort)
        if model:
            _post_turn_model_span(payload.session_id, model)


def _emit_assistant_response_only(payload: HookPayload) -> None:
    """PostToolUse fast path: ingest per-turn data (assistant_response +
    tool_attribution + turn_usage) for newly-written turns only. Skips
    the global `turn` model span — that's only needed to catch a /model
    switch and still runs on UserPromptSubmit / Stop / SessionEnd.

    Throttled by the per-session seen-uuid cache so the typical case
    (no new turn since the last tool call) costs one transcript scan
    plus zero HTTP calls.
    """
    provider = payload.resolved_provider
    transcript_path = provider.resolve_transcript_path(payload)
    if not transcript_path:
        return
    _ingest_transcript_usage(payload.session_id, transcript_path, None, provider,
                             effort_level=_payload_effort_level(payload))


def _read_tail_lines(path: str) -> list[str] | None:
    """Return the last `_TAIL_BYTES` worth of decoded, newline-split
    lines from a transcript, dropping the partial-prefix line when we
    seeked mid-line. None on any I/O error."""
    try:
        size = os.path.getsize(path)
        with open(path, 'rb') as f:
            if size > _TAIL_BYTES:
                f.seek(-_TAIL_BYTES, os.SEEK_END)
            chunk = f.read()
    except OSError:
        return None
    text = chunk.decode('utf-8', errors='replace')
    lines = text.split('\n')
    if size > _TAIL_BYTES and lines:
        lines = lines[1:]
    return lines


def _find_latest_assistant_model(lines: list[str]) -> str | None:
    for raw in reversed(lines):
        raw = raw.strip()
        if not raw:
            continue
        try:
            entry = json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            continue
        if entry.get('type') != 'assistant':
            continue
        msg = entry.get('message') or {}
        m = msg.get('model')
        if isinstance(m, str) and m.strip() and m != '<synthetic>':
            return m
    return None


def _latest_turn_model(path: str) -> str | None:
    """Return the `model` on the last `type: assistant` entry of a jsonl
    transcript, or None. Reads at most `_TAIL_BYTES` from the end so a
    GB-sized transcript doesn't stall the hook."""
    lines = _read_tail_lines(path)
    if lines is None:
        return None
    return _find_latest_assistant_model(lines)
