"""Parse a Claude Code numbered select-menu widget out of raw tmux pane text.

Distinct from `delivery._await_pane_text` (which only confirms a KNOWN
string echoed back after typing), this extracts what options are actually
on screen right now — needed for requests whose `PermissionRequest` hook
payload carries no `permission_suggestions` at all. Empirically verified
against a live pane (regin session 197d3fea, span 7b47961b): the trace DB
recorded `option_count=1, default_option_id="deny"` for a pending
`ExitPlanMode` request while the real terminal showed a 4-option menu
("1. Yes, auto-accept edits" / "2. Yes, manually approve edits" / "3. No,
refine with Ultraplan on Claude Code on the web" / "4. Tell Claude what to
change") — there is no structural channel to that menu, only the rendered
text.

Every invariant below exists to make a bad parse unusable rather than
guessed-at against a live process: `parse_select_menu` returns `None` the
moment it cannot be confident, and callers must treat `None` as "resolve
this in the terminal", never as "assume option 0".
"""

from __future__ import annotations

import re
from typing import NamedTuple

# A menu row, stripped of any leading cursor glyph: an ordinal, a dot, a
# space, then the label. Matched against each line's cursor-stripped,
# right-trimmed content — leading indentation and the cursor are read
# separately so column position never affects whether a line counts.
_OPTION_RE = re.compile(r'^(\d{1,2})\.\s+(\S.*)$')
_CURSOR_GLYPH = '❯'  # '❯' — the glyph claude's select TUI focuses with.

_MIN_OPTIONS = 2
_MAX_OPTIONS = 20  # sanity bound; a real menu is never this deep.


class ParsedMenu(NamedTuple):
    options: list[str]  # 0-based, in display order.
    cursor_index: int   # which option currently carries the cursor.


def _candidates(lines: list[str]) -> list[tuple[bool, int, str]]:
    """(cursor, ordinal, label) for every line that looks like a menu row.

    A label that soft-wraps onto a second terminal row is not rejoined: the
    continuation line has no leading digit, so it is simply absent from
    this pass. That can truncate what a caller SHOWS, but never shifts
    which index means what, so it never affects which keystroke gets
    driven.
    """
    out = []
    for raw in lines:
        left = raw.lstrip()
        cursor = left.startswith(_CURSOR_GLYPH)
        content = (left[1:] if cursor else left).strip()
        m = _OPTION_RE.match(content)
        if m:
            out.append((cursor, int(m.group(1)), m.group(2).strip()))
    return out


def _last_contiguous_run(
        candidates: list[tuple[bool, int, str]]) -> list[tuple[bool, int, str]]:
    """The trailing slice of `candidates` numbered N, N-1, ... down to 1,
    scanning backward from the end. Anchoring on the LAST run (the bottom of
    the pane) picks the actual live widget over an earlier "1. "-style list
    in scrollback — a plan body or markdown block a prior turn printed."""
    run = [candidates[-1]]
    for c in reversed(candidates[:-1]):
        if c[1] != run[0][1] - 1:
            break
        run.insert(0, c)
    return run


def _validate_run(run: list[tuple[bool, int, str]]) -> int | None:
    """The run's single cursor row index, or `None` if the run can't be
    trusted as a live menu (wrong size, gaps/repeats, or not exactly one
    focused row)."""
    if not (_MIN_OPTIONS <= len(run) <= _MAX_OPTIONS):
        return None
    if [c[1] for c in run] != list(range(1, len(run) + 1)):
        return None
    cursor_rows = [i for i, c in enumerate(run) if c[0]]
    return cursor_rows[0] if len(cursor_rows) == 1 else None


def parse_select_menu(text: str) -> ParsedMenu | None:
    """The live numbered select menu in `text`, or `None` when it can't be
    trusted.

    Refuses unless ALL hold:
      - the LAST contiguous run of candidate lines is numbered 1..N with no
        gaps or repeats, N in [`_MIN_OPTIONS`, `_MAX_OPTIONS`] (see
        `_last_contiguous_run`);
      - exactly one row in that run carries the cursor glyph. Zero means
        nothing is focused (a stale capture of an already-resolved dialog);
        more than one means the capture is corrupted or straddles two
        redraws (see `_validate_run`).
    """
    candidates = _candidates((text or '').splitlines())
    if len(candidates) < _MIN_OPTIONS:
        return None

    run = _last_contiguous_run(candidates)
    cursor_index = _validate_run(run)
    if cursor_index is None:
        return None

    return ParsedMenu(options=[c[2] for c in run], cursor_index=cursor_index)
