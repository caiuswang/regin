"""Server-side live transcript rescan, triggered by the trace view's poll.

Assistant text/thinking has no Claude Code hook — it lands only in the
transcript file, which the hooks scan only when a hook fires (PostToolUse /
Stop / SubagentStop). During a long agentic turn (or a running subagent) the
file is flushed between hook events, so the text waits for the next hook —
seconds for the main agent, until SubagentStop for a subagent.

This closes that gap: while someone is viewing a session, the `/map?shallow`
poll fires a fire-and-forget rescan that re-reads the main transcript (and any
subagent transcripts) and ingests turns the hook scan hasn't posted yet, so
they appear within a poll or two. It reuses the exact hook emission (which
posts to localhost), gated by the per-session seen-uuid cache so a steady
session costs one transcript read and zero posts. Deduped per trace so
overlapping polls don't pile up threads.
"""

from __future__ import annotations

import glob
import os
import threading
import time
from pathlib import Path

_running: set = set()
_lock = threading.Lock()
# path -> last-seen mtime, so an idle/ended session's poll skips the
# transcript re-read entirely (a cheap stat instead of a parse every 4s).
_last_mtime: dict = {}
# trace_id -> (main-transcript mtime, monotonic wall) of the last COMPLETED
# rescan. `trigger_rescan` skips spawning a thread when the file hasn't changed
# since that rescan AND we ran one within `_MIN_RESCAN_INTERVAL_SEC` — so a
# multi-viewer idle session doesn't pay a thread + roster/gate queries on every
# 4s poll. A changed file, a never-scanned trace, or one past the interval is
# never skipped.
_rescan_gate: dict = {}
_MIN_RESCAN_INTERVAL_SEC = 10.0
# Only walk the transcript for lost tool spans when a PENDING placeholder has
# been stuck this long — generous enough that a live PostToolUse (which posts
# within ~seconds) is never raced into a duplicate span; a genuinely lost span
# is stuck far longer.
_BACKFILL_PENDING_AGE_SEC = 60
# trace_id -> ResumableScanState for the main transcript. Persists the scan
# accumulator + committed byte offset across polls so a changed file costs
# O(new bytes) to parse, not O(file size). See lib/trace/transcript_usage.py.
_scan_states: dict = {}
# trace_id -> {subagent_path: ResumableScanState}. Same idea, per subagent
# transcript (each subagent writes its own jsonl under <session>/subagents/).
_sub_scan_states: dict = {}
# LRU cap on the live accumulator maps. The SessionEnd hook fires in a
# separate subprocess and can't reach this server-process state, so there's
# no clean session-end signal to evict on; instead we bound the set to the
# most-recently-rescanned traces. A dropped-but-still-live session just
# re-parses once on its next poll (offset 0) and then resumes incrementally.
_MAX_TRACKED = 32


def _bound_tracked() -> None:
    """Evict least-recently-rescanned traces beyond `_MAX_TRACKED`. Dicts keep
    insertion order and `_do_rescan` re-inserts each touched trace at the end,
    so `next(iter(...))` is the oldest."""
    for store in (_scan_states, _sub_scan_states):
        while len(store) > _MAX_TRACKED:
            store.pop(next(iter(store)), None)


def _file_changed(path: str) -> bool:
    """True (and records the new mtime) when `path` changed since the last
    scan. A file regin can't stat is treated as unchanged (skip)."""
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        return False
    if _last_mtime.get(path) == mtime:
        return False
    _last_mtime[path] = mtime
    return True


def _subagent_glob(main_path: str) -> str:
    """Glob for a session's subagent transcripts (`<session>/subagents/
    agent-*.jsonl`). Shared by the rescan (which parses them) and the throttle
    gate (which stats them for freshness)."""
    return str(Path(main_path).with_suffix('') / 'subagents' / 'agent-*.jsonl')


def _max_transcript_mtime(main_path: str) -> float | None:
    """Newest mtime across the main transcript AND every subagent transcript.
    The gate compares this (not the main mtime alone) so a subagent streaming
    while the main file is static — the agent blocked on an Agent tool — still
    defeats the throttle and gets rescanned. Cheap: one stat per small glob."""
    newest: float | None = None
    for path in (main_path, *glob.glob(_subagent_glob(main_path))):
        try:
            mtime = os.path.getmtime(path)
        except OSError:
            continue
        if newest is None or mtime > newest:
            newest = mtime
    return newest


def _find_main_transcript(trace_id: str) -> str | None:
    """The session's own transcript, found by trace_id across project dirs
    (avoids reconstructing Claude Code's cwd→dir encoding)."""
    matches = glob.glob(
        str(Path.home() / '.claude' / 'projects' / '*' / f'{trace_id}.jsonl')
    )
    return matches[0] if matches else None


def _selfheal_ghost_agents(trace_id: str) -> None:
    """Reconstruct subagent.start/stop markers lost to an ingest outage:
    agent_id-tagged spans with no start marker. Gated on one cheap EXISTS; the
    reconstruction is idempotent (deterministic substart-sa-* ids). Own guard so
    a heal failure can't lose the caller's scan states."""
    try:
        from lib.trace.repair import has_ghost_agents, reconstruct_subagent_markers
        if has_ghost_agents(trace_id):
            reconstruct_subagent_markers(trace_id)
    except Exception:
        pass


