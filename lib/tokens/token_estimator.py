"""Estimate token cost of individual tool calls.

The Anthropic API returns one `usage` block per assistant turn, never
per tool. To attribute a turn's tokens to specific tool calls we
tokenize each `tool_use` block (output side) and each `tool_result`
content (input side) ourselves.

Text uses tiktoken's `cl100k_base` — not Claude's exact tokenizer, but
within a few percent for prose and JSON, and the same encoder family
most tooling assumes. Image content uses Anthropic's published
approximation: tokens ≈ (width × height) / 750, capped at 1600 per
image. We read PNG/JPEG dimensions from the first few bytes of the
base64-decoded data — no Pillow / external image lib needed.

The encoder is the single switch-point: swap in a real Claude
tokenizer here later and every caller picks it up.
"""

from __future__ import annotations

import base64
import struct
from functools import lru_cache
from typing import Any

import tiktoken


_IMAGE_TOKEN_CAP = 1600
_IMAGE_PIXEL_DIVISOR = 750
_TOOL_REFERENCE_TOKENS = 10


@lru_cache(maxsize=1)
def _encoder() -> tiktoken.Encoding:
    return tiktoken.get_encoding('cl100k_base')


def estimate_text_tokens(text: Any) -> int:
    if not isinstance(text, str) or not text:
        return 0
    try:
        return len(_encoder().encode(text, disallowed_special=()))
    except Exception:
        return max(1, len(text) // 4)


def _png_dimensions(head: bytes) -> tuple[int, int] | None:
    if len(head) < 24 or head[:8] != b'\x89PNG\r\n\x1a\n':
        return None
    if head[12:16] != b'IHDR':
        return None
    width, height = struct.unpack('>II', head[16:24])
    if width <= 0 or height <= 0:
        return None
    return width, height


def _jpeg_dimensions(head: bytes) -> tuple[int, int] | None:
    if len(head) < 4 or head[:2] != b'\xff\xd8':
        return None
    i = 2
    n = len(head)
    while i + 8 < n:
        if head[i] != 0xFF:
            return None
        while i + 1 < n and head[i + 1] == 0xFF:
            i += 1
        if i + 1 >= n:
            return None
        marker = head[i + 1]
        i += 2
        if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
            if i + 7 >= n:
                return None
            height = (head[i + 3] << 8) | head[i + 4]
            width = (head[i + 5] << 8) | head[i + 6]
            if width <= 0 or height <= 0:
                return None
            return width, height
        if marker in (0xD8, 0xD9):
            return None
        if i + 1 >= n:
            return None
        seg_len = (head[i] << 8) | head[i + 1]
        if seg_len < 2:
            return None
        i += seg_len
    return None


def _decode_b64_head(b64data: str, max_chars: int = 2048) -> bytes:
    head = b64data[:max_chars]
    # Strip whitespace introduced by line-wrapping
    head = ''.join(head.split())
    pad = (-len(head)) % 4
    if pad:
        head = head + '=' * pad
    try:
        return base64.b64decode(head, validate=False)
    except Exception:
        return b''


def _dimensions(b64data: str, media_type: str) -> tuple[int, int] | None:
    head = _decode_b64_head(b64data)
    if not head:
        return None
    mt = (media_type or '').lower()
    if 'png' in mt:
        return _png_dimensions(head)
    if 'jpeg' in mt or 'jpg' in mt:
        return _jpeg_dimensions(head)
    return _png_dimensions(head) or _jpeg_dimensions(head)


def estimate_image_tokens_from_dims(width: int, height: int) -> int:
    """Anthropic's published image cost from known pixel dimensions:
    `(w × h) / 750`, capped at ~1600. Use this when the dimensions are
    authoritative (e.g. Claude Code's `tool_response.file.dimensions`)
    rather than header-decoded from base64 — it's the billed size after
    any server-side downsample, and needs no base64 to parse."""
    if width <= 0 or height <= 0:
        return _IMAGE_TOKEN_CAP
    return min(_IMAGE_TOKEN_CAP, max(1, (width * height) // _IMAGE_PIXEL_DIVISOR))


def estimate_image_tokens(source: Any) -> int:
    """Approximate the Anthropic-billed cost of one image content block.

    `source` is an image block's `.source` dict, e.g.
    `{"type": "base64", "media_type": "image/png", "data": "..."}`.

    Anthropic's published formula is `(w × h) / 750`, capped at ~1600
    per image. When dimensions are unreadable we return the cap rather
    than 0 — an image is never free, and the user's whole reason for
    this feature was that screenshots silently look cheap.
    """
    if not isinstance(source, dict):
        return _IMAGE_TOKEN_CAP
    if source.get('type') != 'base64':
        return _IMAGE_TOKEN_CAP
    data = source.get('data')
    if not isinstance(data, str) or not data:
        return _IMAGE_TOKEN_CAP
    dims = _dimensions(data, source.get('media_type', ''))
    if dims is None:
        return _IMAGE_TOKEN_CAP
    w, h = dims
    tokens = (w * h) // _IMAGE_PIXEL_DIVISOR
    return min(_IMAGE_TOKEN_CAP, max(1, tokens))


def estimate_block_tokens(block: Any) -> int:
    if isinstance(block, str):
        return estimate_text_tokens(block)
    if not isinstance(block, dict):
        return 0
    btype = block.get('type')
    if btype == 'text':
        return estimate_text_tokens(block.get('text'))
    if btype == 'image':
        return estimate_image_tokens(block.get('source'))
    if btype == 'tool_reference':
        return _TOOL_REFERENCE_TOKENS
    return 0


def estimate_content_tokens(content: Any) -> int:
    """Sum tokens for a tool_result.content payload (str or list of blocks)."""
    if content is None:
        return 0
    if isinstance(content, str):
        return estimate_text_tokens(content)
    if isinstance(content, list):
        return sum(estimate_block_tokens(b) for b in content)
    return 0


def estimate_image_only_tokens(content: Any) -> int:
    """Subtotal of just the image blocks within a tool_result.content payload."""
    if not isinstance(content, list):
        return 0
    return sum(
        estimate_image_tokens(b.get('source'))
        for b in content
        if isinstance(b, dict) and b.get('type') == 'image'
    )


def estimate_tool_use_tokens(name: Any, tool_input: Any) -> int:
    """Cost of emitting a tool_use block: the tool name plus JSON-encoded input."""
    import json
    name_str = name if isinstance(name, str) else ''
    try:
        input_str = json.dumps(tool_input, separators=(',', ':'), ensure_ascii=False)
    except (TypeError, ValueError):
        input_str = str(tool_input) if tool_input is not None else ''
    return estimate_text_tokens(name_str + input_str)
