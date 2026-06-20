"""Serve-time merge of the append-only span store into one canonical view.

`session_spans` is written append-only by two independent sources — live
hook events (`source='hook'`: tool timing, permissions, skill reads, the
in-flight `promptlive-` prompt placeholder) and the transcript scan
(`source='transcript'`: the real `prompt-<uuid>` anchor,
assistant_response/thinking, local commands). The two disagree about the
same turn: a placeholder and its real anchor coexist; a pending tool/
permission span coexists with its resolved counterpart.

`merge_spans` is the SINGLE place that reconciles them. It runs at read
time as a pure function over the rows of one window — no DB writes, no
mutation — so the store stays append-only and projection rules can change
without a data migration (they self-heal on the next read).

It owns three things, in order:

  1. **dedup / supersession** — drop the transient placeholder/pending rows
     whose resolved counterpart is present in the window (this replaces the
     old ingest-time DELETE/promote machinery: `_retire_superseded_pending`,
     `_retire_pending_permissions`, `_sweep_superseded_pending_blockers`).
  2. **reparent** — `_graft_orphans` (the 3-tier ladder in projection.py).
  3. **reorder** — `_graft_orphans` already sorts; `_build_span_tree`
     (downstream) does the final stable sort by (start_time, id).

When the resolved counterpart is NOT yet present (a prompt still in flight,
a permission still blocking), the placeholder is kept — that is exactly how
the live view shows in-progress work. Over an already-reconciled historical
window (no placeholders/pending left) every drop rule is a no-op, so
`merge_spans(raw) == _graft_orphans(raw)` — the idempotency/no-regression
property the read path relies on.
"""

from __future__ import annotations

import re

from lib.trace.pending_spans import (
    PROMPT_PLACEHOLDER_PREFIX,
    is_pending_span_id,
    pending_id_for_resolved,
)
from lib.trace.projection import _graft_orphans

# A bare slash command echo, e.g. `/goal-verified`, `/git:commit`. The resolved
# transcript anchor for a slash command holds only this collapsed token; the
# live `promptlive-` placeholder holds its full expansion (the placeholder text
# STARTS WITH this echo). Used to pair the two so the expansion isn't lost when
# the placeholder is otherwise dropped as stale.
_SLASH_COMMAND_RE = re.compile(r'^/[\w:-]+$')


def _attrs(span: dict) -> dict:
    a = span.get('attributes')
    return a if isinstance(a, dict) else {}


def _inherit_turn_linkage(survivor: dict, placeholder: dict) -> dict:
    """Hand a retired tool placeholder's `turn_uuid` + `resp-`/`think-` parent
    to the resolved span that supersedes it, when the survivor never got its
    own.

    A slow tool emits its `pending-<tu>` placeholder at PreToolUse and its
    resolved `tool.<Name>` at PostToolUse, both parent-less / turn_uuid-less.
    If a `turn_trace` attribution pass lands while only the placeholder exists,
    the placeholder absorbs the turn linkage and the later resolved span never
    does (the turn is cached, so it's never re-attributed). Dropping the
    placeholder here would then strand the resolved span on the prompt-root
    graft fallback — the assistant-response branch visibly collapses.

    Gate on a NULL `turn_uuid`: attribution always sets `turn_uuid` alongside
    the parent, so its absence is the unambiguous mark of the un-attributed
    survivor. That also makes the transfer materialize-proof — `_persist_
    projection` writes `parent_id` but never `turn_uuid`, so a prompt-root
    parent a prior materialize may have baked onto the survivor is still
    overridden here. Returns a copy when it changes anything (merge stays
    pure)."""
    if survivor.get('turn_uuid') or not placeholder.get('turn_uuid'):
        return survivor
    out = dict(survivor)
    out['turn_uuid'] = placeholder.get('turn_uuid')
    if placeholder.get('parent_id'):
        out['parent_id'] = placeholder.get('parent_id')
    return out


