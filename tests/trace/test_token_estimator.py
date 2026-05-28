"""Unit tests for lib.tokens.token_estimator."""

from __future__ import annotations

import base64
import struct

from lib.tokens.token_estimator import (
    estimate_block_tokens,
    estimate_content_tokens,
    estimate_image_only_tokens,
    estimate_image_tokens,
    estimate_text_tokens,
    estimate_tool_use_tokens,
)


def _png_header(width: int, height: int) -> bytes:
    sig = b'\x89PNG\r\n\x1a\n'
    ihdr = b'\x00\x00\x00\rIHDR' + struct.pack('>II', width, height) + b'\x08\x02\x00\x00\x00'
    return sig + ihdr


def _jpeg_header(width: int, height: int) -> bytes:
    soi = b'\xff\xd8'
    app0 = b'\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00'
    sof0 = b'\xff\xc0\x00\x11\x08' + struct.pack('>HH', height, width) + b'\x03\x01\x22\x00\x02\x11\x01\x03\x11\x01'
    return soi + app0 + sof0


def _b64_image_source(raw: bytes, media: str) -> dict:
    return {'type': 'base64', 'media_type': media,
            'data': base64.b64encode(raw).decode('ascii')}


def test_text_tokens_handles_empty_and_none():
    assert estimate_text_tokens(None) == 0
    assert estimate_text_tokens('') == 0
    assert estimate_text_tokens(123) == 0


def test_text_tokens_for_real_text_is_positive():
    s = 'hello world, this is a tokenization test'
    n = estimate_text_tokens(s)
    assert 5 <= n <= 20


def test_image_tokens_from_png_header_uses_dimensions():
    src = _b64_image_source(_png_header(1280, 720), 'image/png')
    n = estimate_image_tokens(src)
    # 1280 * 720 / 750 ≈ 1228, capped at 1600
    assert 1100 <= n <= 1600


def test_image_tokens_from_jpeg_header_uses_dimensions():
    src = _b64_image_source(_jpeg_header(800, 600), 'image/jpeg')
    n = estimate_image_tokens(src)
    # 800 * 600 / 750 = 640
    assert 600 <= n <= 700


def test_image_tokens_caps_at_1600_for_huge_images():
    src = _b64_image_source(_png_header(4096, 4096), 'image/png')
    assert estimate_image_tokens(src) == 1600


def test_image_tokens_unknown_source_returns_cap():
    # Better to overestimate than to silently charge 0 for an image.
    assert estimate_image_tokens(None) == 1600
    assert estimate_image_tokens({'type': 'url', 'url': 'https://x'}) == 1600
    assert estimate_image_tokens({'type': 'base64', 'media_type': 'image/png', 'data': ''}) == 1600
    assert estimate_image_tokens({'type': 'base64', 'media_type': 'image/png', 'data': 'not-real-b64-xx'}) == 1600


def test_image_tokens_falls_back_when_media_type_missing():
    src = {'type': 'base64', 'data': base64.b64encode(_png_header(200, 200)).decode('ascii')}
    n = estimate_image_tokens(src)
    assert 40 <= n <= 60  # 200 * 200 / 750 ≈ 53


def test_block_tokens_dispatches_by_type():
    assert estimate_block_tokens({'type': 'text', 'text': 'hello'}) > 0
    img = {'type': 'image', 'source': _b64_image_source(_png_header(100, 100), 'image/png')}
    assert estimate_block_tokens(img) >= 1
    assert estimate_block_tokens({'type': 'tool_reference'}) == 10
    assert estimate_block_tokens({'type': 'unknown'}) == 0
    assert estimate_block_tokens('plain string') > 0
    assert estimate_block_tokens(None) == 0
    assert estimate_block_tokens(42) == 0


def test_content_tokens_handles_string_and_list():
    assert estimate_content_tokens(None) == 0
    assert estimate_content_tokens('hello world hello world') > 0
    blocks = [
        {'type': 'text', 'text': 'first chunk'},
        {'type': 'image', 'source': _b64_image_source(_png_header(640, 480), 'image/png')},
        {'type': 'text', 'text': 'second chunk'},
    ]
    total = estimate_content_tokens(blocks)
    # image is ~400, text adds a handful more
    assert total >= 400


def test_image_only_subtotal_excludes_text():
    blocks = [
        {'type': 'text', 'text': 'should not count'},
        {'type': 'image', 'source': _b64_image_source(_png_header(750, 1000), 'image/png')},
    ]
    n = estimate_image_only_tokens(blocks)
    # 750 * 1000 / 750 = 1000
    assert 900 <= n <= 1100


def test_tool_use_tokens_includes_name_and_input():
    n = estimate_tool_use_tokens(
        'mcp__plugin_playwright_playwright__browser_take_screenshot',
        {'raw': True},
    )
    assert n >= 5


def test_tool_use_tokens_handles_non_serializable_input():
    class NotJSON:
        def __repr__(self) -> str:
            return 'NotJSON(...)'

    n = estimate_tool_use_tokens('SomeTool', NotJSON())
    assert n >= 1


def test_text_tokens_handles_special_token_strings():
    # tiktoken raises on `<|endoftext|>` by default — we must NOT.
    n = estimate_text_tokens('<|endoftext|> please summarize')
    assert n > 0
