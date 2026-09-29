"""Prompt-anchor text matching: pair a resolved slash-command / truncated anchor
with the live `promptlive-` placeholder that still holds its full text."""

from __future__ import annotations

import re

from lib.trace.pending_spans import (
    PROMPT_PLACEHOLDER_PREFIX,
    is_pending_span_id,
)
from lib.trace.alias import ORIGIN_KEY as _ORIGIN_KEY


# A bare slash command echo, e.g. `/goal-verified`, `/git:commit`. The resolved
# transcript anchor for a slash command holds only this collapsed token; the
# live `promptlive-` placeholder holds its full expansion (the placeholder text
# STARTS WITH this echo). Used to pair the two so the expansion isn't lost when
# the placeholder is otherwise dropped as stale.
_SLASH_COMMAND_RE = re.compile(r'^/[\w:-]+$')

# The suffix a byte-capped prompt anchor carries when its text was truncated at
# post time (`hook_manager/.../span_posters.py::_truncate_utf8_with_marker`, cap
# `_PROMPT_ANCHOR_TEXT_MAX_BYTES`). A resolved anchor ending in this marker is a
# truncated view whose full text still lives on the live `promptlive-`
# placeholder — the same rescue the slash-command echo gets.
_PROMPT_ANCHOR_TRUNC_MARKER = '\n…[truncated]'


def _attrs(span: dict) -> dict:
    a = span.get('attributes')
    return a if isinstance(a, dict) else {}


def _is_live_prompt_placeholder(span: dict) -> bool:
    sid = span.get('span_id')
    return (
        span.get('name') == 'prompt'
        and span.get('status_code') == 'PENDING'
        and isinstance(sid, str)
        and sid.startswith(PROMPT_PLACEHOLDER_PREFIX)
    )


def _slash_echo_text(span: dict) -> str | None:
    """The bare `/command` echo a resolved prompt anchor carries, or None.

    A genuine slash-command anchor (status not PENDING, real `prompt-<uuid>`
    id) holds only the collapsed command token; that's the echo a placeholder's
    expansion must start with to be its same-turn pair."""
    if span.get('name') != 'prompt' or span.get('status_code') == 'PENDING':
        return None
    if is_pending_span_id(span.get('span_id')):
        return None
    text = _attrs(span).get('text')
    if not isinstance(text, str):
        return None
    stripped = text.strip()
    return stripped if _SLASH_COMMAND_RE.match(stripped) else None


def _anchor_match_prefix(span: dict) -> str | None:
    """The text prefix a placeholder's fuller text must start with to be this
    resolved anchor's expansion. Two anchor shapes qualify: a slash-command
    anchor carrying only its bare `/command` echo, and a byte-capped anchor
    whose text was truncated with `_PROMPT_ANCHOR_TRUNC_MARKER` (its pre-marker
    head). Both are prefixes of the full prompt the placeholder holds. None when
    `span` is not a resolved prompt anchor eligible for absorption."""
    echo = _slash_echo_text(span)
    if echo is not None:
        return echo
    text = _attrs(span).get('text')
    if (span.get('name') == 'prompt' and isinstance(text, str)
            and span.get('status_code') != 'PENDING'
            and not is_pending_span_id(span.get('span_id'))
            and text.endswith(_PROMPT_ANCHOR_TRUNC_MARKER)):
        return text[:-len(_PROMPT_ANCHOR_TRUNC_MARKER)]
    return None


def _is_expansion_anchor(candidate: dict, placeholder: dict, ph_text: str) -> bool:
    """True if `candidate` is a resolved prompt anchor in the same trace whose
    text (its `/command` echo or its truncated head) is a strict prefix of the
    placeholder's fuller text, and whose id is not below the placeholder's (the
    placeholder is minted just before its anchor)."""
    # Same-writer check. `trace_id` alone no longer distinguishes them: an
    # alias group's rows are re-keyed to the canonical id at fetch, so this
    # compared equal across the two writers and let a placeholder pair with the
    # OTHER writer's anchor — parking the expansion on the SDK row and leaving
    # the canonical anchor holding the bare echo.
    if candidate.get(_ORIGIN_KEY) != placeholder.get(_ORIGIN_KEY):
        return False
    if candidate.get('trace_id') != placeholder.get('trace_id'):
        return False
    prefix = _anchor_match_prefix(candidate)
    if prefix is None or not ph_text.startswith(prefix) or len(ph_text) <= len(prefix):
        return False
    ph_id, sid = placeholder.get('id'), candidate.get('id')
    return ph_id is None or sid is None or sid >= ph_id


