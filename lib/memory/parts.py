"""Split a memory body into an addressable lead + named parts.

`recall` used to return whole bodies, which made it the single most
expensive memory tool (measured: 1364 tok/call avg against 322 for
`index_fetch`, which hands back addresses instead). This module derives the
seams a body already has so `recall` can return the lead and *name* the
rest, leaving the caller to pull what it needs via `memory_read`.

Splitting on authored seams rather than a character budget is deliberate:
a fixed cap truncates mid-sentence, and a body whose lead paragraph ends on
a colon ("…Two injectors:") is actively misleading when cut there — hence
`_lead_is_incomplete`.

Measured against the live corpus (474 memories): only 20% carry named
seams (md headings or bold labels) and 74% are flat prose, so the named
part index is a bonus, not the mechanism. The lead/rest split is what
generalises — first paragraphs are 46% of corpus bytes, mean 344 chars.
"""

from __future__ import annotations

import re

#: A lead shorter than this is a fragment, not a statement — pull the next
#: block in rather than emit a stub the caller must always follow up on.
LEAD_MIN_CHARS = 120

#: Past this, a "paragraph" is a wall of text with no blank line in it — the
#: one shape the block split cannot help with (63% of recalled hits are a
#: single block; their leads run to 1488 chars). Cut at the last sentence end
#: before the budget, never mid-sentence, and let `memory_read` serve the rest.
LEAD_MAX_CHARS = 600

_SENTENCE_END = re.compile(r"(?<=[.!?])\s")

_MD_HEADING = re.compile(r"^\s{0,3}#{1,4}\s+(\S.*?)\s*$")
_BOLD_LINE = re.compile(r"^\s*\*\*([^*]{3,60}?):?\*\*\s*:?\s*$")
_BOLD_LEAD = re.compile(r"^\s*\*\*([^*]{3,60}?):?\*\*")


def _seam_name(line: str) -> str | None:
    """The part name this line opens, or None if it opens no part.

    Checked most-specific first: a `## Why` heading and a `**Why:**` label
    line both name a part, but a paragraph merely *starting* with bold text
    only counts when the bold run is followed by more prose on the line.
    """
    for pattern in (_MD_HEADING, _BOLD_LINE):
        m = pattern.match(line)
        if m:
            return m.group(1).strip()
    m = _BOLD_LEAD.match(line)
    if m and line[m.end():].strip():
        return m.group(1).strip()
    return None


def _blocks(body: str) -> list[str]:
    return [b for b in re.split(r"\n\s*\n", (body or "").strip()) if b.strip()]


def _lead_is_incomplete(lead: str) -> bool:
    """A lead that trails into what follows — too short to stand alone, or
    ending on a colon/dash that promises the next block."""
    stripped = lead.strip()
    return len(stripped) < LEAD_MIN_CHARS or stripped.endswith((":", "—", "-"))


def split_lead(body: str) -> tuple[str, str]:
    """`(lead, rest)` on a blank-line boundary — never mid-sentence.

    Absorbs following blocks while the lead is incomplete, so the caller
    always gets a self-contained opening. `rest` is "" when the whole body
    is the lead, which is the signal to emit it verbatim.
    """
    blocks = _blocks(body)
    if not blocks:
        return (body or "").strip(), ""
    taken = 1
    while taken < len(blocks) and _lead_is_incomplete("\n\n".join(blocks[:taken])):
        taken += 1
    lead = "\n\n".join(blocks[:taken])
    rest = "\n\n".join(blocks[taken:])
    if len(lead) > LEAD_MAX_CHARS:
        lead, spill = _cut_at_sentence(lead)
        rest = f"{spill}\n\n{rest}" if rest else spill
    return lead, rest


def _cut_at_sentence(lead: str) -> tuple[str, str]:
    """Split an over-long lead at the last sentence end within budget.

    Falls back to returning it whole when it contains no sentence boundary at
    all (a single unpunctuated run): a hard character cut would violate the
    never-mid-sentence contract, and an oversized lead is the lesser harm.
    """
    cuts = [m.start() for m in _SENTENCE_END.finditer(lead)
            if m.start() <= LEAD_MAX_CHARS]
    if not cuts:
        return lead, ""
    at = cuts[-1] + 1
    return lead[:at].strip(), lead[at:].strip()


def named_parts(body: str) -> list[tuple[str, str]]:
    """`[(name, text)]` for every authored seam in `body`, in order.

    Empty for the ~74% of memories that are flat prose — callers must treat
    an empty index as "no parts", not as "unparsed".
    """
    lines = (body or "").split("\n")
    parts: list[tuple[str, list[str]]] = []
    for line in lines:
        name = _seam_name(line)
        if name is not None:
            parts.append((name, [line]))
        elif parts:
            parts[-1][1].append(line)
    return [(name, "\n".join(body_lines).strip()) for name, body_lines in parts]


def find_part(body: str, wanted: str) -> str | None:
    """The named part matching `wanted` case-insensitively, by exact name
    then by prefix — so `part="Why"` finds a `**Why:**` section and
    `part="how"` finds `**How to apply:**`."""
    parts = named_parts(body)
    target = (wanted or "").strip().lower()
    for name, text in parts:
        if name.lower() == target:
            return text
    for name, text in parts:
        if name.lower().startswith(target) or target in name.lower():
            return text
    return None


__all__ = ["LEAD_MIN_CHARS", "split_lead", "named_parts", "find_part"]