def _classify_supersessions(spans: list[dict], placeholders: dict) -> tuple[set, dict]:
    """Walk the window once: return the placeholder keys each resolved span
    supersedes (`drop`) and, for a resolved *tool* span, the `pending-` tool
    placeholder it should inherit turn linkage from (`inherit`).

    Both sides are gated on `tool.` rather than name-equality: the pending
    span is minted `tool.{raw}` while the resolved one is `tool.{normalize}`,
    so an exact-name guard would silently miss any tool `_normalize_tool_name`
    rewrites (today none, but it's documented to collapse MCP names). The
    placeholder is already matched by the survivor's own tool_use_id, whose
    only two candidates are `pending-<tu>` (tool) and `permreq-<tu>`
    (permission.request) — the `tool.` check keeps the former and drops the
    latter without reparenting the resolved tool span."""
    drop: set[tuple] = set()
    inherit: dict = {}  # survivor span_id → placeholder it should inherit from
    for s in spans:
        is_tool = (s.get('name') or '').startswith('tool.')
        for pid in pending_id_for_resolved(s, _attrs(s)):
            key = (s.get('trace_id'), pid)
            drop.add(key)
            ph = placeholders.get(key)
            if is_tool and ph is not None and (ph.get('name') or '').startswith('tool.'):
                inherit[s.get('span_id')] = ph
    return drop, inherit


def _drop_superseded_placeholders(spans: list[dict]) -> list[dict]:
    """Drop the `promptlive-`/`pending-`/`permreq-` placeholder rows whose
    resolved span is present in the window, keyed by `pending_id_for_resolved`
    (prompt-text hash / tool_use_id). A placeholder with no resolved
    counterpart in the window survives — that's the in-flight prompt / blocking
    tool the live view must still show.

    Before dropping a tool placeholder, hand its turn linkage to the resolving
    tool span (`_inherit_turn_linkage`) so a slow tool keeps its branch."""
    placeholders = {
        (s.get('trace_id'), s.get('span_id')): s
        for s in spans if is_pending_span_id(s.get('span_id'))
    }
    drop, inherit = _classify_supersessions(spans, placeholders)
    if not drop:
        return spans
    out: list[dict] = []
    for s in spans:
        if (s.get('trace_id'), s.get('span_id')) in drop:
            continue
        ph = inherit.get(s.get('span_id'))
        out.append(_inherit_turn_linkage(s, ph) if ph is not None else s)
    return out


def _gate_resolved_tool_name(span: dict) -> str | None:
    """The tool_name whose permission gate this span resolves, or None: a
    non-pending `tool.<X>` means the gate was granted (the tool ran); a
    `permission.denied` means it was denied."""
    name = span.get('name') or ''
    if name.startswith('tool.') and span.get('status_code') != 'PENDING':
        return _attrs(span).get('tool_name') or name[len('tool.'):]
    if name == 'permission.denied':
        return _attrs(span).get('tool_name')
    return None


def _drop_resolved_permission_requests(spans: list[dict]) -> list[dict]:
    """Drop PENDING `permission.request` rows whose tool's gate resolved in the
    window. Claude Code's PermissionRequest payload carries no tool_use_id, so
    these can't be matched by deterministic id — correlate by `tool_name`.
    Permissions block the session one at a time, so dropping every pending
    request for the resolved tool_name is safe. (Replaces ingest's
    `_retire_pending_permissions`.)"""
    resolved: dict[str, set] = {}
    for s in spans:
        tool_name = _gate_resolved_tool_name(s)
        if tool_name:
            resolved.setdefault(s.get('trace_id'), set()).add(tool_name)
    if not resolved:
        return spans

    def retired(s: dict) -> bool:
        if s.get('name') != 'permission.request' or s.get('status_code') != 'PENDING':
            return False
        return _attrs(s).get('tool_name') in resolved.get(s.get('trace_id'), ())

    return [s for s in spans if not retired(s)]


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


def _is_expansion_anchor(candidate: dict, placeholder: dict, ph_text: str) -> bool:
    """True if `candidate` is a resolved slash-command anchor in the same trace
    whose `/command` echo the placeholder's text expands, and whose id is not
    below the placeholder's (the placeholder is minted just before its anchor)."""
    if candidate.get('trace_id') != placeholder.get('trace_id'):
        return False
    echo = _slash_echo_text(candidate)
    if echo is None or not ph_text.startswith(echo) or len(ph_text) <= len(echo):
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
    """Move a slash-command placeholder's full expansion onto its surviving
    resolved anchor, then drop the placeholder.

    A slash command (`/goal-verified`) yields TWO prompt rows: a resolved
    `prompt-<uuid>` anchor carrying only the collapsed echo, and a PENDING
    `promptlive-` placeholder carrying the full expansion (turn_uuid is NULL on
    both, so turn-pairing can't help). Left alone, `_drop_stale_blockers` would
    drop the placeholder once a later prompt lands and the expansion would be
    lost. Here we instead transfer the expansion onto a COPY of the resolved
    anchor (status stays OK, so PENDING-excluding aggregate readers still see
    it) and drop the placeholder — mirroring how `_inherit_turn_linkage`
    returns `dict(survivor)` copies to keep the merge pure. A stray client-only
    placeholder (`/workflows`) has no such anchor and is left untouched for the
    existing stale-drop path."""
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
            survivor['attributes'] = {**_attrs(s), 'text': expansion_by_anchor[sig]}
            out.append(survivor)
        else:
            out.append(s)
    return out


