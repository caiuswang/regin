"""Re-key the rows a `/clear` left in the wrong conversation.

An SDK run posted every conversation it drove to one `sdk-<hex>` trace, and
that trace unions with `agent_runs.cli_session_id` — the run's FIRST child —
so a cleared session absorbed every later conversation's spans while each heir
had no live stream. `lib.trace.alias.sdk_span_trace_id` fixed the write path;
this moves what was already written onto the segment it belongs to.

Which child a span belongs to is decided by identity, never by a clock:

  * a tool span by its `tool_use_id`, which the child's own hook trace also
    recorded,
  * a prompt span by its text, likewise,
  * anything else by the nearest preceding evidenced span in `id` order —
    `session_spans` is append-only, so `id` IS the order the runner wrote in.

A run whose evidence ever goes backwards is refused whole rather than guessed
at: the runner drives one conversation at a time, so interleaved evidence means
the join is wrong and no split is safe.

    .venv/bin/python scripts/backfill_clear_segments.py          # report
    .venv/bin/python scripts/backfill_clear_segments.py --apply  # write
"""

from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lib.orm.engine import get_connection  # noqa: E402
from lib.trace.alias import sdk_span_trace_id  # noqa: E402


class Refused(Exception):
    """The evidence does not support a safe split."""


def scope_of(run: str, children: list[str]) -> list[str]:
    """The run's own trace plus every segment it could already have been split
    into.

    Re-deriving the whole assignment across both is what makes a second pass a
    no-op rather than a half-repair: once the spans move, the run trace alone
    no longer holds the conversation being judged. Enumerated rather than
    matched with `LIKE` so every read stays on the `trace_id` index — the span
    table is large enough that one scan costs minutes.
    """
    return [run] + [sdk_span_trace_id(run, c) for c in children[1:]]


def runs_with_heirs(conn) -> list[tuple[str, list[str]]]:
    """`(run_id, [first_child, *heirs])` for every run that drove more than
    one conversation. Children in the order the runner first saw them."""
    rows = conn.execute("""
        SELECT s.trace_id AS run, s.cli_session_id AS child,
               r.cli_session_id AS first_child
        FROM agent_run_sessions s
        JOIN agent_runs r ON r.trace_id = s.trace_id
        WHERE r.cli_session_id IS NOT NULL
        ORDER BY s.id
    """).fetchall()
    seen: dict[str, list[str]] = defaultdict(list)
    first: dict[str, str] = {}
    for row in rows:
        seen[row["run"]].append(row["child"])
        first[row["run"]] = row["first_child"]
    out = []
    for run, children in seen.items():
        head = first[run]
        ordered = [head] + [c for c in children if c != head]
        if len(ordered) > 1:
            out.append((run, ordered))
    return sorted(out)


def _evidence(conn, scope: list[str], children: list[str]) -> dict[int, str]:
    """Span id → the child whose own hook trace names it."""
    marks: dict[int, str] = {}
    holder = ",".join("?" for _ in children)
    scoped = ",".join("?" for _ in scope)
    for row in conn.execute(f"""
        SELECT sp.id AS id, h.trace_id AS child
        FROM session_spans sp
        JOIN session_spans h
          ON h.tool_use_id = json_extract(sp.attributes, '$.tool_use_id')
         AND h.trace_id IN ({holder})
        WHERE sp.trace_id IN ({scoped})
          AND json_extract(sp.attributes, '$.tool_use_id') IS NOT NULL
    """, (*children, *scope)):
        marks[row["id"]] = row["child"]
    for row in conn.execute(f"""
        SELECT sp.id AS id, h.trace_id AS child
        FROM session_spans sp
        JOIN session_spans h
          ON h.name = 'prompt'
         AND json_extract(h.attributes, '$.text')
             = json_extract(sp.attributes, '$.text')
         AND h.trace_id IN ({holder})
        WHERE sp.trace_id IN ({scoped})
          AND sp.name = 'prompt'
          AND json_extract(sp.attributes, '$.text') IS NOT NULL
    """, (*children, *scope)):
        marks.setdefault(row["id"], row["child"])
    return marks


