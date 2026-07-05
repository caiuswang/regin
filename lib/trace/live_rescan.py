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
from pathlib import Path

_running: set = set()
_lock = threading.Lock()
# path -> last-seen mtime, so an idle/ended session's poll skips the
# transcript re-read entirely (a cheap stat instead of a parse every 4s).
_last_mtime: dict = {}
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


def _find_main_transcript(trace_id: str) -> str | None:
    """The session's own transcript, found by trace_id across project dirs
    (avoids reconstructing Claude Code's cwd→dir encoding)."""
    matches = glob.glob(
        str(Path.home() / '.claude' / 'projects' / '*' / f'{trace_id}.jsonl')
    )
    return matches[0] if matches else None


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

        if _file_changed(main):
            main_state = ingest_transcript_usage_resumable(
                trace_id, main, main_state,
            )
        # Subagents: each writes its own transcript under <session>/subagents/.
        sub_glob = str(Path(main).with_suffix('') / 'subagents' / 'agent-*.jsonl')
        for path in glob.glob(sub_glob):
            if not _file_changed(path):
                continue
            name = os.path.basename(path)
            agent_id = name[len('agent-'):-len('.jsonl')]
            subs[path] = emit_subagent_responses_resumable(
                trace_id, path, agent_id, subs.get(path), seen=_load_seen(trace_id),
            )

        # Self-heal ghost agents: agent_id-tagged spans with no
        # subagent.start (markers lost to an ingest outage) get their
        # markers reconstructed from the on-disk transcripts. Gated on one
        # cheap EXISTS query; the reconstruction itself is idempotent
        # (deterministic substart-sa-* span ids). Own guard so a heal
        # failure can't lose the scan states below.
        try:
            from lib.trace.repair import has_ghost_agents, reconstruct_subagent_markers
            if has_ghost_agents(trace_id):
                reconstruct_subagent_markers(trace_id)
        except Exception:
            pass

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


def trigger_rescan(trace_id: str) -> None:
    """Fire-and-forget background rescan for a viewed session. No-op (returns
    immediately) if one is already running for this trace, so the 4 s poll
    can't pile up scans. The freshly-ingested turns appear on the next poll."""
    if not trace_id:
        return
    with _lock:
        if trace_id in _running:
            return
        _running.add(trace_id)
    threading.Thread(target=_do_rescan, args=(trace_id,), daemon=True).start()
