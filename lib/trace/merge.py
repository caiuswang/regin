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

from lib.trace.pending_spans import pending_id_for_resolved
from lib.trace.projection import _graft_orphans


def _attrs(span: dict) -> dict:
    a = span.get('attributes')
    return a if isinstance(a, dict) else {}


def _drop_superseded_placeholders(spans: list[dict]) -> list[dict]:
    """Drop the `promptlive-`/`pending-`/`permreq-` placeholder rows whose
    resolved span is present in the window, keyed by `pending_id_for_resolved`
    (prompt-text hash / tool_use_id). A placeholder with no resolved
    counterpart in the window survives — that's the in-flight prompt / blocking
    tool the live view must still show."""
    drop: set[tuple] = set()
    for s in spans:
        for pid in pending_id_for_resolved(s, _attrs(s)):
            drop.add((s.get('trace_id'), pid))
    if not drop:
        return spans
    return [s for s in spans
            if (s.get('trace_id'), s.get('span_id')) not in drop]


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
    first (so superseded placeholders can't open phantom turns), then the
    deterministic reparent ladder. Drop-in replacement for `_graft_orphans`
    at the read path — identical over already-reconciled windows.

    `prompt_id_ceiling` is the per-trace GLOBAL max prompt id; a windowed
    reader passes it so stray prompt placeholders drop even in an older
    scroll-up window (see `_drop_stale_blockers`). Whole-session readers omit
    it."""
    if not raw:
        return raw
    spans = _drop_superseded_placeholders(raw)
    spans = _drop_resolved_permission_requests(spans)
    spans = _drop_stale_blockers(spans, prompt_id_ceiling=prompt_id_ceiling)
    return _graft_orphans(spans)
