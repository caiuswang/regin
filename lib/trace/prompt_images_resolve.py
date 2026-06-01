"""Resolve the images attached to a user prompt into base64 payloads.

Shared between the live `UserPromptSubmit` capture path and the
transcript-replay path in `turn_trace`. A prompt references its images
with `[Image #N]` markers in the text; the bytes live either in
Claude Code's per-session cache (`~/.claude/image-cache/<session>/N.<ext>`,
written for every submission regardless of transcript format) or, on
older sessions, inline as base64 `image` content parts in the transcript.

The cache is the source of truth while a session is *live* (Claude Code
cleans the directory at session end), so `turn_trace` — which runs on
every PostToolUse a few seconds after submit — can still read it. The
inline parts are the durable fallback for replay/repair after the cache
is gone.
"""

from __future__ import annotations

import base64
import os
import re

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


def extract_image_parts(content) -> list[dict]:
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


def resolve_prompt_images(
    session_id: str,
    prompt_text: str,
    inline_images: list[dict] | None = None,
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
    inline_images = inline_images or []

    indices = _parse_marker_indices(prompt_text)
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


def _parse_marker_indices(prompt_text: str) -> list[int]:
    """Ordered, de-duplicated `[Image #N]` indices in the prompt text."""
    indices: list[int] = []
    seen: set[int] = set()
    for m in _IMAGE_MARKER_RE.finditer(prompt_text or ''):
        n = int(m.group(1))
        if n < 1 or n in seen:
            continue
        seen.add(n)
        indices.append(n)
    return indices


def _load_cache_image(cache_dir: str, n: int) -> dict | None:
    """Return `{media_type, data_b64}` for `<cache_dir>/<n>.<ext>` or None."""
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
