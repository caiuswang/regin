"""Parse a Claude Code TUI screen (raw `tmux capture-pane -p` text) into the
live status the operator reads off the real terminal.

Why this exists: regin's `phase` verdict is derived from span recency
(`_main_phase` in `web/blueprints/trace/sessions/phase.py`), and spans only arrive
from hooks. Every hook-silent interval — extended thinking, one long tool
call, a long streamed response — decays to `idle` within seconds while the
terminal plainly shows the agent at work. The rendered screen is the only
channel that knows, so this module reads it.

Pure text in, structured out: no tmux, no DB, no I/O. `delivery.capture_screen`
supplies the text and owns every reachability/identity guard.

Every invariant here exists to make a bad parse *unusable* rather than
guessed-at. `parse_screen` returns None the moment it cannot be confident,
and callers must treat None as "fall back to the span heuristic", never as
"assume idle" — an unrecognised TUI version that silently read as idle would
be strictly worse than the recency rule it replaced.
"""

from __future__ import annotations

import re
from typing import NamedTuple

# The busy line is `<spinner> <Verb>… (<elapsed> · ↓ <n> tokens · …)`, and
# builds disagree about which of those parts they render. Any ONE mark
# identifies it, because relying on a single one has already failed twice:
# the spinner glyph rotates every frame, and `esc to interrupt` is absent on
# long turns in claude 2.x (verified against a live pane —
# `scripts/bridge_mvp.py`'s BUSY_NEEDLE reads that frame as idle).
_STATUS_MARKS = (
    re.compile(r'^\s*\S{1,2}\s+[A-Za-z][A-Za-z-]*…'),   # spinner + gerund
    re.compile(r'esc to interrupt', re.IGNORECASE),
)
# A parenthetical group, then the elapsed clock ANYWHERE inside it — the
# fields are ordered differently across builds (`(1m 4s · ↓ 2k tokens)`,
# `(↓ 2k tokens · 12s)`, `(esc to interrupt · 1m 4s)`), so anchoring on the
# clock being first silently loses the turn.
_PARENS_RE = re.compile(r'\(([^()]*)\)')
_CLOCK_RE = re.compile(r'\b(?:(\d+)h\s+)?(?:(\d+)m\s+)?(\d+)s\b')
# The whimsical gerund claude labels the turn with, e.g. "✻ Sock-hopping… ".
# Optional: a build that drops it is still busy off the other marks.
_VERB_RE = re.compile(r'^\s*\S{1,2}\s+([A-Za-z][A-Za-z-]*)…')
_TOKENS_RE = re.compile(r'[↓↑]\s*([\d.]+)([kKmM]?)\s*tokens')
# The retry banner claude draws beside the spinner while an API call is being
# re-attempted. It is its OWN line, indented behind a width-2 gutter box that
# holds an arrow glyph — the gutter is stripped so the label reads
# `Connection error`, not `↓ Connection error`. Only a NON-word glyph is
# eaten, so a label that legitimately opens on a digit (`500 Internal server
# error`) keeps it.
_GUTTER = r'^\s*(?:[^\w\s]\s+)?'
# Variant 1 (`error.rateLimits`):
#   `<label> · Retrying in <dur>[ (<resets>)] · attempt <n>/<max>`
# Anchored on the trailing `attempt n/max` because the label is open-ended —
# `API error`, `Connection error`, `500 Internal server error`, or a rate-limit
# phrase that carries its own parenthetical (`Usage limit reached (2pm)`).
# The `(resets …)` note inside the duration segment is tolerated and dropped:
# it is a wall clock, and nothing downstream may key on one.
_RETRY_RE = re.compile(
    _GUTTER +
    r'(?P<label>\S.*?)\s+·\s+Retrying in\s+(?P<wait>[^·()]*[^\s·()])'
    r'(?:\s*\([^()]*\))?\s+·\s+attempt\s+(?P<attempt>\d+)/(?P<max>\d+)\s*$'
)
# Variant 2 (`error.noResponse`) — a DIFFERENT sentence, not a spelling of the
# first: lowercase `retrying`, `waiting up to` instead of `Retrying in`, and a
# label that ends in the elapsed silence (`No response from the API after 8m`).
# This is the branch a long silent stall renders, which is the shape of the
# incident this whole feature exists for, so missing it would leave the feature
# inert on its own motivating case.
#
# Its last attempt renders `· retrying once, waiting up to <dur>` with NO
# attempt counter at all — the screen genuinely does not say which number this
# is, so `retry_attempt` stays None and `retry_final` carries the fact instead.
# Inventing an attempt number to fill the field would be fabricating data the
# terminal never showed.
_NO_RESPONSE_RE = re.compile(
    _GUTTER +
    r'(?P<label>\S.*?)\s+·\s+retrying(?P<once>\s+once)?,'
    r'\s+waiting up to\s+(?P<wait>[^·]*[^\s·])'
    r'(?:\s+·\s+attempt\s+(?P<attempt>\d+)/(?P<max>\d+))?\s*$'
)
_RETRY_PATTERNS = (_RETRY_RE, _NO_RESPONSE_RE)
# The composer box: a `❯` prompt line fenced by horizontal rules. Its
# presence is what licenses an "idle" verdict — absence of a busy line alone
# never does, because a pane in copy-mode or mid-resize shows neither.
_PROMPT_GLYPH = '❯'
# `❯ 2. Yes, and don't ask again` is a select-menu cursor, not the composer.
# A menu drawn with plain rules instead of corner glyphs otherwise passes the
# fencing check, reads as an idle prompt, and leaks the option text as a
# "draft" the card then shows.
_OPTION_RE = re.compile(r'\s*\d{1,2}\.\s')
_RULE_RE = re.compile(r'^\s*[─━]{20,}\s*$')
# Footer identity line: the session UUID claude prints under the composer,
# then cwd, branch and `ctx:N%`. The UUID is the only evidence that names
# WHICH session occupies the pane right now — `bridge_panes` rows are keyed
# by trace_id and never retired, so one pane accumulates many.
_UUID_RE = re.compile(
    r'\b([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\b'
)
_CTX_RE = re.compile(r'ctx:\s*(\d+)%')
_BRANCH_RE = re.compile(r'\(([^()]{1,80})\)')
# The in-flight tool: claude's `⏺ Tool(args)` bullet, with `⎿ Running…`
# under it while it has not returned.
_TOOL_RE = re.compile(r'^\s*⏺\s+(\S.*)$')
_RUNNING_RE = re.compile(r'^\s*⎿\s+Running…')

