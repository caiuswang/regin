"""Post spans + turn_usage events for one transcript's worth of data.

The transcript parser (`lib.trace.transcript_usage.read_usage`) returns
four lists of rows:

  * `turns` — assistant turns with usage + per-call attribution
  * `attachments` — task_reminder / skill_listing / deferred_tools_delta
  * `system_events` — stop_hook_summary / turn_duration / away_summary
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
    build_interrupt_attrs,
    build_recorded_deny_attrs,
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


_SYSTEM_EVENT_EMITTERS = {
    'stop_hook_summary': _emit_stop_summary_span,
    'away_summary': _emit_away_summary_span,
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


def _post_queued_command_span(trace_id: str, att, ts, resp_ts=None) -> bool:
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
    replay UPSERTs the same row.

    **Timing:** the attachment's own timestamp (`ts`) is when the user
    *typed* it — mid the interrupted turn — so anchoring there sorts the
    prompt into the middle of that prior turn. We anchor at `resp_ts`, the
    first response it triggered (the attachment uuid is that response's
    parentUuid), so it sorts with its own turn. `resp_ts` is None on the
    first scan after dequeue (response not flushed yet): emit at `ts` as a
    fallback but DON'T mark seen, so a later scan re-times it (UPSERT) once
    the response lands. Only prompt-mode queues become prompt spans; a queued
    slash command (different `command_mode`) is not a model prompt — mark it
    seen without a span.

    A queued prompt with pasted images carries `prompt` as a list of
    content blocks (text + base64 image), not a bare string — same dual
    shape as `message.content` on a real user entry. Recover both: the
    text anchors the span, the image parts land in `prompt_images` like
    the UserPromptSubmit anchor path (_emit_one_prompt_anchor).
    """
    from lib.hook_plugin import post_span  # type: ignore
    from lib.trace.transcript_usage import queued_prompt_content
    text, inline_parts = queued_prompt_content(att.payload or {})
    if not text and not inline_parts:
        return True
    span_id = f'prompt-{att.uuid[:13]}'
    images, kept = _resolve_capped_anchor_images(
        trace_id, text or '', inline_parts)
    attrs = _anchor_attrs(text or '', images, kept)
    attrs['queued'] = True
    # `resp_ts` is a raw transcript timestamp (offset-aware UTC); normalise it
    # to local-naive like the response spans (and `ts`) so it sorts correctly
    # against its sibling spans instead of as a `15:..Z` < `19:..` string.
    start = _normalise_turn_ts(resp_ts) if resp_ts else ts
    ok = post_span(
        trace_id=trace_id,
        span_id=span_id,
        name='prompt',
        start_time=start, end_time=start, duration_ms=0,
        attributes=attrs,
    )
    if ok and kept:
        _post_prompt_images(trace_id, span_id, kept)
    # Cache only once anchored at the real response time; otherwise re-time
    # next scan when the response has landed.
    return bool(ok) and resp_ts is not None


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


# Prompt-anchor text can be a full slash-command expansion (a skill body
# can be several KiB). Cap so the turn-anchor span attrs stay lean — the
# untruncated prompt still lives on the live UserPromptSubmit span.
_PROMPT_ANCHOR_TEXT_MAX_BYTES = 8 * 1024


def _post_prompt_anchor_spans(
    trace_id: str,
    turns,
    prompt_texts: dict[str, str],
    prompt_timestamps: dict[str, str],
    prompt_image_parts: dict[str, list],
    seen: set[str],
) -> None:
    """Emit the turn-anchor `prompt-<prompt_uuid[:13]>` span (and its
    `prompt_images`) for each turn.

    `prompt` spans are the only turn boundaries the projection paginates
    on (`_TURN_ANCHOR_NAMES`), and this is now their **sole** authoritative
    producer. The live UserPromptSubmit hook deliberately no longer writes
    one: at fire time the current prompt isn't flushed, so a span_id keyed
    off the transcript would land on a *prior* turn's entry and clobber its
    anchor (text + a now() timestamp) on upsert. Emitting here from
    `prompt_uuid` (the entry the assistant actually responded to) gives the
    correct id + text + prompt-time stamp; a stray `prompt` placeholder that no
    turn backs is dropped by the serve-time merge once a newer prompt lands
    (lib/trace/merge.py:_drop_stale_blockers).

    Images attach to the same `prompt-<prompt_uuid>` id (so they land on the
    right prompt), resolved from Claude Code's per-session cache while it's
    still present, falling back to the transcript's inline base64 parts.

    Idempotent via the span_id; throttled by the seen-cache keyed on the
    prompt entry uuid (a prompt's text never changes once submitted).
    """
    new_uuids: list[str] = []
    posted: set[str] = set()
    for turn in turns:
        pu = turn.prompt_uuid
        if not pu or pu in seen or pu in posted:
            continue
        text = prompt_texts.get(pu)
        if not text:
            continue
        posted.add(pu)
        if _emit_one_prompt_anchor(
            trace_id, pu, text,
            prompt_timestamps.get(pu), prompt_image_parts.get(pu),
        ):
            new_uuids.append(pu)
    _mark_seen(trace_id, new_uuids)


