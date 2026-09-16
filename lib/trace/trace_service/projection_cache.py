"""Writer-invalidated memo for the trace view's span-derived reads.

The trace view re-asks for the same session every few seconds, and the two
expensive halves of that answer — the paginated projection and the roster's
`json_extract` scan — are pure functions of the stored rows. Memoising them
turns a ~130ms projection into a dict lookup.

**Invalidation is driven by the writer, not by a read-side fingerprint.**
A re-ingest UPDATEs a span in place, preserving its row id (see
`_insert_span_row`), so no cheap read-side aggregate — `MAX(id)`,
`COUNT(*)`, `last_seen` — can see an assistant response growing under a
stable row set. `_insert_span_row` and `_attribute_one_call` therefore call
`invalidate()` with the traces they touched; that is exact by construction.

`fingerprint()` still forms part of each key, as defence in depth against a
writer this process does not run (a `regin rebuild`, a repair): it catches
any change in row count or id range, and it discriminates page windows.

Deliberately NOT memoised, because none of it is a function of the stored
rows: the roster's clock-dependent `stale` verdict (computed on the memoised
rows, so it stays live), the tmux screen read, the transcript-derived queued
prompts, and bridge reachability.
"""

from __future__ import annotations

from collections import OrderedDict
from threading import Lock

from lib.trace.alias import sql_in

# A handful of open trace tabs is the realistic ceiling, and each entry holds
# one page's structural half, so this stays well under a megabyte.
_MAX_ENTRIES = 32

# Epochs outlive their cache entries — a viewer's tally has to stay meaningful
# across the LRU turning over — so they get their own, much larger, bound.
# One small int per trace written since boot; without a cap a long-lived
# `regin serve` accumulates one per trace, forever.
_MAX_EPOCHS = 4096

_cache: OrderedDict = OrderedDict()
# trace_id -> keys filed under it, so a write invalidates by session without
# walking the whole cache. An alias group files its entry under every member:
# spans land under a run's `sdk-` id while the reader is keyed on the
# canonical one, and either id must drop the other's cached answer.
_by_trace: dict[str, set] = {}
# trace_id -> how many times its spans have changed. Handed to the browser so
# it can tell a stream that delivered every change from one that dropped a
# frame: the push fan-out sheds a queue's head under load and re-sends
# nothing (`hub._offer`), so a client trusting it blindly would keep a stale
# body forever. A gap in this counter is the client's cue to re-ask for
# everything. In memory on purpose — a server restart reads as a gap, which is
# exactly the right answer for a client whose peer forgot what it had sent.
_epoch: OrderedDict = OrderedDict()
_lock = Lock()


def fingerprint(conn, group) -> tuple:
    """Row-set identity for one session, as a comparable tuple.

    Cheap (one indexed aggregate per table) and deliberately coarse: this is
    the secondary guard, so it only has to catch rows appearing or vanishing
    under a writer that did not call `invalidate()`. `sessions` rides along
    because the merge's stuck-pending demotion reads status/last_seen, and
    `turn_usage` because `_attach_compaction_reclaim` brackets compaction
    boundaries with its rows.

    The database file is part of it: the rest is per-session counters, which
    two different test fixtures can easily collide on.
    """
    placeholders, params = sql_in(group)
    spans = conn.execute(
        f"SELECT MAX(id), COUNT(*) FROM session_spans "
        f"WHERE trace_id IN {placeholders}",
        params).fetchone()
    activity = conn.execute(
        f"SELECT status, last_seen FROM sessions WHERE trace_id IN {placeholders} "
        "ORDER BY last_seen DESC LIMIT 1",
        params).fetchone()
    turns = conn.execute(
        f"SELECT MAX(rowid), COUNT(*) FROM turn_usage "
        f"WHERE trace_id IN {placeholders}",
        params).fetchone()
    db_file = conn.execute("PRAGMA database_list").fetchone()[2]
    return (db_file, tuple(spans),
            tuple(activity) if activity else None, tuple(turns))


def get(key):
    with _lock:
        hit = _cache.get(key)
        if hit is not None:
            _cache.move_to_end(key)
        return hit


def put(key, value, traces) -> None:
    """Cache `value` under `key`, filed against every trace id in `traces`."""
    with _lock:
        _cache[key] = value
        _cache.move_to_end(key)
        for trace_id in traces:
            _by_trace.setdefault(trace_id, set()).add(key)
        while len(_cache) > _MAX_ENTRIES:
            evicted, _ = _cache.popitem(last=False)
            _forget_key(evicted)


def invalidate(traces) -> None:
    """Drop everything cached for these trace ids and advance their epoch.

    Called from the write path. Accepts a single id or an iterable. The cache
    drop is a no-op for a trace nothing has read — the common case on a
    machine whose sessions nobody is watching — but the epoch advances either
    way, because a reader that arrives later still has to be able to tell how
    many changes it missed.
    """
    if isinstance(traces, str):
        traces = (traces,)
    with _lock:
        for trace_id in traces:
            _epoch[trace_id] = _epoch.get(trace_id, 0) + 1
            _epoch.move_to_end(trace_id)
            for key in _by_trace.pop(trace_id, ()):  # noqa: B020
                _cache.pop(key, None)
        # Evicting the least-recently-CHANGED trace, which is never one a
        # viewer is watching: a watched session is being written to, so it
        # stays at the hot end. An evicted trace restarts at 1, and a reader
        # still holding the old tally reads that as a mismatch and resyncs —
        # the safe direction, and it self-corrects on the very next tick.
        while len(_epoch) > _MAX_EPOCHS:
            _epoch.popitem(last=False)


def epoch(traces) -> int:
    """How many times these traces' spans have changed in this process.

    Summed over the alias group, and that is load-bearing rather than tidy:
    the writer invalidates under the RAW id it wrote (a run's spans land under
    its `sdk-` or child alias), while the reader is keyed on the canonical one.
    Reading a single id would leave a group's counter frozen at zero — and a
    frozen counter is precisely what tells a client its cached bodies are
    still good.
    """
    if isinstance(traces, str):
        traces = (traces,)
    with _lock:
        return sum(_epoch.get(t, 0) for t in traces)


def clear() -> None:
    with _lock:
        _cache.clear()
        _by_trace.clear()
        _epoch.clear()


def _forget_key(key) -> None:
    """Drop an evicted key from the per-trace index. Caller holds the lock."""
    for trace_id, keys in list(_by_trace.items()):
        keys.discard(key)
        if not keys:
            del _by_trace[trace_id]
