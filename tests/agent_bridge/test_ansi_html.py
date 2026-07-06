"""OSC-8 hyperlink safety in `lib/agent_bridge/ansi_html.py`.

`convert()` turns a live pane's `capture-pane -e` output into HTML that the
/live terminal-peek panel drops straight into a `v-html`ed `<pre>`. An OSC-8
hyperlink target is attacker-influenced (whatever text the pane printed), so
the href scheme must be allowlisted before it becomes a clickable `<a>` —
`html.escape` neutralizes quote-breakout but NOT a `javascript:` scheme.

Pinned here:

  * `_safe_href` allows only http/https/mailto and rejects scripting schemes
    even under whitespace/control-char/case obfuscation,
  * an unsafe OSC-8 link renders as plain text (no `<a href>` in the DOM),
  * a colliding NUL-placeholder with an out-of-range index does not raise.
"""

from __future__ import annotations

import pytest

from lib.agent_bridge import ansi_html
from lib.agent_bridge.ansi_html import _safe_href


@pytest.mark.parametrize("url", [
    "http://example.com",
    "https://example.com/path?q=1#frag",
    "HTTPS://EXAMPLE.COM",
    "mailto:someone@example.com",
    "  https://example.com  ",          # leading/trailing whitespace
    "\x00\x1fhttps://example.com",       # leading control bytes
])
def test_safe_href_allows_safe_schemes(url):
    assert _safe_href(url) is not None


@pytest.mark.parametrize("url", [
    "javascript:alert(1)",
    "JavaScript:alert(1)",
    "  javascript:alert(1)",             # leading whitespace
    "java\tscript:alert(1)",             # embedded tab
    "java\nscript:alert(1)",             # embedded newline
    "\x01\x02javascript:alert(1)",       # leading control bytes
    "data:text/html,<script>alert(1)</script>",
    "vbscript:msgbox(1)",
    "VBScript:msgbox(1)",
    "//evil.example.com",                # scheme-relative — ambiguous
    "/relative/path",                    # relative — no scheme
    "not a url",                         # no scheme
    "",
])
def test_safe_href_rejects_unsafe_or_ambiguous(url):
    assert _safe_href(url) is None


def _osc8(url: str, text: str) -> str:
    """Wrap `text` in a raw OSC-8 hyperlink pointing at `url`."""
    return f"\x1b]8;;{url}\x1b\\{text}\x1b]8;;\x1b\\"


def test_convert_emits_anchor_for_safe_link():
    out = ansi_html.convert(_osc8("https://example.com", "click"))
    assert '<a href="https://example.com"' in out
    assert 'rel="noopener"' in out
    assert "click" in out


def test_convert_drops_anchor_for_javascript_link():
    out = ansi_html.convert(_osc8("javascript:alert(1)", "click"))
    assert "<a href" not in out
    assert "javascript:" not in out
    assert "click" in out  # link text survives as plain text


def test_convert_survives_out_of_range_placeholder():
    # Pane text literally containing a colliding placeholder with an index
    # past the (empty) links list must not raise IndexError → 500.
    out = ansi_html.convert("before \x00L9\x00 after")
    assert "before" in out and "after" in out
