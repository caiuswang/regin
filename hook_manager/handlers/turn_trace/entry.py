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
    _post_system_event_spans,
)

_TAIL_BYTES = 64 * 1024  # cap the read at 64 KiB — only need the last turn


def handle(payload: HookPayload) -> HookResponse | None:
    try:
        if payload.event == 'PostToolUse':
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


def _ingest_transcript_usage(
    trace_id: str,
    transcript_path: str,
    fallback_model: str | None,
    effort_level: str | None = None,
) -> None:
    """Read the full transcript and emit every derived span/event for
    rows the seen-uuid cache hasn't already accepted."""
    from lib.settings import settings  # type: ignore
    from lib.trace.transcript_usage import read_usage  # type: ignore

    capture_text = bool(getattr(settings, 'capture_assistant_response', True))
    max_text_bytes = int(getattr(settings, 'assistant_response_max_bytes', 50_000) or 0)
    usage = read_usage(
        transcript_path,
        max_text_bytes=max_text_bytes if capture_text and max_text_bytes > 0 else None,
    )
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
    _post_attachment_spans(trace_id, usage.attachments, seen)
    _post_system_event_spans(trace_id, usage.system_events, seen)
    _post_local_command_spans(trace_id, usage.local_commands, seen)


def _emit_span(payload: HookPayload) -> None:
    transcript_path = payload.raw.get('transcript_path')
    if not isinstance(transcript_path, str) or not transcript_path:
        return
    model = _latest_turn_model(transcript_path)
    if model:
        _post_turn_model_span(payload.session_id, model)
    _emit_session_title_if_changed(payload.session_id, transcript_path)
    _ingest_transcript_usage(payload.session_id, transcript_path, model,
                             effort_level=_payload_effort_level(payload))


def _emit_assistant_response_only(payload: HookPayload) -> None:
    """PostToolUse fast path: ingest per-turn data (assistant_response +
    tool_attribution + turn_usage) for newly-written turns only. Skips
    the global `turn` model span — that's only needed to catch a /model
    switch and still runs on UserPromptSubmit / Stop / SessionEnd.

    Throttled by the per-session seen-uuid cache so the typical case
    (no new turn since the last tool call) costs one transcript scan
    plus zero HTTP calls.
    """
    transcript_path = payload.raw.get('transcript_path')
    if not isinstance(transcript_path, str) or not transcript_path:
        return
    _ingest_transcript_usage(payload.session_id, transcript_path, None,
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