def assign(conn, run: str, children: list[str]) -> dict[str, list[int]]:
    """Child → the span ids of the conversation it held.

    Raises `Refused` when the evidence is not monotonic in write order.
    """
    scope = scope_of(run, children)
    scoped = ",".join("?" for _ in scope)
    marks = _evidence(conn, scope, children)
    rank = {child: i for i, child in enumerate(children)}
    held: dict[str, list[int]] = defaultdict(list)
    current = children[0]
    for row in conn.execute(
            f"SELECT id FROM session_spans WHERE trace_id IN ({scoped})"
            " ORDER BY id", scope):
        mark = marks.get(row["id"])
        if mark is not None:
            if rank[mark] < rank[current]:
                raise Refused(
                    f"span {row['id']} names {mark[:8]} after {current[:8]}")
            current = mark
        held[current].append(row["id"])
    return held


def _turns(conn, scope: list[str], span_ids: list[int]) -> list[str]:
    """The turn_uuids whose every span is in `span_ids`.

    A turn belongs to one conversation, so a turn split across the boundary
    would mean the assignment is wrong; such a turn is left where it is.
    """
    owned = set(span_ids)
    scoped = ",".join("?" for _ in scope)
    by_turn: dict[str, set[int]] = defaultdict(set)
    for row in conn.execute(f"""
        SELECT id, json_extract(attributes, '$.turn_uuid') AS turn
        FROM session_spans
        WHERE trace_id IN ({scoped})
          AND json_extract(attributes, '$.turn_uuid') IS NOT NULL
    """, scope):
        by_turn[row["turn"]].add(row["id"])
    return [turn for turn, ids in by_turn.items() if ids <= owned]


def move(conn, run: str, children: list[str], child: str,
         span_ids: list[int]) -> dict[str, int]:
    """Re-key one heir's rows onto its segment trace. Idempotent."""
    segment = sdk_span_trace_id(run, child)
    holder = ",".join("?" for _ in span_ids)
    # Read before the write: both of these are keyed off the spans, and after
    # the re-key the run trace no longer holds them.
    span_uids = [r["span_id"] for r in conn.execute(
        f"SELECT span_id FROM session_spans WHERE id IN ({holder})", span_ids)]
    turns = _turns(conn, scope_of(run, children), span_ids)
    spans = conn.execute(
        f"""UPDATE session_spans SET trace_id = ?
            WHERE id IN ({holder}) AND trace_id <> ?""",
        (segment, *span_ids, segment)).rowcount
    uid_holder = ",".join("?" for _ in span_uids)
    mapped = conn.execute(
        f"""UPDATE session_trace_map SET trace_id = ?
            WHERE trace_id = ? AND span_id IN ({uid_holder})""",
        (segment, run, *span_uids)).rowcount if span_uids else 0
    turn_holder = ",".join("?" for _ in turns)
    usage = conn.execute(
        f"""UPDATE turn_usage SET trace_id = ?
            WHERE trace_id = ? AND turn_uuid IN ({turn_holder})""",
        (segment, run, *turns)).rowcount if turns else 0
    return {'segment': segment, 'spans': spans,
            'trace_map': mapped, 'turn_usage': usage}


def refresh(conn, trace_ids: list[str]) -> None:
    """Re-derive the aggregates the session list reads for each conversation."""
    from lib.trace.trace_service.ingest import (
        _refresh_active_work_ms, _refresh_group_span_count,
    )

    for trace_id in trace_ids:
        _refresh_group_span_count(conn, trace_id)
    _refresh_active_work_ms(conn, trace_ids)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true",
                        help="write; otherwise report what would move")
    args = parser.parse_args()

    conn = get_connection()
    moved_any = False
    try:
        for run, children in runs_with_heirs(conn):
            try:
                held = assign(conn, run, children)
            except Refused as exc:
                print(f"{run}  REFUSED — {exc}")
                continue
            print(f"{run}  first={children[0][:8]} "
                  f"keeps {len(held.get(children[0], []))} span(s)")
            for child in children[1:]:
                span_ids = held.get(child, [])
                if not span_ids:
                    print(f"    {child[:8]}  nothing to move")
                    continue
                moved_any = True
                if not args.apply:
                    print(f"    {child[:8]}  would move {len(span_ids)} span(s)"
                          f" → {sdk_span_trace_id(run, child)}")
                    continue
                done = move(conn, run, children, child, span_ids)
                print(f"    {child[:8]}  moved {done['spans']} span(s), "
                      f"{done['trace_map']} map row(s), "
                      f"{done['turn_usage']} usage row(s) → {done['segment']}")
            if args.apply:
                refresh(conn, children)
        if args.apply:
            conn.commit()
        elif moved_any:
            print("\nnothing written — re-run with --apply")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
