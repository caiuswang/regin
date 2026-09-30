"""Serve-time handling of stale PENDING blockers: drop the ones a later row
resolved, and demote the ones the session has moved on from."""

from __future__ import annotations

from datetime import datetime

from lib.trace.pending_spans import (
    INACTIVE_THRESHOLD_SEC,
    is_agent_scoped_prompt,
    is_human_blocker,
    parse_naive_ts as _merge_ts,
)
from lib.trace.merge_anchors import _attrs, _is_live_prompt_placeholder


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
        if s.get('name') != 'prompt' or is_agent_scoped_prompt(s):
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


# A stuck PENDING that the stale-blocker sweep can't reach — it is NEWER than
# the last prompt, so no later prompt supersedes it — is instead demoted to a
# resolved-interrupted rendering here. `_STALE_PENDING_OLDER_THAN_SEC` guards
# the session-inactive path so a tool that only just started on a
# just-abandoned session isn't prematurely called dead. `INACTIVE_THRESHOLD`
# mirrors the roster stale window (web/blueprints/trace/sessions/roster.py).
INTERRUPT_SOURCE_STALE = 'stale'


_STALE_PENDING_OLDER_THAN_SEC = 60


# Rule (a) grace: a pending is only demoted-as-interrupted once the same agent
# has demonstrably moved on for this long. Set ABOVE the 60s transcript
# backfill gate (repair.has_stuck_pending_tools) so a merely-lost PostToolUse
# always gets a shot at being superseded by its true OK span before we render
# it "⏹ interrupted".
_MOVED_ON_GRACE_SEC = 90


def _is_demotable_pending(span: dict) -> bool:
    """A PENDING tool / permission-request / ask placeholder that could be a
    stuck blocker. Prompt placeholders are excluded from the clock rules —
    they retire via `_drop_stale_blockers` / `_absorb_slash_command_expansions`,
    plus the event-based `session.end` ceiling in `_demote_stale_pending`."""
    if span.get('status_code') != 'PENDING':
        return False
    name = span.get('name') or ''
    return name.startswith('tool.') or name == 'permission.request'


def _latest_move_on_ts(spans: list[dict]) -> dict:
    """`(trace_id, agent_id) → latest assistant_response/prompt start_time`
    (naive local) in ONE pass, so `_moved_on_past_grace` is an O(1) lookup
    rather than a rescan of the whole window per pending span."""
    out: dict = {}
    for s in spans:
        if s.get('name') not in ('assistant_response', 'prompt'):
            continue
        ts = _merge_ts(s.get('start_time'))
        if ts is None:
            continue
        key = (s.get('trace_id'), _attrs(s).get('agent_id'))
        prev = out.get(key)
        if prev is None or ts > prev:
            out[key] = ts
    return out


def _latest_span_ts(spans: list[dict]) -> dict:
    """`(trace_id, agent_id) → latest start_time of ANY span` (naive local).

    The evidence `_blocks_on_a_human` weighs newest-ness against. Same key as
    `_latest_move_on_ts`, so a subagent running beside a parked main loop
    never counts as the main loop having moved.
    """
    out: dict = {}
    for s in spans:
        ts = _merge_ts(s.get('start_time'))
        if ts is None:
            continue
        key = (s.get('trace_id'), _attrs(s).get('agent_id'))
        prev = out.get(key)
        if prev is None or ts > prev:
            out[key] = ts
    return out


def _moved_on_past_grace(pending: dict, move_on_ts: dict) -> bool:
    """True when a same-agent `assistant_response`/`prompt` started at least
    `_MOVED_ON_GRACE_SEC` after the pending — Claude demonstrably moved past the
    blocking call AND enough time has elapsed that a lost PostToolUse would have
    been backfilled with the true OK span (rule a). The grace keeps a merely-
    lost result from being mislabeled '⏹ interrupted' seconds after the next
    response lands."""
    p_ts = _merge_ts(pending.get('start_time'))
    if p_ts is None:
        return False
    key = (pending.get('trace_id'), _attrs(pending).get('agent_id'))
    newer = move_on_ts.get(key)
    return newer is not None and (newer - p_ts).total_seconds() >= _MOVED_ON_GRACE_SEC


def _session_is_inactive(session_activity: dict | None, now: datetime) -> bool:
    if not session_activity:
        return False
    if session_activity.get('status') == 'ended':
        return True
    last_seen = _merge_ts(session_activity.get('last_seen'))
    if last_seen is None:
        return False
    return (now - last_seen).total_seconds() > INACTIVE_THRESHOLD_SEC


