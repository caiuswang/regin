"""SGR/OSC-8 escape codes (`tmux capture-pane -e` output) to inline-styled HTML.

Renders exactly what `capture_screen()` read off a live pane so the /live
card's terminal-peek panel can drop the result straight into a `<pre>` — no
client-side escape parsing, no xterm.js dependency. Colors are xterm's
standard 256-color palette (16 named, 6x6x6 cube, grayscale ramp); OSC-8
hyperlinks (used by Claude Code's own "Learn more" links) become real `<a>`
tags instead of leaking their raw escape text.
"""

from __future__ import annotations

import html
import re

_BASIC_16 = ['000000', '800000', '008000', '808000', '000080', '800080',
             '008080', 'c0c0c0', '808080', 'ff0000', '00ff00', 'ffff00',
             '0000ff', 'ff00ff', '00ffff', 'ffffff']


def _xterm256_to_hex(code: str) -> str:
    n = int(code)
    if n < 16:
        return '#' + _BASIC_16[n]
    if n <= 231:
        n -= 16
        r, g, b = n // 36, (n % 36) // 6, n % 6
        comp = lambda v: 0 if v == 0 else 55 + v * 40  # noqa: E731
        return '#%02x%02x%02x' % (comp(r), comp(g), comp(b))
    gray = 8 + (n - 232) * 10
    return '#%02x%02x%02x' % (gray, gray, gray)


_SIMPLE_CODE_EFFECTS = {
    '': lambda s: s.update(fg=None, bg=None, bold=False),
    '0': lambda s: s.update(fg=None, bg=None, bold=False),
    '1': lambda s: s.update(bold=True),
    '22': lambda s: s.update(bold=False),
    '39': lambda s: s.update(fg=None),
    '49': lambda s: s.update(bg=None),
}


def _is_256_color_code(codes: list[str], j: int, prefix: str) -> bool:
    return codes[j] == prefix and j + 2 < len(codes) and codes[j + 1] == '5'


def _apply_code(state: dict, codes: list[str], j: int) -> int:
    if _is_256_color_code(codes, j, '38'):
        state['fg'] = _xterm256_to_hex(codes[j + 2])
        return j + 3
    if _is_256_color_code(codes, j, '48'):
        state['bg'] = _xterm256_to_hex(codes[j + 2])
        return j + 3
    effect = _SIMPLE_CODE_EFFECTS.get(codes[j])
    if effect:
        effect(state)
    return j + 1


def _span_open_tag(state: dict) -> str | None:
    style = []
    if state['fg']:
        style.append(f"color:{state['fg']}")
    if state['bg']:
        style.append(f"background:{state['bg']}")
    if state['bold']:
        style.append('font-weight:600')
    if not style:
        return None
    return f'<span style="{";".join(style)}">'


_OSC8_OPEN_RE = re.compile(r'\x1b\]8;[^;]*;(.*?)(?:\x1b\\|\x07)')
_OSC8_CLOSE_RE = re.compile(r'\x1b\]8;;(?:\x1b\\|\x07)')


def _extract_osc8_links(text: str) -> tuple[str, list[str]]:
    """Stash OSC-8 hyperlink targets as index tokens before SGR conversion.

    Close must run before open — `]8;;ESC\\` (empty params, empty url) is a
    degenerate match of the open pattern's own grammar, so converting close
    markers first is what keeps them from being swallowed as bogus opens.
    """
    links: list[str] = []

    def stash(m: re.Match) -> str:
        links.append(m.group(1))
        return f'\x00L{len(links) - 1}\x00'

    text = _OSC8_CLOSE_RE.sub('\x00E\x00', text)
    text = _OSC8_OPEN_RE.sub(stash, text)
    return text, links


_SAFE_SCHEMES = frozenset({'http', 'https', 'mailto'})
_URL_INLINE_STRIP_RE = re.compile(r'[\t\n\r]')
_URL_LEAD_STRIP_RE = re.compile(r'^[\x00-\x20]+')
_URL_SCHEME_RE = re.compile(r'([a-zA-Z][a-zA-Z0-9+.\-]*):')


def _safe_href(url: str) -> str | None:
    """Escaped href value if `url` carries an allowlisted safe scheme
    (http/https/mailto), else None.

    Normalizes the way a browser does before it sniffs a scheme: tab/newline/
    CR bytes are dropped from anywhere (so `java\\tscript:` collapses to
    `javascript:`) and leading control/space is trimmed. Only an explicit
    allowlisted scheme yields an href; anything relative, scheme-relative, or
    otherwise unclassifiable returns None so no navigable `javascript:` /
    `data:` / `vbscript:` URL can reach the DOM."""
    cleaned = _URL_LEAD_STRIP_RE.sub('', _URL_INLINE_STRIP_RE.sub('', url))
    m = _URL_SCHEME_RE.match(cleaned)
    if not m or m.group(1).lower() not in _SAFE_SCHEMES:
        return None
    return html.escape(cleaned, quote=True)


def _reinsert_links(html_text: str, links: list[str]) -> str:
    def open_tag(m: re.Match) -> str:
        idx = int(m.group(1))
        if idx >= len(links):
            return html.escape(m.group(0))
        href = _safe_href(links[idx])
        if href is None:
            return ''
        return (f'<a href="{href}" target="_blank" rel="noopener" '
                'style="color:inherit;text-decoration:underline">')

    html_text = re.sub(r'\x00L(\d+)\x00', open_tag, html_text)
    return html_text.replace('\x00E\x00', '</a>')


_SGR_RE = re.compile(r'\x1b\[([0-9;]*)m')


def convert(text: str) -> str:
    """`capture-pane -e` output to inline-styled HTML, safe to drop in a `<pre>`."""
    text, links = _extract_osc8_links(text)
    out = []
    open_span = False
    state = {'fg': None, 'bg': None, 'bold': False}
    pos = 0
    for m in _SGR_RE.finditer(text):
        chunk = text[pos:m.start()]
        if chunk:
            out.append(html.escape(chunk))
        pos = m.end()
        codes = m.group(1).split(';') if m.group(1) else ['0']
        j = 0
        while j < len(codes):
            j = _apply_code(state, codes, j)
        if open_span:
            out.append('</span>')
            open_span = False
        tag = _span_open_tag(state)
        if tag:
            out.append(tag)
            open_span = True
    tail = text[pos:]
    if tail:
        out.append(html.escape(tail))
    if open_span:
        out.append('</span>')
    return _reinsert_links(''.join(out), links)