def _expansion_anchor_for(
    placeholder: dict, spans: list[dict], used: set[tuple],
) -> dict | None:
    """The resolved slash-command anchor whose echo this placeholder expands.

    The placeholder text must START WITH the anchor's bare `/command` echo and
    be strictly longer (the expansion). When several candidate anchors match,
    pick the nearest one by id at-or-after the placeholder; anchors already
    claimed by an earlier placeholder (`used`) are skipped so two `/goal-verified`
    turns ingested back-to-back pair one-to-one instead of both grabbing the
    earliest anchor."""
    ph_text = _attrs(placeholder).get('text')
    if not isinstance(ph_text, str):
        return None
    matches = [
        s for s in spans
        if (s.get('trace_id'), s.get('span_id')) not in used
        and _is_expansion_anchor(s, placeholder, ph_text)
    ]
    if not matches:
        return None
    return min(matches, key=lambda s: (s.get('id') is None, s.get('id') or 0))


def _pair_slash_expansions(
    placeholders: list[dict], spans: list[dict],
) -> tuple[dict[tuple, str], set[tuple]]:
    """Greedily pair each placeholder with its resolved anchor, claiming each
    anchor so it can't be reused. Earliest placeholder takes the earliest
    matching anchor first, so two `/goal-verified` turns ingested back-to-back
    pair one-to-one. Returns (expansion-text by anchor key, placeholder keys to
    drop)."""
    placeholders.sort(key=lambda s: (s.get('id') is None, s.get('id') or 0))
    expansion_by_anchor: dict[tuple, str] = {}
    drop: set[tuple] = set()
    for ph in placeholders:
        anchor = _expansion_anchor_for(ph, spans, set(expansion_by_anchor))
        if anchor is None:
            continue
        key = (anchor.get('trace_id'), anchor.get('span_id'))
        expansion_by_anchor[key] = _attrs(ph).get('text')
        drop.add((ph.get('trace_id'), ph.get('span_id')))
    return expansion_by_anchor, drop


def _absorb_slash_command_expansions(spans: list[dict]) -> list[dict]:
    """Move a placeholder's full prompt text onto its surviving resolved anchor,
    then drop the placeholder.

    Two anchor shapes lose their full text to the merge and are rescued here.
    A slash command (`/goal-verified`) yields a resolved `prompt-<uuid>` anchor
    carrying only the collapsed echo plus a PENDING `promptlive-` placeholder
    with the full expansion. A large prompt (e.g. an external-agent regenerate
    task, >8 KiB) yields a resolved anchor whose text was byte-capped with the
    `\n…[truncated]` marker at post time, while the live placeholder holds the
    untruncated prompt. In both, turn_uuid is NULL on both rows so turn-pairing
    can't help, and `_drop_stale_blockers` would drop the placeholder once a
    later prompt lands — losing the full text. Here we instead transfer it onto
    a COPY of the resolved anchor (status stays OK, so PENDING-excluding
    aggregate readers still see it) and drop the placeholder — mirroring how
    `_inherit_turn_linkage` returns `dict(survivor)` copies to keep the merge
    pure. A stray client-only placeholder (`/workflows`) has no such anchor and
    is left untouched for the existing stale-drop path."""
    placeholders = [s for s in spans if _is_live_prompt_placeholder(s)]
    if not placeholders:
        return spans
    expansion_by_anchor, drop = _pair_slash_expansions(placeholders, spans)
    if not drop:
        return spans
    out: list[dict] = []
    for s in spans:
        sig = (s.get('trace_id'), s.get('span_id'))
        if sig in drop:
            continue
        if sig in expansion_by_anchor:
            survivor = dict(s)
            full = expansion_by_anchor[sig]
            # `chars` travels with the text it counts, as it does when an
            # untruncated body is taken over in `_take_untruncated_text` —
            # otherwise the reader's attribute rail reads `chars 22` beside
            # the 1625-character prompt it is describing.
            survivor['attributes'] = {**_attrs(s), 'text': full,
                                      'chars': len(full)}
            out.append(survivor)
        else:
            out.append(s)
    return out
