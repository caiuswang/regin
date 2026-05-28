"""Handler: UserPromptSubmit → lightweight prompt tracing + plan-decision detection.

Replaces the external user_prompt_trace_hook.py without touching the legacy
trace-ingest API. When a plan is active (inferred from the presence of a
recent `~/.claude/plans/*.md` file modified in the last hour), this handler
classifies the prompt as approved/rejected/neither based on keyword match
so downstream dashboards can attribute outcomes.
"""

from __future__ import annotations

import json
import os
import re
import time

from lib.providers import get_active_provider
from lib.tokens.token_estimator import estimate_image_tokens

from ..core import HookPayload, HookResponse

_APPROVE_WORDS = ('approve', 'proceed', 'yes', 'looks good', 'looks great',
                  'go ahead', 'continue', 'lgtm', 'ship it')
_REJECT_WORDS = ('reject', 'cancel', 'abort', 'discard', 'no thanks', 'stop')

# Maps `[Image #N]` to a file in `~/.claude/image-cache/<session>/N.<ext>`.
# Claude Code numbers images session-cumulatively (so N can be >1 even for
# a prompt with a single image), and writes the cache file for every
# submission regardless of whether the transcript also inlines base64.
_IMAGE_MARKER_RE = re.compile(r'\[Image #(\d+)\]')
_IMAGE_EXT_TO_MEDIA = {
    '.png': 'image/png',
    '.jpg': 'image/jpeg',
    '.jpeg': 'image/jpeg',
    '.gif': 'image/gif',
    '.webp': 'image/webp',
}


def _detect_decision(prompt: str) -> str | None:
    lower = prompt.lower()
    for kw in _APPROVE_WORDS:
        if kw in lower:
            return 'approved'
    for kw in _REJECT_WORDS:
        if kw in lower:
            return 'rejected'
    return None


def _plan_is_active(provider=None, fresh_seconds: int = 3600) -> bool:
    """True if any plan file has been modified in the last `fresh_seconds`.

    `provider` defaults to the globally active provider so tests can
    monkey-patch `get_active_provider` or pass a provider directly.
    """
    if provider is None:
        provider = get_active_provider()
    plans_dir = str(provider.plans_dir())
    if not os.path.isdir(plans_dir):
        return False
    now = time.time()
    try:
        for fname in os.listdir(plans_dir):
            if not fname.endswith('.md'):
                continue
            try:
                if now - os.stat(os.path.join(plans_dir, fname)).st_mtime < fresh_seconds:
                    return True
            except OSError:
                continue
    except OSError:
        return False
    return False


_TASK_NOTIFICATION_RE = re.compile(r'<task-notification>(.*?)</task-notification>', re.DOTALL)
_TASK_FIELD_RE = {
    'task_id': re.compile(r'<task-id>([^<]*)</task-id>'),
    'tool_use_id': re.compile(r'<tool-use-id>([^<]*)</tool-use-id>'),
    'output_file': re.compile(r'<output-file>([^<]*)</output-file>'),
    'status': re.compile(r'<status>([^<]*)</status>'),
    'summary': re.compile(r'<summary>([^<]*)</summary>'),
}


def _parse_task_notification(text: str) -> dict | None:
    """Return parsed fields from a task-notification block, or None if not one.

    Claude Code injects background-task completions as synthetic user prompts
    wrapping the payload in `<task-notification>` XML tags. We want these to
    surface as their own span type rather than masquerade as user prompts.
    """
    if '<task-notification>' not in text:
        return None
    body_match = _TASK_NOTIFICATION_RE.search(text)
    if not body_match:
        return None
    body = body_match.group(1)
    fields: dict[str, str] = {}
    for key, regex in _TASK_FIELD_RE.items():
        m = regex.search(body)
        if m:
            fields[key] = m.group(1).strip()
    return fields


def handle(payload: HookPayload) -> HookResponse | None:
    text = payload.prompt
    if not text:
        return None

    task_fields = _parse_task_notification(text)

    # Trace emission (best-effort): record a `prompt` span (or
    # `task.notification` span for background-task completions injected
    # by Claude Code) so the session trace view has something to graft
    # subsequent tool calls under. Without this span, later spans
    # (skill.read, tool.*) orphan at the session root.
    try:
        if task_fields is not None:
            _emit_task_notification_span(payload, text, task_fields)
        else:
            _emit_span(payload, text)
    except Exception:
        pass

    # Background-task notifications are system-injected, not user prompts —
    # skip plan-decision keyword detection so a "completed" status doesn't
    # get misread as approval.
    if task_fields is not None:
        return HookResponse(suppress_output=True)

    # Only emit additional_context when we have an actionable signal.
    # Length, slash-command detection, etc. are all things the model can
    # derive from the prompt it already received.
    if _plan_is_active(provider=payload.resolved_provider):
        decision = _detect_decision(text)
        if decision:
            return HookResponse(
                suppress_output=True,
                additional_context=f'plan_decision={decision}',
            )
    return HookResponse(suppress_output=True)


