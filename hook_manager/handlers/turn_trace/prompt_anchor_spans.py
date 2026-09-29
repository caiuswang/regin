"""Post prompt-shaped spans: the per-turn prompt anchors (with their images)
and queued-command prompts."""

from __future__ import annotations

from .cache import _mark_seen
from .timestamps import _normalise_attachment_ts


# ───────────────────────────── attachments ─────────────────────────────


def _truncate_utf8_with_marker(text: str, max_bytes: int) -> tuple[str, bool]:
    encoded = text.encode('utf-8')
    if len(encoded) <= max_bytes:
        return text, False
    head = encoded[:max_bytes].decode('utf-8', errors='ignore')
    return head + '\n…[truncated]', True


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
    attrs['entry_uuid'] = att.uuid
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
    prompt_ids: dict[str, str] | None = None,
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
    prompt_ids = prompt_ids or {}
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
            prompt_ids.get(pu),
        ):
            new_uuids.append(pu)
    _mark_seen(trace_id, new_uuids)


def _emit_one_prompt_anchor(
    trace_id: str,
    prompt_uuid: str,
    text: str,
    ts_raw: str | None,
    inline_parts: list | None,
    prompt_id: str | None = None,
) -> bool:
    """Post one `prompt-<uuid>` anchor span plus its `prompt_images`.
    Returns True iff the span persisted (so the caller caches the uuid)."""
    from lib.hook_plugin import post_span  # type: ignore

    span_id = f'prompt-{prompt_uuid[:13]}'
    images, kept = _resolve_capped_anchor_images(trace_id, text, inline_parts)
    attrs = _anchor_attrs(text, images, kept, prompt_id)
    # The identity an SDK-launched session's other writer stamps from the
    # delivery echo; sharing it here is what lets the two anchors collapse.
    attrs['entry_uuid'] = prompt_uuid
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


def _anchor_attrs(
    text: str, images: list, kept: list, prompt_id: str | None = None,
) -> dict:
    """Build the `prompt` span attributes, including image metadata.

    `prompt_id` (Claude Code's `promptId` for this entry) is stamped when
    present so the serve-time ladder can value-join tool spans carrying the
    same value as `source_prompt_id` onto this anchor. Anchors emitted before
    this change (or without a captured id, e.g. queued commands) carry no
    `prompt_id` and fall through to the existing ladder unchanged."""
    capped, truncated = _truncate_utf8_with_marker(
        text, _PROMPT_ANCHOR_TEXT_MAX_BYTES,
    )
    attrs: dict = {
        'text': capped,
        'chars': len(text),
        'slash_command': text.split()[0] if text.startswith('/') else None,
    }
    if prompt_id:
        attrs['prompt_id'] = prompt_id
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


def _normalise_turn_ts(ts: str) -> str:
    """Transcript timestamps are offset-aware UTC; the server's
    _widen_envelopes mixes them with offset-naive timestamps, so
    convert to local-naive before posting."""
    if not ts.endswith('Z'):
        return ts
    from datetime import datetime
    dt = datetime.fromisoformat(ts.replace('Z', '+00:00'))
    return dt.astimezone().replace(tzinfo=None).isoformat()