# How far above the composer box a busy line may sit. The status line renders
# directly above it, but a variable stack of hint/tip chrome can sit between —
# so the window is generous, and it is the status MARKS above, not the
# distance, that keep scrollback from matching.
_STATUS_LOOKBACK = 12
# How far from the busy line the retry banner may sit. It renders as its own
# line immediately beside the spinner, with at most the `noResponse` hint line
# ("a proxy or gateway that buffers streaming responses…") between them, and it
# may fall on either side. Deliberately far tighter than `_STATUS_LOOKBACK`:
# the previous turn's banner can still be drawn inside that 12-line window when
# the NEXT turn's spinner starts, and a banner read across that boundary
# attributes a retry to a turn that never had one.
_RETRY_ADJACENCY = 2
# How far above the status line to look for the tool bullet that produced
# it. A tool invocation wraps over several lines, so this is generous.
_TOOL_LOOKBACK = 30


class ScreenState(NamedTuple):
    """What the terminal says about this session right now.

    `busy` is the load-bearing field; everything else is decoration the NOW
    zone renders when present. A field the screen did not carry is None
    rather than a default, so a caller can tell "not shown" from "zero".
    """

    busy: bool
    verb: str | None = None
    elapsed_sec: int | None = None
    tokens: int | None = None
    tool: str | None = None
    session_id: str | None = None
    cwd: str | None = None
    branch: str | None = None
    model: str | None = None
    ctx_pct: int | None = None
    draft: str | None = None
    retry_label: str | None = None
    retry_in: str | None = None
    retry_attempt: int | None = None
    retry_max: int | None = None
    # True only on the `retrying once` frame, which states there is a retry in
    # flight while naming no attempt number. It is a third state, not a
    # stand-in for one: `retry_attempt is None` there because the screen said
    # nothing, and `retry_final` is what the screen DID say.
    retry_final: bool | None = None