# Image base64 strings can be MB-sized — bump the tail read so a single
# prompt with several screenshots still fits in one chunk. Sized to
# accommodate ~3× 5 MB images + slack.
_TRANSCRIPT_TAIL_BYTES = 24 * 1024 * 1024


def _latest_user_prompt(transcript_path: str) -> tuple[str | None, list[dict]]:
    """Return `(uuid, image_parts)` for the most recent real user entry.

    Reads the transcript tail once and pulls both the entry uuid (used
    to derive the prompt span_id, same scheme `turn_trace` uses) and
    its `message.content` image parts so we don't double-read a
    potentially MB-sized file.

    `image_parts` is a list of `{idx, media_type, data_b64}` dicts in
    submission order; `idx` is 1-indexed and matches the `[Image #N]`
    markers Claude Code inlines into the text part. Tool-result user
    entries (synthetic messages carrying tool output) are skipped.
    """
    if not isinstance(transcript_path, str) or not transcript_path:
        return None, []
    if not os.path.isfile(transcript_path):
        return None, []
    try:
        size = os.path.getsize(transcript_path)
    except OSError:
        return None, []
    try:
        with open(transcript_path, 'rb') as f:
            if size > _TRANSCRIPT_TAIL_BYTES:
                f.seek(-_TRANSCRIPT_TAIL_BYTES, os.SEEK_END)
            chunk = f.read()
    except OSError:
        return None, []
    text = chunk.decode('utf-8', errors='replace')
    lines = text.split('\n')
    if size > _TRANSCRIPT_TAIL_BYTES and lines:
        lines = lines[1:]  # drop partial first line after a mid-line seek
    last_uuid: str | None = None
    last_images: list[dict] = []
    for raw in lines:
        raw = raw.strip()
        if not raw:
            continue
        try:
            entry = json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            continue
        if entry.get('type') != 'user':
            continue
        msg = entry.get('message') or {}
        content = msg.get('content')
        if isinstance(content, list) and content and isinstance(content[0], dict):
            if content[0].get('type') == 'tool_result':
                continue
        u = entry.get('uuid')
        if not isinstance(u, str) or not u:
            continue
        last_uuid = u
        last_images = _extract_image_parts(content)
    return last_uuid, last_images


def _extract_image_parts(content) -> list[dict]:
    """Pull base64-image content parts out of a user message.

    Returns an ordered list of `{idx, media_type, data_b64}` dicts;
    `idx` is the 1-based position among image parts only (skipping text
    parts), which matches the `[Image #N]` marker numbering.
    """
    out: list[dict] = []
    if not isinstance(content, list):
        return out
    for part in content:
        if not isinstance(part, dict) or part.get('type') != 'image':
            continue
        source = part.get('source') or {}
        if source.get('type') != 'base64':
            continue
        media_type = source.get('media_type')
        data_b64 = source.get('data')
        if not isinstance(media_type, str) or not isinstance(data_b64, str):
            continue
        if not data_b64:
            continue
        out.append({
            'idx': len(out) + 1,
            'media_type': media_type,
            'data_b64': data_b64,
        })
    return out


def _latest_user_prompt_uuid(transcript_path: str) -> str | None:
    """Back-compat shim — see `_latest_user_prompt`."""
    uuid, _ = _latest_user_prompt(transcript_path)
    return uuid


def _emit_task_notification_span(payload: HookPayload, text: str, fields: dict) -> None:
    """Emit a `task.notification` span for a background-task completion.

    span_id is derived from the task_id so transcript replays upsert the
    same row rather than duplicating. Falls through to a random uuid if
    the task_id is missing (best-effort idempotency).
    """
    from lib.hook_plugin import post_span  # type: ignore
    task_id = fields.get('task_id') or ''
    span_id = f'task-{task_id[:13]}' if task_id else None
    attributes = {
        'text': text,
        'task_id': fields.get('task_id'),
        'tool_use_id': fields.get('tool_use_id'),
        'output_file': fields.get('output_file'),
        'status': fields.get('status'),
        'summary': fields.get('summary'),
    }
    post_span(
        trace_id=payload.session_id,
        name='task.notification',
        span_id=span_id,
        attributes=attributes,
    )