def _selfheal_lost_tool_spans(trace_id: str, main_changed: bool) -> None:
    """Recover tool.* spans (stuck pendings, lost TaskCreate/TaskUpdate,
    user-interrupted calls) the live PostToolUse path never posted. Only when
    the main transcript changed this poll AND a pending has been stuck past the
    age gate — a healthy session pays one cheap EXISTS, not a transcript walk.
    The backfill is idempotent and skips any tool_use lacking a transcript
    result, so it can't race a live post into a duplicate."""
    if not main_changed:
        return
    try:
        from lib.trace.repair import (
            backfill_transcript_tool_spans, has_stuck_pending_tools,
        )
        if has_stuck_pending_tools(trace_id, _BACKFILL_PENDING_AGE_SEC):
            backfill_transcript_tool_spans(trace_id)
    except Exception:
        pass


def _do_rescan(trace_id: str) -> None:
    try:
        main = _find_main_transcript(trace_id)
        if not main:
            return
        # Main agent: reuse turn_trace's scan (seen-cache gated, posts to self).
        # mtime gate skips the rescan when the file hasn't changed since the
        # last poll — idle/ended sessions cost a stat, not a parse. When it HAS
        # changed, the resumable scan parses only the appended bytes (state
        # carries the accumulator + committed offset across polls).
        from hook_manager.handlers.turn_trace.cache import _load_seen
        from hook_manager.handlers.turn_trace.entry import (
            ingest_transcript_usage_resumable,
        )
        from hook_manager.handlers.subagent_lifecycle import (
            emit_subagent_responses_resumable,
        )
        # Detach this trace's accumulators under the lock (heavy parsing runs
        # lock-free below); they're re-attached + LRU-bounded under the lock
        # at the end. Flask is threaded, so the dict structure mutations must
        # not race a concurrent trace's `_bound_tracked` iteration.
        with _lock:
            main_state = _scan_states.pop(trace_id, None)
            subs = _sub_scan_states.pop(trace_id, {})

        main_changed = _file_changed(main)
        if main_changed:
            main_state = ingest_transcript_usage_resumable(
                trace_id, main, main_state,
            )
        # Subagents: each writes its own transcript under <session>/subagents/.
        for path in glob.glob(_subagent_glob(main)):
            if not _file_changed(path):
                continue
            name = os.path.basename(path)
            agent_id = name[len('agent-'):-len('.jsonl')]
            subs[path] = emit_subagent_responses_resumable(
                trace_id, path, agent_id, subs.get(path), seen=_load_seen(trace_id),
            )

        _selfheal_ghost_agents(trace_id)
        _selfheal_lost_tool_spans(trace_id, main_changed)
        _record_rescan_gate(trace_id, main)

        with _lock:
            if main_state is not None:
                _scan_states[trace_id] = main_state
            if subs:
                _sub_scan_states[trace_id] = subs
            _bound_tracked()
    except Exception:
        pass
    finally:
        with _lock:
            _running.discard(trace_id)


def _record_rescan_gate(trace_id: str, main_path: str) -> None:
    """Stamp the newest transcript mtime (main + subagents) + wall-clock at
    rescan completion so `trigger_rescan` can throttle the next poll."""
    mtime = _max_transcript_mtime(main_path)
    if mtime is None:
        return
    _rescan_gate[trace_id] = (mtime, time.monotonic())


def _should_skip_rescan(trace_id: str, main_path: str) -> bool:
    """True when NEITHER the main nor any subagent transcript changed since the
    last completed rescan AND that rescan ran within `_MIN_RESCAN_INTERVAL_SEC`.
    A never-scanned trace, any changed file, or one past the interval is never
    skipped — so a fresh main turn OR a streaming subagent is never throttled
    away (gating on the main mtime alone staled subagent spans up to 10s while
    the main agent sat blocked on an Agent tool)."""
    prev = _rescan_gate.get(trace_id)
    if prev is None:
        return False
    prev_mtime, prev_wall = prev
    mtime = _max_transcript_mtime(main_path)
    if mtime is None or mtime != prev_mtime:
        return False
    return (time.monotonic() - prev_wall) < _MIN_RESCAN_INTERVAL_SEC


def trigger_rescan(trace_id: str) -> None:
    """Fire-and-forget background rescan for a viewed session. No-op (returns
    immediately) if one is already running for this trace, so the 4 s poll
    can't pile up scans. The freshly-ingested turns appear on the next poll.

    Also throttled: if the main transcript is unchanged since the last completed
    rescan and that rescan ran within `_MIN_RESCAN_INTERVAL_SEC`, skip spawning
    entirely — a multi-viewer idle session then costs one `stat`, not a thread +
    roster/gate queries, on every poll."""
    if not trace_id:
        return
    main = _find_main_transcript(trace_id)
    if main and _should_skip_rescan(trace_id, main):
        return
    with _lock:
        if trace_id in _running:
            return
        _running.add(trace_id)
    threading.Thread(target=_do_rescan, args=(trace_id,), daemon=True).start()