def _blocks_on_a_human(pending: dict, session_activity: dict | None,
                       latest_span_ts: dict) -> bool:
    """True for an unresolved permission/ask/plan that is the newest thing its
    own agent did on a still-open session — the state rule (b)'s clock cannot
    see.

    Such a span is why the session went quiet, so its own age is not evidence
    it is stuck; it is the length of the wait. A stuck `tool.Bash` gets no such
    exemption — nothing about it explains the silence.

    Newest-ness is read off the SPAN stream, never `sessions.last_seen`: that
    column is a session heartbeat every ingest writer bumps — a transcript
    rescan, a backfill landing rows stamped in the past — so it overtook a live
    park within two minutes of it being raised and demoted a question the agent
    was still blocked on to '⏹ interrupted', leaving `/live` with no answer
    sheet for an agent that never moved.
    """
    if not session_activity or session_activity.get('status') == 'ended':
        return False
    if not is_human_blocker(pending.get('name'), pending.get('span_id')):
        return False
    p_ts = _merge_ts(pending.get('start_time'))
    newest = latest_span_ts.get(
        (pending.get('trace_id'), _attrs(pending).get('agent_id')))
    return p_ts is not None and (newest is None or p_ts >= newest)


def _demote(pending: dict) -> dict:
    """Copy of the pending rendered as resolved-interrupted (status ERROR +
    interrupt markers the frontend already reads). Merge stays pure — a copy,
    never a mutation."""
    out = dict(pending)
    out['status_code'] = 'ERROR'
    out['attributes'] = {
        **_attrs(pending),
        'is_interrupt': True,
        'interrupted': True,
        'interrupt_source': INTERRUPT_SOURCE_STALE,
    }
    return out


def _demote_stale_pending(
    spans: list[dict], session_activity: dict | None = None,
) -> list[dict]:
    """Demote stuck PENDING tool/permission/ask placeholders to a
    resolved-interrupted rendering (see `merge_spans`). Two triggers:

      (a) a same-agent newer `assistant_response`/`prompt` started at least
          `_MOVED_ON_GRACE_SEC` after the pending — Claude moved on and the
          backfill has had its shot, OR
      (b) the session is inactive (ended, or silent past INACTIVE_THRESHOLD)
          and the pending is older than `_STALE_PENDING_OLDER_THAN_SEC` —
          EXCEPT for a human blocker that is the session's newest span on a
          still-open session (`_blocks_on_a_human`), whose age measures the
          wait, not a fault, OR
      (c) its agent never ran (`session_activity['never_ran']`, read off the
          whole store by `pending_spans.never_ran_blocker_agents`): a side
          fork's placeholder that no one can ever answer.

    INVARIANT: a legitimately-running long tool on an ACTIVE session with no
    same-agent completion activity after it is never demoted — (a) needs a
    later same-agent move-on past the grace, (b) needs an inactive session.

    A `promptlive-` placeholder gets one extra, event-based rule: a
    `session.end` row with a later monotonic id proves the session ended
    after the submission, so the prompt can never resolve — it demotes
    regardless of any clock. This is the only path that resolves a stuck
    in-flight prompt whose delivery echo never arrived (the run died between
    submit and echo); the last prompt of a live session is untouched."""
    end_ceiling = _session_end_ceiling(spans)
    if not end_ceiling and not any(_is_demotable_pending(s) for s in spans):
        return spans
    now = (session_activity or {}).get('now') or datetime.now()
    inactive = _session_is_inactive(session_activity, now)
    move_on_ts = _latest_move_on_ts(spans)
    latest_span_ts = _latest_span_ts(spans)

    never_ran = (session_activity or {}).get('never_ran') or set()

    def demote(s: dict) -> bool:
        if _moved_on_past_grace(s, move_on_ts):
            return True
        if (s.get('trace_id'), _attrs(s).get('agent_id')) in never_ran:
            return True
        if not inactive or _blocks_on_a_human(s, session_activity,
                                              latest_span_ts):
            return False
        p_ts = _merge_ts(s.get('start_time'))
        return p_ts is not None and \
            (now - p_ts).total_seconds() > _STALE_PENDING_OLDER_THAN_SEC

    def undelivered(s: dict) -> bool:
        if not _is_live_prompt_placeholder(s) or s.get('id') is None:
            return False
        return s['id'] < end_ceiling.get(s.get('trace_id'), -1)

    return [
        _demote(s) if (_is_demotable_pending(s) and demote(s))
        or undelivered(s) else s
        for s in spans
    ]


def _session_end_ceiling(spans: list[dict]) -> dict:
    """Per-trace max id of `session.end` rows — the event that outdates any
    still-pending prompt submitted before it."""
    out: dict = {}
    for s in spans:
        if s.get('name') != 'session.end' or s.get('id') is None:
            continue
        tid = s.get('trace_id')
        out[tid] = max(out.get(tid, -1), s['id'])
    return out
