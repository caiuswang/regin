"""Session-level retention prune for the append-only trace store.

`lib/trace/reap.py` removes superseded PENDING *placeholder* rows within a
still-live session. This module is the coarser lever: it drops whole sessions'
worth of trace data that no longer needs to be kept, so `db/regin.db` stops
growing without bound across thousands of sessions.

Three independent, opt-in modes (nothing is deleted unless a mode is enabled):

  * ``purge_test`` — remove ``is_test=1`` fixture sessions entirely (rows a test
    run leaked into the live DB), across every trace-keyed table incl.
    ``sessions``.
  * ``orphans`` — remove session-DETAIL rows (spans/map/usage/images/context)
    whose ``trace_id`` has no ``sessions`` row, left behind when a session row
    was dropped elsewhere. Deliberately scoped to the detail tables: tables
    like ``agent_messages`` (inbox) and ``bridge_*`` legitimately hold
    session-less rows (e.g. synthetic ``wiki-debt`` warnings) that are NOT
    orphaned trace data, so the orphan sweep must not touch them.
  * ``days`` — retention cutoff: drop the heavy per-span DETAIL of real
    (``is_test=0``) sessions older than N days while KEEPING the lightweight
    ``sessions`` aggregate row, so the sessions list + token/cost analytics
    survive but the drill-down trace is reclaimed. ``drop_sessions`` also
    removes the aggregate row.

Every mode is dry-run-safe and reports a per-table row tally. A real run must be
confirmed by the caller (the CLI forces ``--yes``); ``vacuum`` then returns the
freed pages to the OS (deletes alone only add to the freelist).
"""

from __future__ import annotations

import sqlite3

import lib.orm.engine as _engine
from lib.activity_log import get_activity_logger
from lib.orm.engine import get_connection

log = get_activity_logger("trace_ingest")

# Every table physically keyed by a session's trace_id EXCEPT `sessions`
# itself (the live schema's trace-child tables).
_CHILD_TABLES = (
    "session_spans", "session_trace_map", "turn_usage", "prompt_images",
    "session_repos", "context_composition", "agent_messages",
    "session_grades", "bridge_panes", "bridge_messages",
)

# The heavy per-span detail a retention cutoff drops; `sessions` (and the
# small metadata tables) are kept so history + analytics stay intact.
_DETAIL_TABLES = (
    "session_spans", "session_trace_map", "turn_usage", "prompt_images",
    "context_composition",
)


def _existing_tables(conn) -> set[str]:
    return {
        r["name"]
        for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).fetchall()
    }


def _plan(purge_test: bool, orphans: bool, days: int,
          drop_sessions: bool) -> list[tuple[str, tuple, str, tuple]]:
    """Build the ordered (label, tables, where_expr, params) operations.

    Children are listed before their `sessions` row so a foreign-key-on delete
    never strands a parent. `where_expr` is a complete predicate spliced after
    `WHERE`; `sessions`-table ops key on `is_test`/`started_at` directly rather
    than a `trace_id` subquery.
    """
    ops: list[tuple[str, tuple, str, tuple]] = []
    if purge_test:
        ops.append((
            "test-fixtures (detail)", _CHILD_TABLES,
            "trace_id IN (SELECT trace_id FROM sessions WHERE is_test = 1)", (),
        ))
        ops.append(("test-fixtures (sessions)", ("sessions",), "is_test = 1", ()))
    if orphans:
        # Scoped to detail tables, NOT every child table: agent_messages /
        # bridge_* hold legitimate session-less rows that aren't orphaned trace.
        ops.append((
            "orphans", _DETAIL_TABLES,
            "trace_id NOT IN (SELECT trace_id FROM sessions)", (),
        ))
    if days > 0:
        cutoff = (f"-{int(days)} days",)
        ops.append((
            f"retention >{int(days)}d (detail)", _DETAIL_TABLES,
            "trace_id IN (SELECT trace_id FROM sessions "
            "WHERE is_test = 0 AND started_at < datetime('now', ?))", cutoff,
        ))
        if drop_sessions:
            ops.append((
                f"retention >{int(days)}d (sessions)", ("sessions",),
                "is_test = 0 AND started_at < datetime('now', ?)", cutoff,
            ))
    return ops


def _rows_for(conn, tables: tuple, where_expr: str, params: tuple,
              existing: set[str]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for table in tables:
        if table not in existing:
            continue
        n = conn.execute(
            f"SELECT COUNT(*) AS n FROM {table} WHERE {where_expr}", params
        ).fetchone()["n"]
        if n:
            counts[table] = n
    return counts


def _delete_for(conn, tables: tuple, where_expr: str, params: tuple,
                existing: set[str]) -> dict[str, int]:
    deleted: dict[str, int] = {}
    for table in tables:
        if table not in existing:
            continue
        cur = conn.execute(f"DELETE FROM {table} WHERE {where_expr}", params)
        if cur.rowcount:
            deleted[table] = cur.rowcount
    return deleted


def _vacuum() -> bool:
    """Reclaim freed pages to the OS. Runs on its own autocommit connection
    (VACUUM cannot run inside a transaction). Returns False if the DB is busy
    (e.g. `regin serve` holds a write lock) rather than aborting the prune."""
    # Resolved at call time. `from … import DB_PATH` binds the real path at
    # import, which the tests' `tmp_db` monkeypatch of `lib.orm.engine.DB_PATH`
    # cannot reach — the first test to prune with vacuum=True would VACUUM the
    # developer's production database.
    conn = sqlite3.connect(_engine.DB_PATH, isolation_level=None)
    try:
        conn.execute("VACUUM")
        return True
    except sqlite3.OperationalError:
        log.error("prune_vacuum_busy", exc_info=True)
        return False
    finally:
        conn.close()


def prune_trace_data(*, purge_test: bool = False, orphans: bool = False,
                     days: int = 0, drop_sessions: bool = False,
                     dry_run: bool = True, vacuum: bool = False) -> dict:
    """Run the enabled prune modes and return a summary.

    A `dry_run` counts but writes nothing. With no mode enabled the result is
    empty (`enabled=[]`) and the DB is untouched — the caller reports guidance.
    """
    enabled = [m for m, on in (("purge_test", purge_test),
                               ("orphans", orphans), ("days", days > 0)) if on]
    ops = _plan(purge_test, orphans, days, drop_sessions)

    conn = get_connection()
    try:
        existing = _existing_tables(conn)
        by_table: dict[str, int] = {}
        for _label, tables, where_expr, params in ops:
            step = (_rows_for if dry_run else _delete_for)(
                conn, tables, where_expr, params, existing)
            for table, n in step.items():
                by_table[table] = by_table.get(table, 0) + n
        if not dry_run:
            conn.commit()
    finally:
        conn.close()

    vacuumed = False
    if not dry_run and vacuum:
        vacuumed = _vacuum()

    total = sum(by_table.values())
    record = log.read if dry_run else log.write
    record("trace_pruned", enabled=enabled, rows=total, dry_run=dry_run,
           vacuumed=vacuumed)
    return {
        "enabled": enabled,
        "by_table": by_table,
        "rows": total,
        "dry_run": dry_run,
        "vacuumed": vacuumed,
    }
