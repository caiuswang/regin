"""Post spans for a transcript's non-turn rows: system events, attachments,
local commands and rewinds.

Idempotency comes from deterministic span IDs (`sys-<uuid[:13]>`,
`att-<uuid[:13]>`, `cmd-<uuid[:13]>`) so a replayed transcript only UPSERTs.
"""

from __future__ import annotations

from .cache import _mark_seen
from .prompt_anchor_spans import _post_queued_command_span, _truncate_utf8_with_marker
from .timestamps import _normalise_attachment_ts


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


# The recap's `content` is free prose (a paragraph or two). Cap it so a
# verbose recap doesn't bloat the span attributes blob.
_RECAP_CONTENT_MAX_BYTES = 8 * 1024


def _emit_away_summary_span(trace_id: str, ev) -> bool:
    """Emit a `harness.recap` span for a `system: away_summary` entry —
    the prose recap Claude Code writes when the session goes idle. The
    text lives in the entry's top-level `content` string."""
    from lib.hook_plugin import post_span  # type: ignore
    payload = ev.payload or {}
    raw = payload.get('content')
    content = raw if isinstance(raw, str) else ''
    content, truncated = _truncate_utf8_with_marker(content, _RECAP_CONTENT_MAX_BYTES)
    ts = _normalise_attachment_ts(ev.timestamp)
    attrs = {
        'subtype': 'away_summary',
        'turn_uuid': ev.turn_uuid,
        'content': content,
        'content_truncated': truncated,
    }
    return post_span(
        trace_id=trace_id,
        span_id=f'sys-{ev.uuid[:13]}',
        name='harness.recap',
        start_time=ts, end_time=ts, duration_ms=0,
        attributes=attrs,
    )


def _emit_model_refusal_span(trace_id: str, ev) -> bool:
    """Emit a `harness.model_refusal` span for a `system: model_refusal_fallback`
    entry — the CLI's record that it walked a response back after a refusal and
    silently retried on a fallback model. Observability only: no reparenting,
    no token/rewind accounting (a retracted message never appears as its own
    transcript entry, so there is nothing to suppress or double-count). The
    original/fallback models, refusal category, and retracted uuids ride the
    entry payload verbatim; surface them as flat attrs. Same `sys-<uuid[:13]>`
    id convention as the sibling stop_summary / recap spans."""
    from lib.hook_plugin import post_span  # type: ignore
    payload = ev.payload or {}
    retracted = (
        payload.get('retracted_message_uuids')
        or payload.get('retractedMessageUuids') or []
    )
    attrs = {
        'subtype': 'model_refusal_fallback',
        'turn_uuid': ev.turn_uuid,
        'original_model': payload.get('original_model') or payload.get('originalModel'),
        'fallback_model': payload.get('fallback_model') or payload.get('fallbackModel'),
        'api_refusal_category': (
            payload.get('api_refusal_category') or payload.get('apiRefusalCategory')
        ),
        'retracted_message_uuids': list(retracted) if isinstance(retracted, list) else [],
    }
    ts = _normalise_attachment_ts(ev.timestamp)
    return post_span(
        trace_id=trace_id,
        span_id=f'sys-{ev.uuid[:13]}',
        name='harness.model_refusal',
        start_time=ts, end_time=ts, duration_ms=0,
        attributes=attrs,
    )


_SYSTEM_EVENT_EMITTERS = {
    'stop_hook_summary': _emit_stop_summary_span,
    'away_summary': _emit_away_summary_span,
    'model_refusal_fallback': _emit_model_refusal_span,
}


def _post_system_event_spans(trace_id: str, events, seen: set[str]) -> None:
    """Emit a span per traced `system:` event: `hook.stop_summary` for
    `stop_hook_summary`, `harness.recap` for `away_summary`. The
    `turn_duration` siblings are folded into the matching
    `assistant_response` span's `duration_ms` and don't need their own
    span. Idempotent via `sys-<uuid[:13]>` span_id."""
    new_uuids: list[str] = []
    for ev in events:
        if ev.uuid in seen:
            continue
        emitter = _SYSTEM_EVENT_EMITTERS.get(ev.subtype)
        if emitter is None:
            # No span needed for this subtype — record the uuid so we
            # don't keep re-scanning it.
            new_uuids.append(ev.uuid)
            continue
        if emitter(trace_id, ev):
            new_uuids.append(ev.uuid)
    _mark_seen(trace_id, new_uuids)


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


_ATTACHMENT_HANDLERS = {
    'task_reminder': _post_task_reminder_span,
    'skill_listing': _post_skill_listing_span,
    'deferred_tools_delta': _post_tools_delta_span,
    # 'queued_command' is special-cased in _post_attachment_spans (it needs the
    # first-response timestamp to re-time the recovered prompt anchor).
}