_STALE_PENDING_NAMES = ('permission.request', 'prompt')


def _is_stale_pending_name(name: str) -> bool:
    return name.startswith('tool.') or name in _STALE_PENDING_NAMES


def _drop_stale_blockers(spans: list[dict], prompt_id_ceiling=None) -> list[dict]:
    """Drop stale PENDING rows superseded by a newer prompt — anything PENDING
    from a prior turn that the user implicitly abandoned by submitting again.
    Keyed on the monotonic `id` (not start_time: anchors are tz-aware,
    placeholders naive). Covers two cases:

      * an *interrupted* blocking tool / permission (AskUserQuestion,
        ExitPlanMode, a permission gate) that never resolved — replaces
        ingest's `_sweep_superseded_pending_blockers`;
      * a stray `promptlive-` prompt placeholder for a client-only command
        (`/workflows`, `/clear`) that never produced a model turn, so no real
        `prompt-<uuid>` anchor ever supersedes it — replaces the
        `reconcile_prompt_spans` deletion (which kept the newest + image-owning
        prompts; real anchors are non-PENDING so they're never touched here).

    The cutoff is the newest prompt id. `prompt_id_ceiling` is the per-trace
    GLOBAL max prompt id, which a windowed reader (fetch_session_paginated)
    passes so a stray that is the newest prompt *within an older scroll-up
    window* still drops — the window-local max alone would wrongly keep it.
    Full-session readers pass None (window == whole session). The genuinely
    newest prompt session-wide equals the ceiling, so it is never < cutoff and
    is always kept."""
    window_max: dict[str, int] = {}
    for s in spans:
        if s.get('name') != 'prompt':
            continue
        sid = s.get('id')
        if sid is None:
            continue
        tid = s.get('trace_id')
        if sid > window_max.get(tid, -1):
            window_max[tid] = sid
    if not window_max and prompt_id_ceiling is None:
        return spans

    def cutoff_for(tid) -> int | None:
        wm = window_max.get(tid)
        if prompt_id_ceiling is None:
            return wm
        return prompt_id_ceiling if wm is None else max(wm, prompt_id_ceiling)

    def stale(s: dict) -> bool:
        if s.get('status_code') != 'PENDING':
            return False
        if not _is_stale_pending_name(s.get('name') or ''):
            return False
        sid = s.get('id')
        cutoff = cutoff_for(s.get('trace_id'))
        return sid is not None and cutoff is not None and sid < cutoff

    return [s for s in spans if not stale(s)]


def merge_spans(raw: list[dict], prompt_id_ceiling=None) -> list[dict]:
    """Reconcile one window of append-only rows into the canonical span list.

    Pure: returns a new list, mutates neither `raw` nor the DB. Dedup runs
    first (so superseded placeholders can't open phantom turns), then a
    slash-command rescue (`_absorb_slash_command_expansions` moves an
    expansion onto its resolved echo before the stale sweep could drop it),
    then the deterministic reparent ladder. Drop-in replacement for
    `_graft_orphans` at the read path — identical over already-reconciled
    windows.

    `prompt_id_ceiling` is the per-trace GLOBAL max prompt id; a windowed
    reader passes it so stray prompt placeholders drop even in an older
    scroll-up window (see `_drop_stale_blockers`). Whole-session readers omit
    it."""
    if not raw:
        return raw
    spans = _drop_superseded_placeholders(raw)
    spans = _drop_resolved_permission_requests(spans)
    spans = _absorb_slash_command_expansions(spans)
    spans = _drop_stale_blockers(spans, prompt_id_ceiling=prompt_id_ceiling)
    return _graft_orphans(spans)