def _elapsed_seconds(line: str) -> int | None:
    """Turn elapsed from any parenthetical on the status line, or None."""
    for group in _PARENS_RE.findall(line):
        m = _CLOCK_RE.search(group)
        if m:
            return ((int(m.group(1) or 0) * 3600)
                    + (int(m.group(2) or 0) * 60) + int(m.group(3)))
    return None


def _tokens(line: str) -> int | None:
    m = _TOKENS_RE.search(line)
    if not m:
        return None
    scale = {'k': 1_000, 'm': 1_000_000}.get(m.group(2).lower(), 1)
    return int(float(m.group(1)) * scale)


def _find_composer(lines: list[str]) -> int | None:
    """Index of the `❯` composer line, searched from the bottom up.

    Bounded by the two rules that fence it: a `❯` anywhere else on screen
    (a select-menu cursor, a shell prompt echoed in output) is not the
    composer and must not license an idle verdict.
    """
    for i in range(len(lines) - 1, -1, -1):
        if _PROMPT_GLYPH not in lines[i]:
            continue
        if _OPTION_RE.match(lines[i].split(_PROMPT_GLYPH, 1)[1]):
            continue
        above = i > 0 and _RULE_RE.match(lines[i - 1])
        below = i + 1 < len(lines) and _RULE_RE.match(lines[i + 1])
        if above and below:
            return i
    return None


def _find_busy(lines: list[str], composer: int) -> int | None:
    """Index of the turn's status line above the composer, or None.

    A line counts on ANY status mark, or on carrying an elapsed clock in
    parens — never on all three, so one build's missing field cannot lose the
    whole turn.
    """
    start = max(0, composer - 1 - _STATUS_LOOKBACK)
    for i in range(composer - 1, start - 1, -1):
        line = lines[i]
        if _RULE_RE.match(line):
            continue
        if any(mark.search(line) for mark in _STATUS_MARKS):
            return i
        if _elapsed_seconds(line) is not None and '·' in line:
            return i
    return None


def _retry_fields(m) -> dict:
    """One matched banner as the ScreenState fields it populates."""
    groups = m.groupdict()
    attempt = groups.get('attempt')
    return {
        'retry_label': m.group('label').strip(),
        'retry_in': m.group('wait').strip(),
        'retry_attempt': int(attempt) if attempt else None,
        'retry_max': int(groups['max']) if groups.get('max') else None,
        'retry_final': bool(groups.get('once')) or None,
    }


def _find_retry(lines: list[str], index: int, composer: int) -> dict:
    """The retry banner's fields, or {} when this turn shows none.

    Bound to the BUSY line, not to the composer window. The banner renders as
    its own line beside the spinner, and the two travel together — so anchoring
    on the spinner is what ties the retry to the turn that is actually running.
    Scanning the whole `_STATUS_LOOKBACK` window instead read the PREVIOUS
    turn's banner, still drawn a few lines up, as belonging to the new turn:
    the operator saw `attempt 5/10` over a healthy turn and an ERROR span was
    persisted under a prompt that never retried.

    Only ever consulted for a frame `_find_busy` already called busy. The
    banner alone is NOT evidence of a live retry — the last one of a turn
    stays on screen after the turn settles, so deriving `busy` from it would
    read a session sitting at the prompt as working, the exact asymmetry
    `_screen_phase` refuses to make in the other direction.
    """
    lo = max(0, index - _RETRY_ADJACENCY)
    hi = min(composer, index + _RETRY_ADJACENCY + 1)
    for i in range(lo, hi):
        for pattern in _RETRY_PATTERNS:
            m = pattern.match(lines[i])
            if m:
                return _retry_fields(m)
    return {}


