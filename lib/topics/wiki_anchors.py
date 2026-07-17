"""Wiki-cited identifier anchors — the grounding signal for content-drift
materiality.

A topic wiki cites concrete identifiers in inline code spans (`foo_bar`,
`lib/topics/route.py:104`, `Class.method`). Those citations are the wiki's
checkable claims about the code: while every anchor that grounded to a ref
file at digest time still appears in it, an edit there has not invalidated
anything the wiki actually says. Materiality is "a cited anchor vanished",
not "any byte changed" — a raw hash mismatch dismisses as noise roughly half
the time (measured on this repo's drift-thread history).

Extraction is deterministic and prose-blind: inline code spans only, split
into identifier tokens, tokens shorter than 4 chars dropped (path segments
like `lib`/`py` are not claims). Grounding is token-exact on both sides —
an anchor counts as present only when it appears as a whole identifier, so
a rename to a superset (`foo` → `foo_v2`) reads as vanished, not surviving
as a substring.
"""

from __future__ import annotations

import re

# Uncapped span body: a length cap would make an over-long span fail at its
# opening backtick, letting its CLOSING backtick pair with the next span's
# opener — leaking inter-span prose as anchors and dropping the real anchor
# after it. Instead every paired span is consumed intact (newlines allowed,
# as in CommonMark) and over-long ones are discarded post-match. Pairing is
# done per paragraph, so a stray unpaired backtick can't shift pairing past
# a blank line.
_CODE_SPAN = re.compile(r"`([^`]+)`")
_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]{3,}")
_PARAGRAPH_BREAK = re.compile(r"\n\s*\n")
_FENCE_LINE = re.compile(r"^\s*(```|~~~)")
_MAX_SPAN_LEN = 120


def _without_fenced_blocks(text: str) -> str:
    """Blank out fenced code blocks (``` or ~~~) — their backtick runs would
    poison span pairing, and example code is illustration, not citation.
    Dropped lines become empty lines, so prose separated only by a fence
    keeps its paragraph boundary instead of merging into one pairing scope.
    A dangling unterminated fence blanks only its marker line: swallowing
    the rest of the page would silently drop every later citation (agent
    wikis do get truncated mid-fence)."""
    lines = text.splitlines()
    fences = [i for i, line in enumerate(lines) if _FENCE_LINE.match(line)]
    dangling = fences.pop() if len(fences) % 2 else None
    drop: set[int] = set()
    for open_i, close_i in zip(fences[0::2], fences[1::2]):
        drop.update(range(open_i, close_i + 1))
    if dangling is not None:
        drop.add(dangling)
    return "\n".join("" if i in drop else line for i, line in enumerate(lines))


def wiki_anchor_tokens(wiki_text: str) -> set[str]:
    """Identifier tokens cited in the wiki's inline code spans. Dotted or
    pathy spans contribute each identifier segment separately
    (`lib/topics/route.py` → {"topics", "route"})."""
    tokens: set[str] = set()
    for paragraph in _PARAGRAPH_BREAK.split(_without_fenced_blocks(wiki_text)):
        for span in _CODE_SPAN.findall(paragraph):
            if len(span) <= _MAX_SPAN_LEN:
                tokens.update(_IDENTIFIER.findall(span))
    return tokens


def content_identifier_tokens(content: str) -> set[str]:
    """Whole identifier tokens of one ref file, for token-exact grounding.
    Maximal-munch tokenization is what makes the check boundary-safe: in
    `foo_v2` only the full token exists, so a wiki-cited `foo` correctly
    reads as vanished."""
    return set(_IDENTIFIER.findall(content))


def anchors_in_content(tokens: set[str], content: str) -> list[str]:
    """The cited tokens present (as whole identifiers) in one ref file — the
    subset of the wiki's claims that ground to this file, stored per digest
    row at capture."""
    return sorted(tokens & content_identifier_tokens(content))


__all__ = ["wiki_anchor_tokens", "anchors_in_content",
           "content_identifier_tokens"]
