"""Backfill `memory_exemplars` from already-graded injection events.

The exemplar loop (`lib/memory/feedback.py`) records the firing prompt's
embedding going forward, but events scored before the `injection_events.query`
column existed have NULL queries — so they were never turned into exemplars.
This one-shot reconstructs each historical query from the trace: a graded event
is paired with the `prompt` span nearest its `injected_at`, mirrored through the
hook's `_recall_query` (slash-command stripping + 2000-char cap) so the
embedding matches what live recall would produce, then written via
`store.add_query_exemplars` (which embeds, dedups, and trims per polarity).

`--polarity negative` (default) backfills hard ignores (engaged=0, matched=0)
as demoting exemplars; `--polarity positive` backfills engaged injects
(engaged=1) as boosting ones.

    .venv/bin/python scripts/backfill_exemplars.py --days 7 --dry-run
    .venv/bin/python scripts/backfill_exemplars.py --days 7 --polarity positive

Idempotent enough for repeated runs: re-running re-writes the same exemplars and
the per-(memory, polarity) cap trims duplicates, but prefer a single real pass.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timedelta

from sqlmodel import select

import lib.memory as memory
from hook_manager.handlers.memory_recall import _recall_query
from lib.memory.engine import MemorySessionLocal
from lib.memory.models import InjectionEvent
from lib.orm.engine import get_connection


def _parse_ts(ts: str):
    try:
        return datetime.fromisoformat(ts)
    except (TypeError, ValueError):
        return None


def _events_by_session(cutoff: str, polarity: int) -> dict[str, list[tuple[str, str]]]:
    """{session_id: [(memory_id, injected_at), …]} for graded events of the
    requested polarity since `cutoff` whose query was never recorded. Negative
    = hard ignore (engaged=0, matched=0); positive = engaged (engaged=1)."""
    out: dict[str, list[tuple[str, str]]] = {}
    with MemorySessionLocal() as s:
        stmt = select(InjectionEvent.session_id, InjectionEvent.memory_id,
                      InjectionEvent.injected_at).where(
            InjectionEvent.injected_at >= cutoff,
            InjectionEvent.query.is_(None))
        if polarity > 0:
            stmt = stmt.where(InjectionEvent.engaged == 1)
        else:
            stmt = stmt.where(InjectionEvent.engaged == 0,
                              InjectionEvent.matched == 0)
        rows = s.exec(stmt).all()
    for sid, mid, at in rows:
        out.setdefault(sid, []).append((mid, at))
    return out


def _prompt_spans(trace_id: str) -> list[tuple[datetime, str]]:
    """(start_time, prompt_text) for a session's `prompt` spans, oldest first."""
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT start_time, attributes FROM session_spans "
            "WHERE trace_id = ? AND name = 'prompt' "
            "AND status_code != 'PENDING' ORDER BY start_time ASC",
            (trace_id,)).fetchall()
    finally:
        conn.close()
    out = []
    for r in rows:
        t = _parse_ts(r["start_time"])
        if t is None:
            continue
        try:
            text = (json.loads(r["attributes"] or "{}") or {}).get("text") or ""
        except (json.JSONDecodeError, ValueError):
            text = ""
        if text:
            out.append((t, text))
    return out


def _nearest_query(prompts: list[tuple[datetime, str]], injected_at: str):
    """The `_recall_query`-normalised text of the prompt nearest `injected_at`,
    or None when the session has no usable prompt span."""
    at = _parse_ts(injected_at)
    if at is None or not prompts:
        return None
    _, text = min(prompts, key=lambda p: abs((p[0] - at).total_seconds()))
    q = _recall_query(text)[:2000].strip()
    return q or None


def _reconstruct(by_session: dict) -> tuple[dict, int, int]:
    """(items_by_session, matched, no_prompt): pair each event with the
    `_recall_query`-normalised text of its nearest prompt span."""
    matched, no_prompt, items_by_session = 0, 0, {}
    for sid, events in by_session.items():
        prompts = _prompt_spans(sid)
        items = []
        for mid, at in events:
            q = _nearest_query(prompts, at)
            if q is None:
                no_prompt += 1
                continue
            items.append((mid, q))
            matched += 1
        if items:
            items_by_session[sid] = items
    return items_by_session, matched, no_prompt


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument("--polarity", choices=["positive", "negative"],
                    default="negative")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    polarity = 1 if args.polarity == "positive" else -1

    store = memory.get_store()
    if getattr(store._embedder, "model_id", None) is None:
        raise SystemExit("no embedder available — cannot embed queries")

    cutoff = (datetime.now() - timedelta(days=args.days)).isoformat()
    by_session = _events_by_session(cutoff, polarity)
    n_events = sum(len(v) for v in by_session.values())
    print(f"{args.polarity} events (last {args.days}d, query=NULL): "
          f"{n_events} across {len(by_session)} sessions")

    items_by_session, matched, no_prompt = _reconstruct(by_session)
    distinct_mems = len({mid for items in items_by_session.values()
                         for mid, _ in items})
    print(f"reconstructed query for {matched} events "
          f"({no_prompt} had no usable prompt span); "
          f"{distinct_mems} distinct memories")

    if args.dry_run:
        for sid, items in list(items_by_session.items())[:5]:
            mid, q = items[0]
            print(f"  e.g. {sid[:8]} {mid[:8]} <- {q[:80]!r}")
        print("dry-run: no writes")
        return

    written = sum(store.add_query_exemplars(sid, items, polarity, source="auto")
                  for sid, items in items_by_session.items())
    print(f"wrote {written} {args.polarity} exemplars (after per-memory cap)")


if __name__ == "__main__":
    main()
