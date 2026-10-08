"""The one record shape every searchable surface reduces to, plus the
matching / scoring / windowing primitives shared by every kind.

Deliberately dependency-free (no ORM, no Flask) so the scoring rules can be
tested directly and so the frontend's mirror of them
(`frontend/src/utils/searchDocs.js`) has a single readable reference.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

# Body hits saturate here so a 4000-line shell log cannot outrank a title
# match. Mirrors `score` in the Full-text Search spec §03.
MAX_COUNTED_BODY_HITS = 9
TITLE_WEIGHT = 100
SUB_WEIGHT = 20

# The preview pane is designed against exactly this window: the server never
# ships a whole document, and the client renders 34 of these lines.
BODY_WINDOW_LINES = 60
# Per-span body cap before indexing (spec §10 "binary / huge tool output").
MAX_BODY_CHARS = 200_000
# Below this a query is held: no request, no local pass.
MIN_QUERY_CHARS = 2

KIND_SPAN = "span"
KIND_PATTERN = "pattern"
KIND_SESSION = "session"
KIND_FILE = "file"
KIND_FEATURE = "feature"
KIND_TASK = "task"
KIND_DEFECT = "defect"
# Fixed render order — groups are never re-ordered by score, so the eye
# learns the layout. Process entities lead: the reader's work is organised
# Feature → Task → Defect → Session, and the documents follow.
KIND_ORDER = (KIND_FEATURE, KIND_TASK, KIND_DEFECT, KIND_SESSION,
              KIND_PATTERN, KIND_FILE, KIND_SPAN)
ENTITY_KINDS = (KIND_FEATURE, KIND_TASK, KIND_DEFECT)


@dataclass
class SearchDoc:
    kind: str
    id: str
    title: str
    sub: str = ""
    tag: str = ""
    crumb: str = ""
    body: str = ""
    mono: bool = False
    href: str = ""
    # The /api/files source a viewer opens this document from, in the query
    # shape the client's `source.js` takes (`session` + `path`); empty when
    # the document is not a file one.
    source: dict = field(default_factory=dict)
    # Set by `score_doc`, carried to the client so the row can render its
    # match-count pill without re-scanning the (windowed) body.
    body_hits: int = 0

    def as_json(self) -> dict:
        out = {
            "kind": self.kind, "id": self.id, "title": self.title,
            "sub": self.sub, "tag": self.tag, "crumb": self.crumb,
            "body": self.body, "mono": self.mono, "bodyHits": self.body_hits,
        }
        if self.href:
            out["href"] = self.href
        if self.source:
            out["source"] = self.source
        return out


@dataclass
class Scored:
    doc: SearchDoc
    score: int
    hits: int
    # Document order, ascending. The tie-break: chronological for spans,
    # recency for sessions — each caller emits rows in the order it wants.
    order: int = 0
    extra: dict = field(default_factory=dict)


def count_hits(text: str, query: str) -> int:
    """Non-overlapping case-insensitive occurrences of `query` in `text`."""
    if not text or not query:
        return 0
    hay = text.lower()
    needle = query.lower()
    count = 0
    at = hay.find(needle)
    while at > -1:
        count += 1
        at = hay.find(needle, at + len(needle))
    return count


def score_doc(doc: SearchDoc, query: str, order: int = 0) -> Scored | None:
    """Score one document, or None when it has no hit anywhere.

    Case-insensitive substring on the raw query — no stemming, no fuzz. An
    agent trace is full of identifiers (`rateLimit`, `R-114`, `ETIMEDOUT`)
    where edit-distance matching produces noise, and CJK matches without
    tokenization because substring already works.
    """
    needle = query.lower()
    in_title = needle in (doc.title or "").lower()
    in_sub = needle in (doc.sub or "").lower()
    hits = count_hits(doc.body, query)
    if not in_title and not in_sub and not hits:
        return None
    doc.body_hits = hits
    total = (
        (TITLE_WEIGHT if in_title else 0)
        + (SUB_WEIGHT if in_sub else 0)
        + min(hits, MAX_COUNTED_BODY_HITS)
    )
    return Scored(doc=doc, score=total, hits=hits, order=order)


def rank(scored: list[Scored]) -> list[Scored]:
    """Score desc, then document order — so equal scores stay stable and
    readable rather than shuffling on every keystroke."""
    return sorted(scored, key=lambda s: (-s.score, s.order))


def body_window(body: str, query: str, lines: int = BODY_WINDOW_LINES) -> str:
    """The ≤`lines`-line slice of `body` centred on its first hit.

    The client's preview renders a 34-line window out of this and states its
    own elision; shipping the whole document instead would send megabytes for
    a pane that can only show a screenful.
    """
    if not body:
        return ""
    rows = body.split("\n")
    if len(rows) <= lines:
        return body
    first = -1
    if query:
        needle = query.lower()
        for i, row in enumerate(rows):
            if needle in row.lower():
                first = i
                break
    # The window must always CONTAIN the hit. The spec's `first > 12 ? first-6
    # : 0` rule assumes the 34-line preview, where any early hit is on screen
    # anyway; applied to a small window (the 6-line quotes a session digest is
    # built from) it silently produced a window with no match in it, and those
    # sessions then rendered as "0 matches".
    lead = min(6, max(0, lines - 1))
    if first <= lead * 2 and first < lines:
        start = 0
    else:
        start = max(0, first - lead)
    return "\n".join(rows[start:start + lines])


def clip_body(text: str) -> str:
    """Cap a single document's body before indexing. The tail is dropped and
    said so — a 40MB binary tool result must not be scanned or shipped."""
    if len(text) <= MAX_BODY_CHARS:
        return text
    return text[:MAX_BODY_CHARS] + "\n…[body truncated at 200KB for search]"


# LIKE's own wildcards have to be neutralised or a query containing `%`
# matches every row. `\\` is unusable as the escape character because the
# JSON encoding of a non-ASCII query is full of `\\uXXXX`.
LIKE_ESCAPE = "~"


def like_patterns(query: str) -> list[str]:
    """SQL LIKE patterns that can pre-filter a JSON `attributes` column.

    Attributes are stored via `json.dumps` with the default
    `ensure_ascii=True`, so a CJK query lives in the column as `\\uXXXX`
    escapes while an ASCII one lives verbatim — except for the characters
    JSON escapes. Both encodings are emitted and OR-ed by the caller; the
    authoritative match still runs in Python over the decoded body, so a
    pattern that over-matches only costs a parse.

    Patterns are returned ready for `LIKE ? ESCAPE '~'`.
    """
    forms = {json.dumps(query)[1:-1], json.dumps(query, ensure_ascii=False)[1:-1]}
    out = []
    for form in sorted(forms):
        for ch in (LIKE_ESCAPE, "%", "_"):
            form = form.replace(ch, LIKE_ESCAPE + ch)
        out.append(f"%{form}%")
    return out
