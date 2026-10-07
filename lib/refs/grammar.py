"""The reference grammar: `[label](kind:key)` outside code.

`frontend/src/utils/refs.js` is the same grammar, statement for statement, and
`tests/fixtures/refs_grammar_cases.json` holds both to one answer per case. A
change here that is not made there is how a chip and a backlink come to
disagree about the same text.

A reference is a markdown link, not a token of its own, so it stays readable
wherever regin's renderer is absent — a textarea, `regin feature spec`, a
tracker mirror, the agent reading its prompt.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional
from urllib.parse import quote, unquote

KINDS: tuple[str, ...] = ("feature", "task", "defect", "session", "file",
                          "memory")

#: `file:` has no alias: `f:` is taken by feature, and a path is typed often
#: enough that the whole word is no burden.
KIND_ALIASES: dict[str, str] = {
    "f": "feature", "t": "task", "d": "defect", "s": "session",
    "m": "memory", "lesson": "memory",
}

TITLE_MAX = 60
ELLIPSIS = "…"

_ID_KEY = re.compile(r"[1-9][0-9]{0,9}")
_KEY_PATTERNS = {
    "feature": _ID_KEY,
    "task": _ID_KEY,
    "defect": _ID_KEY,
    "session": re.compile(r"[0-9A-Za-z][0-9A-Za-z_-]{7,127}"),
    "memory": re.compile(r"[0-9a-f]{32}"),
}
_FILE_SEGMENT = r"(?:[A-Za-z0-9\-._~]|%[0-9A-F]{2})+"
_FILE_KEY = re.compile(rf"{_FILE_SEGMENT}(?:/{_FILE_SEGMENT})*")

_SHORT_PREFIX = {"feature": "F", "task": "T", "defect": "D", "session": "S",
                 "memory": "M"}

# The key class is the union of every kind's key alphabet; which kind accepts
# which key is decided after the match, so a near-miss is skipped rather than
# swallowing the text after it.
_LINK = re.compile(r"\[((?:\\[\s\S]|[^\\\[\]])*)\]\(([a-z]+):([A-Za-z0-9\-._~%/]+)\)")
_ASCII_PUNCT = frozenset("!\"#$%&'()*+,-./:;<=>?@[\\]^_`{|}~")
_LABEL_ESCAPES = re.compile(r"([\\\[\]<>&`])")
_NEWLINES = re.compile(r"\r\n|\r|\n")
_EDGE_BLANKS = re.compile(r"^[ \t]+|[ \t]+$")
_TRAILING_BLANKS = re.compile(r"[ \t]+$")
_RUNS_OF_BLANK = re.compile(r"\s+")

# `[^\r\n]`, never `.`: JS's `.` also refuses U+2028/U+2029, and the two
# grammars must not disagree about where a fence opens.
_OPEN_FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})([^\r\n]*)$")
_CLOSE_FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})[ \t]*$")
_BLANK = re.compile(r"^[ \t]*$")
_QUOTE_MARKER = re.compile(r" {0,3}>[ \t]?")


class RefError(ValueError):
    """A request the reference API cannot honour — a 400 naming its parameter."""

    def __init__(self, message: str, field: str) -> None:
        super().__init__(message)
        self.field = field


@dataclass(frozen=True)
class Ref:
    """One reference found in a body. `label` is unescaped; `start`/`end`
    are offsets into the text that was parsed."""

    kind: str
    key: str
    label: str
    start: int
    end: int

    @property
    def ref(self) -> str:
        return f"{self.kind}:{self.key}"


def canonical_kind(name: Optional[str]) -> Optional[str]:
    """A kind or one of its aliases, as the kind it names; None for neither."""
    if name in KINDS:
        return name
    return KIND_ALIASES.get(name or "")


def encode_file_key(path: str) -> Optional[str]:
    """A repo-relative path as a file key: each segment percent-encoded with
    only RFC 3986's unreserved characters left bare. None for a path that is
    empty, absolute, or holds an empty, `.` or `..` segment."""
    segments = (path or "").split("/")
    if any(seg in ("", ".", "..") or "\0" in seg for seg in segments):
        return None
    try:
        return "/".join(quote(seg, safe="", encoding="utf-8", errors="strict")
                        for seg in segments)
    except UnicodeEncodeError:
        return None


def decode_file_key(key: str) -> Optional[str]:
    """The path a file key names, or None when the key is not canonical.

    Canonical is what makes the check whole: `%2E%2E` decodes to `..` and
    `%2F` hides a separator inside a segment, and neither survives
    re-encoding to itself.
    """
    if not _FILE_KEY.fullmatch(key or ""):
        return None
    segments = []
    for seg in key.split("/"):
        try:
            segments.append(unquote(seg, encoding="utf-8", errors="strict"))
        except UnicodeDecodeError:
            return None
    path = "/".join(segments)
    return path if encode_file_key(path) == key else None


def is_valid_key(kind: str, key: str) -> bool:
    if kind == "file":
        return decode_file_key(key) is not None
    pattern = _KEY_PATTERNS.get(kind)
    return bool(pattern and pattern.fullmatch(key or ""))


def parse_ref_target(ref: str) -> Optional[tuple[str, str]]:
    """`feature:128` → `("feature", "128")`; None when it is not a reference."""
    kind, sep, key = (ref or "").partition(":")
    if not sep or kind not in KINDS or not is_valid_key(kind, key):
        return None
    return kind, key


def _flatten(text: str) -> str:
    return _EDGE_BLANKS.sub("", _NEWLINES.sub(" ", text or ""))


def _cut(title: str) -> str:
    flat = _flatten(title)
    if len(flat) <= TITLE_MAX:
        return flat
    return _TRAILING_BLANKS.sub("", flat[:TITLE_MAX]) + ELLIPSIS


def escape_label(text: str) -> str:
    """Backslash-escape what would make a label live markup in `marked`."""
    return _LABEL_ESCAPES.sub(r"\\\1", text)


def format_label(kind: str, key: str, title: str = "") -> str:
    """What accepting a menu row writes between `[` and `]`, escaped.

    A path is never cut: a file reference's label is the only place its
    readable form survives, and half a path names nothing.
    """
    if kind == "file":
        return escape_label(_flatten(decode_file_key(key) or key))
    short = key[:8] if kind in ("session", "memory") else key
    head = f"{_SHORT_PREFIX[kind]}{short}"
    text = _cut(title)
    return escape_label(f"{head} {text}" if text else head)


def format_ref(kind: str, key: str, title: str = "") -> str:
    if kind not in KINDS or not is_valid_key(kind, key):
        raise ValueError(f"not a reference: {kind}:{key}")
    return f"[{format_label(kind, key, title)}]({kind}:{key})"


def unescape_label(label: str) -> str:
    """A stored label as `marked` renders it: `\\` before ASCII punctuation
    drops, any other backslash stays."""
    out = []
    i = 0
    while i < len(label):
        if label[i] == "\\" and i + 1 < len(label) \
                and label[i + 1] in _ASCII_PUNCT:
            out.append(label[i + 1])
            i += 2
        else:
            out.append(label[i])
            i += 1
    return "".join(out)


def _lines(text: str):
    """`(start, end, line)` per line, `end` stopping before the line ending —
    CRLF, CR or LF, as CommonMark reads them."""
    start = 0
    for ending in _NEWLINES.finditer(text):
        yield start, ending.start(), text[start:ending.start()]
        start = ending.end()
    yield start, len(text), text[start:]


def _closes(line: str, fence: tuple[str, int]) -> bool:
    match = _CLOSE_FENCE.match(line)
    return bool(match and match.group(1)[0] == fence[0]
                and len(match.group(1)) >= fence[1])


def _opens(line: str) -> Optional[tuple[str, int]]:
    match = _OPEN_FENCE.match(line)
    if not match:
        return None
    run = match.group(1)
    if run[0] == "`" and "`" in match.group(2):
        return None
    return run[0], len(run)


def _escaped(text: str, at: int, floor: int) -> bool:
    """Whether the character at `at` follows an odd run of backslashes."""
    count = 0
    j = at - 1
    while j >= floor and text[j] == "\\":
        count += 1
        j -= 1
    return count % 2 == 1


def _run_end(text: str, i: int, stop: int) -> int:
    while i < stop and text[i] == "`":
        i += 1
    return i


def _closer(text: str, i: int, stop: int, length: int) -> Optional[int]:
    """The end of the next backtick run of exactly `length`, or None.
    Backslashes are literal inside a code span, so no run here is escaped."""
    while i < stop:
        if text[i] != "`":
            i += 1
            continue
        run = i
        i = _run_end(text, i, stop)
        if i - run == length:
            return i
    return None


def _span_ranges(text: str, start: int, stop: int) -> list[tuple[int, int]]:
    """Inline code spans in one paragraph: a backtick run closes at the next
    run of exactly its length. A run that never closes is literal text."""
    ranges = []
    i = start
    while i < stop:
        if text[i] != "`":
            i += 1
            continue
        opener = i
        i = _run_end(text, i, stop)
        if _escaped(text, opener, start):
            opener += 1
        if opener == i:
            continue
        close = _closer(text, i, stop, i - opener)
        if close is not None:
            ranges.append((opener, close))
            i = close
    return ranges


def _unquote(line: str, limit: int) -> tuple[int, str]:
    """`line` with up to `limit` block-quote markers (`>`) taken off the
    front, and how many were."""
    depth = 0
    while depth < limit:
        marker = _QUOTE_MARKER.match(line)
        if marker is None:
            break
        line = line[marker.end():]
        depth += 1
    return depth, line


class _Blocks:
    """One pass over the lines: fenced blocks become ranges whole, and the
    paragraphs between them are scanned for inline spans."""

    def __init__(self, text: str) -> None:
        self.text = text
        self.ranges: list[tuple[int, int]] = []
        self.fence: Optional[tuple[str, int]] = None
        self.fence_depth = 0
        self.fence_start = 0
        self.fence_end = 0
        self.para: Optional[int] = None
        self.para_end = 0

    def _flush(self) -> None:
        if self.para is not None:
            self.ranges.extend(_span_ranges(self.text, self.para,
                                            self.para_end))
            self.para = None

    def _close_fence(self, end: int) -> None:
        self.ranges.append((self.fence_start, end))
        self.fence = None

    def _inside_fence(self, end: int, line: str) -> bool:
        """Whether `line` belongs to the open fence. A line quoted less deeply
        than the opener ends the quote, and the fence with it."""
        depth, content = _unquote(line, self.fence_depth)
        if depth < self.fence_depth:
            self._close_fence(self.fence_end)
            return False
        if _closes(content, self.fence):
            self._close_fence(end)
        else:
            self.fence_end = end
        return True

    def feed(self, start: int, end: int, line: str) -> None:
        if self.fence is not None and self._inside_fence(end, line):
            return
        depth, content = _unquote(line, len(line))
        opened = _opens(content)
        if opened is not None or _BLANK.match(content):
            self._flush()
            if opened is not None:
                self.fence, self.fence_depth = opened, depth
                self.fence_start, self.fence_end = start, end
            return
        if self.para is None:
            self.para = start
        self.para_end = end

    def finish(self) -> list[tuple[int, int]]:
        if self.fence is not None:
            self._close_fence(len(self.text))
        else:
            self._flush()
        return sorted(self.ranges)


def code_ranges(text: str) -> list[tuple[int, int]]:
    """Half-open `(start, end)` ranges of fenced blocks and inline code spans.

    A fence closes only on its own character at least as long, one inside a
    block quote ends with the quote, and one left open runs to the end of the
    text, as in CommonMark. An inline span never crosses a blank line or a
    fence. Indented code blocks and list-item indentation are deliberately
    not modelled.
    """
    blocks = _Blocks(text)
    for start, end, line in _lines(text):
        blocks.feed(start, end, line)
    return blocks.finish()


def _overlaps(ranges: list[tuple[int, int]], start: int, end: int) -> bool:
    return any(lo < end and start < hi for lo, hi in ranges)


def _is_reference(text: str, match: re.Match, code) -> bool:
    start, end = match.span()
    if start > 0 and text[start - 1] == "!":
        return False
    if _escaped(text, start, 0) or _overlaps(code, start, end):
        return False
    kind = match.group(2)
    return kind in KINDS and is_valid_key(kind, match.group(3))


def parse_refs(text: Optional[str]) -> list[Ref]:
    """Every reference in `text`, in order, duplicates kept.

    Not a reference: an image (`!` before the `[`), an escaped `\\[`, a kind
    outside `KINDS`, a key its kind does not accept, a link with a title
    part, an autolink, and anything touching fenced or inline code.
    """
    if not text:
        return []
    code = code_ranges(text)
    return [Ref(kind=match.group(2), key=match.group(3),
                label=unescape_label(match.group(1)),
                start=match.start(), end=match.end())
            for match in _LINK.finditer(text)
            if _is_reference(text, match, code)]


def plain_title(text: Optional[str]) -> str:
    """A title as one line of reading text, each reference in it read as its
    label. Step titles carry their feature's reference, and a menu row or a
    chip showing `[F139 …](feature:139)` names it twice and reads as neither."""
    if not text:
        return ""
    parts, at = [], 0
    for ref in parse_refs(text):
        parts.append(text[at:ref.start])
        parts.append(ref.label)
        at = ref.end
    parts.append(text[at:])
    return _RUNS_OF_BLANK.sub(" ", "".join(parts)).strip()