def _find_tool(lines: list[str], below: int) -> str | None:
    """The newest `⏺ Tool(…)` bullet above `below` that has not returned.

    A bullet whose `⎿` continuation says anything other than `Running…` has
    already produced output, so it is history, not the in-flight call.
    """
    start = max(0, below - _TOOL_LOOKBACK)
    for i in range(below - 1, start - 1, -1):
        m = _TOOL_RE.match(lines[i])
        if not m:
            continue
        running = any(_RUNNING_RE.match(lines[j])
                      for j in range(i + 1, min(i + 4, len(lines))))
        return m.group(1).strip() if running else None
    return None


def _parse_footer(lines: list[str], composer: int) -> dict:
    """Session id / cwd / branch / model / ctx% off the identity line under
    the composer box. Absent on a pane too short to render it — every field
    stays None rather than being guessed from elsewhere on screen."""
    out: dict = {}
    for line in lines[composer + 1:composer + 4]:
        m = _UUID_RE.search(line)
        if not m:
            continue
        out['session_id'] = m.group(1)
        rest = line[m.end():]
        ctx = _CTX_RE.search(rest)
        if ctx:
            out['ctx_pct'] = int(ctx.group(1))
        # `\~/regin (branch) Opus 5 ctx:11%` — the escaped tilde is claude's
        # own rendering, not a capture artefact.
        path = re.match(r'\s*\\?(~?[^\s(]+)', rest)
        if path:
            out['cwd'] = path.group(1).replace('\\~', '~')
        branch = _BRANCH_RE.search(rest)
        if branch:
            out['branch'] = branch.group(1)
        model = re.search(r'\)\s+(.+?)\s+ctx:', rest)
        if model:
            out['model'] = model.group(1).strip()
        break
    return out


def parse_screen(text: str) -> ScreenState | None:
    """Live status from a raw `capture-pane -p` frame, or None when the
    screen cannot be read with confidence.

    None means exactly one thing to callers: *no verdict* — keep whatever
    the span heuristic said. It covers a pane in copy-mode, one resized too
    narrow to render the composer, a shell that is not claude at all, and a
    future TUI whose chrome we do not recognise. Guessing "idle" for any of
    those would reintroduce the bug this module exists to fix.
    """
    if not text:
        return None
    lines = text.rstrip('\n').split('\n')
    composer = _find_composer(lines)
    if composer is None:
        return None
    footer = _parse_footer(lines, composer)
    draft = lines[composer].split(_PROMPT_GLYPH, 1)[1].strip() or None
    index = _find_busy(lines, composer)
    if index is None:
        return ScreenState(busy=False, draft=draft, **footer)
    verb = _VERB_RE.match(lines[index])
    return ScreenState(
        busy=True,
        verb=verb.group(1) if verb else None,
        elapsed_sec=_elapsed_seconds(lines[index]),
        tokens=_tokens(lines[index]),
        tool=_find_tool(lines, index),
        draft=draft,
        **_find_retry(lines, index, composer),
        **footer,
    )


def as_payload(state: ScreenState | None) -> dict | None:
    """`ScreenState` as the JSON block the /live summary carries, or None.

    Deliberately narrower than the NamedTuple: the fields the card renders,
    plus `session_id` so a client can tell it is looking at the pane it
    thinks it is. `cwd`/`branch`/`model` are already on the summary from the
    DB and are not re-sent from a screen scrape.
    """
    if state is None:
        return None
    return {
        'busy': state.busy,
        'verb': state.verb,
        'elapsed_sec': state.elapsed_sec,
        'tokens': state.tokens,
        'tool': state.tool,
        'ctx_pct': state.ctx_pct,
        'draft': state.draft,
        'session_id': state.session_id,
        'retry_label': state.retry_label,
        'retry_in': state.retry_in,
        'retry_attempt': state.retry_attempt,
        'retry_max': state.retry_max,
        'retry_final': state.retry_final,
    }