def _emit_one_prompt_anchor(
    trace_id: str,
    prompt_uuid: str,
    text: str,
    ts_raw: str | None,
    inline_parts: list | None,
) -> bool:
    """Post one `prompt-<uuid>` anchor span plus its `prompt_images`.
    Returns True iff the span persisted (so the caller caches the uuid)."""
    from lib.hook_plugin import post_span  # type: ignore

    span_id = f'prompt-{prompt_uuid[:13]}'
    images, kept = _resolve_capped_anchor_images(trace_id, text, inline_parts)
    attrs = _anchor_attrs(text, images, kept)
    ts = _normalise_attachment_ts(ts_raw)
    if not post_span(
        trace_id=trace_id, span_id=span_id, name='prompt',
        start_time=ts, end_time=ts, attributes=attrs,
    ):
        return False
    if kept:
        _post_prompt_images(trace_id, span_id, kept)
    return True


def _post_prompt_images(trace_id: str, span_id: str, kept: list) -> None:
    """Persist a prompt's capped images against its `prompt-<uuid>` span."""
    from lib.hook_plugin import post_event  # type: ignore

    post_event('prompt_images', [
        {
            'trace_id': trace_id,
            'prompt_span_id': span_id,
            'idx': img['idx'],
            'media_type': img['media_type'],
            'data_b64': img['data_b64'],
        }
        for img in kept
    ])


def _anchor_attrs(text: str, images: list, kept: list) -> dict:
    """Build the `prompt` span attributes, including image metadata."""
    capped, truncated = _truncate_utf8_with_marker(
        text, _PROMPT_ANCHOR_TEXT_MAX_BYTES,
    )
    attrs: dict = {
        'text': capped,
        'chars': len(text),
        'slash_command': text.split()[0] if text.startswith('/') else None,
    }
    if truncated:
        attrs['text_truncated'] = True
    if images:
        attrs['image_indices'] = [img['idx'] for img in images]
        if len(kept) != len(images):
            attrs['image_indices_persisted'] = [img['idx'] for img in kept]
        total = _image_tokens_total(images)
        if total:
            attrs['image_tokens_estimate'] = total
    return attrs


def _resolve_capped_anchor_images(
    trace_id: str, text: str, inline_parts: list | None,
) -> tuple[list, list]:
    """Resolve the prompt's images and apply the configured persistence
    caps. Returns `(all_resolved, kept_after_caps)` — `all_resolved` feeds
    `image_indices` (so the UI knows images existed even when not stored);
    `kept` is what actually lands in `prompt_images`."""
    from lib.settings import settings  # type: ignore
    from lib.trace.prompt_images_resolve import resolve_prompt_images

    images = resolve_prompt_images(trace_id, text, inline_parts or [])
    if not images or not bool(getattr(settings, 'capture_prompt_images', True)):
        return images, []
    max_count = int(getattr(settings, 'prompt_images_max_count', 10) or 0)
    max_bytes = int(getattr(settings, 'prompt_image_max_bytes', 5_000_000) or 0)
    return images, _cap_images(images, max_count, max_bytes)


def _cap_images(images: list, max_count: int, max_bytes: int) -> list:
    """Filter resolved images by the count cap and per-image decoded-byte
    cap. `max_count`/`max_bytes` of 0 disable that cap."""
    kept: list = []
    for img in images:
        if max_count and len(kept) >= max_count:
            break
        est_bytes = (len(img['data_b64']) * 3) // 4
        if max_bytes and est_bytes > max_bytes:
            continue
        kept.append(img)
    return kept


def _image_tokens_total(images: list) -> int:
    """Local Anthropic image-token estimate summed across the prompt's
    images — see `lib/tokens/token_estimator.py`. The API rolls these into
    `usage.input_tokens`, so they're not separately surfaced elsewhere."""
    from lib.tokens.token_estimator import estimate_image_tokens  # type: ignore

    total = 0
    for img in images:
        total += estimate_image_tokens({
            'type': 'base64',
            'media_type': img.get('media_type'),
            'data': img.get('data_b64'),
        })
    return total


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
    PostToolUse attributes (diff/stdout) are preserved."""
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
    'assistant_text' bucket."""
    from lib.tokens.token_estimator import estimate_text_tokens  # type: ignore
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
    if turn.thinking_text:
        attrs['thinking_text'] = turn.thinking_text
        attrs['thinking_truncated'] = turn.thinking_text_truncated
    if is_primary:
        _add_tool_summary(turn, attrs)
        _add_durations(turn, attrs)
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