def _emit_span(payload: HookPayload, text: str) -> None:
    from lib.hook_plugin import post_span, post_event  # type: ignore
    from lib.settings import settings  # type: ignore

    # Deterministic span_id derived from the transcript's user entry
    # uuid. Falls through to post_span's random uuid if the transcript
    # is unreadable — the prompt span still gets emitted, it just
    # won't be an attachment point for assistant_response children.
    transcript_path = payload.raw.get('transcript_path')
    span_id = None
    inline_images: list[dict] = []
    if isinstance(transcript_path, str):
        prompt_uuid, inline_images = _latest_user_prompt(transcript_path)
        if prompt_uuid:
            span_id = f'prompt-{prompt_uuid[:13]}'

    # Resolve images by `[Image #N]` markers from the (untruncated) prompt
    # text first — Claude Code stores user-submitted images at
    # `~/.claude/image-cache/<session>/<N>.<ext>` regardless of whether
    # the transcript inlines base64. This works for both the new
    # path-reference format and the legacy inline-base64 format. Falls
    # back to inline base64 parts (older sessions) when no cache file
    # exists for a marker.
    images = _resolve_prompt_images(payload.session_id, text, inline_images)

    # Filter by configured caps before posting. Cap compares against
    # decoded byte size.
    capture_images = bool(getattr(settings, 'capture_prompt_images', True))
    max_count = int(getattr(settings, 'prompt_images_max_count', 10) or 0)
    max_bytes = int(getattr(settings, 'prompt_image_max_bytes', 5_000_000) or 0)
    kept_images: list[dict] = []
    if capture_images and span_id:
        for img in images:
            if max_count and len(kept_images) >= max_count:
                break
            est_bytes = (len(img['data_b64']) * 3) // 4
            if max_bytes and est_bytes > max_bytes:
                continue
            kept_images.append(img)

    attributes = {
        'text': text,
        'chars': len(text),
        'slash_command': text.split()[0] if text.startswith('/') else None,
    }
    if images:
        attributes['image_indices'] = [img['idx'] for img in images]
        if len(kept_images) != len(images):
            attributes['image_indices_persisted'] = [img['idx'] for img in kept_images]
        # Local estimate of the per-image Anthropic cost. The API rolls
        # these into `usage.input_tokens` (or `cache_creation_input_tokens`
        # on the cache-miss turn), so they're not separately surfaced
        # anywhere else. Estimated via (w × h) / 750 capped at 1600 —
        # see `lib/tokens/token_estimator.py`.
        image_tokens_total = 0
        for img in images:
            image_tokens_total += estimate_image_tokens({
                'type': 'base64',
                'media_type': img.get('media_type'),
                'data': img.get('data_b64'),
            })
        if image_tokens_total:
            attributes['image_tokens_estimate'] = image_tokens_total
    post_span(
        trace_id=payload.session_id,
        name='prompt',
        span_id=span_id,
        attributes=attributes,
    )

    if kept_images and span_id:
        post_event('prompt_images', [
            {
                'trace_id': payload.session_id,
                'prompt_span_id': span_id,
                'idx': img['idx'],
                'media_type': img['media_type'],
                'data_b64': img['data_b64'],
            }
            for img in kept_images
        ])


def _resolve_prompt_images(
    session_id: str,
    prompt_text: str,
    inline_images: list[dict],
) -> list[dict]:
    """Return ordered list of `{idx, media_type, data_b64}` for the prompt.

    Strategy:
      1. Parse `[Image #N]` markers from the prompt text (the source of
         truth on which images the user attached — the JSONL may or may
         not inline base64 depending on Claude Code version).
      2. For each unique N, look up `~/.claude/image-cache/<session>/N.<ext>`.
      3. If a cache file is missing, fall back to the inline base64 part
         at position N (1-indexed) when present.

    The returned `idx` is the N from the marker, not a position counter —
    `(trace_id, span_id, idx)` is the PK in `prompt_images` so duplicate
    Ns are deduped server-side.
    """
    import base64

    indices: list[int] = []
    seen: set[int] = set()
    for m in _IMAGE_MARKER_RE.finditer(prompt_text or ''):
        n = int(m.group(1))
        if n < 1 or n in seen:
            continue
        seen.add(n)
        indices.append(n)

    if not indices:
        # Legacy fallback: prompts that didn't include `[Image #N]` markers
        # but did inline image parts (rare). Number them 1..N by order.
        return list(inline_images)

    cache_dir = os.path.expanduser(f'~/.claude/image-cache/{session_id}')
    out: list[dict] = []
    inline_by_idx = {img['idx']: img for img in inline_images}
    for n in indices:
        loaded = _load_cache_image(cache_dir, n)
        if loaded is not None:
            out.append({'idx': n, **loaded})
            continue
        # Fall back to an inline part at the same idx if present.
        inline = inline_by_idx.get(n)
        if inline is not None:
            out.append(inline)
    return out


def _load_cache_image(cache_dir: str, n: int) -> dict | None:
    """Return `{media_type, data_b64}` for `<cache_dir>/<n>.<ext>` or None."""
    import base64
    for ext, media_type in _IMAGE_EXT_TO_MEDIA.items():
        path = os.path.join(cache_dir, f'{n}{ext}')
        if not os.path.isfile(path):
            continue
        try:
            with open(path, 'rb') as f:
                raw = f.read()
        except OSError:
            return None
        return {
            'media_type': media_type,
            'data_b64': base64.b64encode(raw).decode('ascii'),
        }
    return None
