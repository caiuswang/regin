"""Handler: refresh the trace-derived wiki 'read' signal at SessionEnd.

The exposure signal is bumped live in `index_fetch`, but the stronger 'read'
signal (the agent actually opened a wiki) is derived from `tool.Read` spans and
would otherwise only refresh on a manual `regin topics wiki-stats --sync`. This
recomputes it once per session end so it stays current.

Cheap and safe to run every time: the sync is a single prefiltered span scan,
and being an idempotent full recompute (SET, not increment) a redundant run is a
no-op. Best-effort — any failure is swallowed so it never disturbs session end.
"""

from __future__ import annotations

from ..core import HookPayload, HookResponse


def handle_end(payload: HookPayload) -> HookResponse | None:
    try:
        import lib.memory as memory
        if memory.enabled():
            from lib.memory.wiki_reads import sync_wiki_reads
            sync_wiki_reads()
    except Exception:
        pass
    return None