def _first_ts_at_or_after(sorted_ts: list, threshold) -> str | None:
    """First timestamp in `sorted_ts` >= `threshold` (raw transcript strings,
    all offset-aware UTC so lexicographic == chronological), or None."""
    if not sorted_ts or not threshold:
        return None
    import bisect
    i = bisect.bisect_left(sorted_ts, threshold)
    return sorted_ts[i] if i < len(sorted_ts) else None


def _post_attachment_spans(trace_id: str, attachments, seen: set[str],
                           turn_timestamps: list | None = None) -> None:
    """Emit one `harness.*` span per unseen Claude Code attachment of
    interest. Idempotent via `att-<uuid[:13]>` (or
    `skill-init-<trace_id[:24]>` for the initial skill-listing row).

    Skill-listing's `isInitial=True` row is folded into a single span
    per session via a deterministic span_id; non-initial deltas get
    their own per-uuid span so the trace shows when new skills came
    on/off line mid-session.
    """
    turn_timestamps = turn_timestamps or []
    new_uuids: list[str] = []
    for att in attachments:
        if att.uuid in seen:
            continue
        ts = _normalise_attachment_ts(att.timestamp)
        if att.kind == 'queued_command':
            # Re-time the recovered prompt to its first response (first turn at
            # or after the attachment's raw timestamp — the attachment is
            # contiguous with its response in the transcript), not its
            # type-time. resp_ts may be absent on the first scan after dequeue
            # (response not flushed yet) — then it isn't marked seen, so a
            # later scan re-times it.
            resp_ts = _first_ts_at_or_after(turn_timestamps, att.timestamp)
            post_ok = _post_queued_command_span(
                trace_id, att, ts, resp_ts=resp_ts)
        else:
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


# ─────────────────────────────── /rewind ───────────────────────────────

# Cap the rolled-back-file list carried on a `rewind` marker. The list holds
# only paths + `<pathhash>@vN` refs (never content), so even a large rewind
# stays small; the cap is a backstop against a pathological branch.
_REWIND_MAX_FILES = 200


def _rewind_marker_attrs(fork) -> dict:
    """Attributes for one `rewind` boundary span. `orphan_keys` /
    `abandoned_prompt_keys` are `<uuid[:13]>` tails the serve-time projection
    (`_mark_rewound_away`) matches against span ids to flag + collapse the
    discarded branch. `rolled_back_files` carries refs only — content loads
    lazily via the `/spans/<id>/rewind` route."""
    return {
        'kind': 'rewind',
        'orphan_keys': sorted({u[:13] for u in fork.orphan_uuids}),
        'abandoned_prompt_keys': [u[:13] for u in fork.abandoned_prompt_uuids],
        'abandoned_prompt_count': len(fork.abandoned_prompt_uuids),
        'abandoned_span_count': len(fork.orphan_uuids),
        'rolled_back_files': list(fork.rolled_back_files[:_REWIND_MAX_FILES]),
        'rolled_back_count': len(fork.rolled_back_files),
        'fork_uuid': fork.fork_uuid,
        'live_child_uuid': fork.live_child_uuid,
    }


def _post_rewind_spans(trace_id: str, rewinds, seen: set[str]) -> None:
    """Emit one `rewind` boundary span per detected `/rewind` fork. The span
    is a conversation-level divider at the fork point; the projection adopts
    the discarded turns under it so the UI collapses the abandoned branch.

    Idempotent via `rewind-<orphan_root[:13]>`. The seen key is namespaced
    (`rewind:<orphan_root>`) so it does NOT collide with the prompt-anchor
    poster, which already marks the same orphan-root uuid seen as a `prompt`
    — without the namespace a re-ingest of an already-scanned session would
    skip the marker. A growing resumable scan only ever firms up a fork (the
    abandoned branch never leaves the file), so re-emission is a harmless
    UPSERT."""
    from lib.hook_plugin import post_span  # type: ignore

    new_keys: list[str] = []
    for fork in rewinds:
        seen_key = f'rewind:{fork.orphan_root}'
        if seen_key in seen:
            continue
        ts = _normalise_attachment_ts(fork.fork_timestamp)
        if post_span(
            trace_id=trace_id,
            span_id=fork.span_id,
            name='rewind',
            start_time=ts, end_time=ts, duration_ms=0,
            attributes=_rewind_marker_attrs(fork),
        ):
            new_keys.append(seen_key)
    _mark_seen(trace_id, new_keys)
