#!/usr/bin/env python3
"""Remove test-run traces from the regin SQLite DB.

A "test trace" is any trace_id that has at least one span whose attributes
match one of the heuristics below:

  1. `attributes.is_test == true`                           (marker stamped
     by lib/hook_plugin.py when REGIN_TRACE_TEST is set — the authoritative
     signal for anything produced by tests/trace/).

  2. `attributes.file_path` lives under a pytest tmp path or under one of
     the test fixture file names.

  3. `attributes.text` / `attributes.command_preview` contains a sentinel
     string that only appears in tests/trace/ test prompts.

Usage:
    ./.venv/bin/python scripts/purge_test_traces.py           # preview only
    ./.venv/bin/python scripts/purge_test_traces.py --apply   # delete
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

DEFAULT_DB = Path(__file__).resolve().parent.parent / "db" / "regin.db"

# WHERE-clause fragments — each returns TRUE for any span that clearly came
# from the trace-regression test harness.
MARKER_CLAUSES = [
    # Authoritative marker (new runs).
    "json_extract(attributes, '$.is_test') IN (1, 'true', 'True')",

    # File paths that only exist under pytest tmp or fixture outputs.
    "json_extract(attributes, '$.file_path') LIKE '%/pytest-of-%/%'",
    "json_extract(attributes, '$.file_path') LIKE '%trace_notify_probe%'",
    "json_extract(attributes, '$.file_path') LIKE '%trace_new_file.txt%'",
    "json_extract(attributes, '$.file_path') LIKE '%/trace-smoke-%'",
    "json_extract(attributes, '$.file_path') LIKE '%/trace-test-%'",
    "json_extract(attributes, '$.file_path') LIKE '%/trace-planprobe-%'",

    # Verbatim prompt sentinels from tests/trace/test_*.py — full-phrase
    # match so they don't catch real user chat about tests.
    "json_extract(attributes, '$.text') LIKE '%please reply with just the word READY%'",
    "json_extract(attributes, '$.text') LIKE '%echo-back-trace-test%'",
    "json_extract(attributes, '$.text') LIKE '%reply with just DONE%'",
    "json_extract(attributes, '$.text') LIKE '%reply with the word ONE%'",
    "json_extract(attributes, '$.text') LIKE '%reply with the word TWO%'",
    "json_extract(attributes, '$.text') LIKE '%reply with the word THREE%'",
    "json_extract(attributes, '$.text') LIKE '%please read sample.txt in the current directory and tell me line 2%'",
    "json_extract(attributes, '$.text') LIKE '%run `echo trace-bash-9371%'",
    "json_extract(attributes, '$.text') LIKE '%use the Grep tool to find the word ''line'' inside sample.txt%'",
    "json_extract(attributes, '$.text') LIKE '%edit sample.py using the Edit tool: replace%'",
    "json_extract(attributes, '$.text') LIKE '%create a new file called trace_new_file.txt%'",
    "json_extract(attributes, '$.text') LIKE '%use the Read tool to read the file at %/.claude/skills/%'",
    "json_extract(attributes, '$.text') LIKE '%use the Task tool%subagent_type=general-purpose%'",
    "json_extract(attributes, '$.text') LIKE '%call the mcp__plugin_playwright_playwright__browser_close%'",
    "json_extract(attributes, '$.text') LIKE '%/plan just state that sample.txt%'",
    "json_extract(attributes, '$.text') LIKE '%/plan add a one-line comment to sample.txt saying ''planned''%'",
    "json_extract(attributes, '$.text') LIKE '%/plan state that sample.txt is fine as is%'",
    "json_extract(attributes, '$.text') LIKE '%simply state in your plan that sample.txt%'",
    "json_extract(attributes, '$.text') LIKE '%add a one-line comment saying PLANNED to sample.txt%'",
    "json_extract(attributes, '$.text') LIKE '%use the Write tool to create a file named trace_notify_probe.dat%'",
    "json_extract(attributes, '$.text') LIKE '%run this exact bash: `rm -rf /tmp/trace-noop-xyz`%'",
    "json_extract(attributes, '$.text') LIKE '%approve, go ahead with this plan%'",
    "json_extract(attributes, '$.text') LIKE '%reject, cancel this plan%'",
    "json_extract(attributes, '$.text') = '/notify just say hello in a 1-word notification title'",
    "json_extract(attributes, '$.text') = 'hello world'",
    "json_extract(attributes, '$.text') = 'hello world trace harness'",
    "json_extract(attributes, '$.text') = 'hello from tmux'",
    "json_extract(attributes, '$.text') = 'ping'",

    # Bash command previews containing our sentinel strings.
    "json_extract(attributes, '$.command_preview') LIKE '%trace-bash-9371%'",
    "json_extract(attributes, '$.command_preview') LIKE '%trace-noop-xyz%'",
]


def find_test_trace_ids(conn: sqlite3.Connection) -> list[str]:
    clauses = " OR ".join(MARKER_CLAUSES)
    sql = f"SELECT DISTINCT trace_id FROM session_spans WHERE {clauses}"
    return [r[0] for r in conn.execute(sql).fetchall() if r[0]]


def purge(conn: sqlite3.Connection, trace_ids: list[str]) -> dict[str, int]:
    if not trace_ids:
        return {}
    placeholders = ",".join("?" for _ in trace_ids)
    stats: dict[str, int] = {}
    for table, col in [
        ("session_spans", "trace_id"),
        ("skill_reads", "session_id"),
        ("plan_sessions", "session_id"),
        ("rule_triggers", "session_id"),
    ]:
        cur = conn.execute(
            f"DELETE FROM {table} WHERE {col} IN ({placeholders})", trace_ids
        )
        stats[table] = cur.rowcount
    conn.commit()
    return stats


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--db", type=Path, default=DEFAULT_DB, help=f"DB path (default: {DEFAULT_DB})")
    p.add_argument("--apply", action="store_true", help="Actually delete (default: preview only)")
    args = p.parse_args()

    if not args.db.exists():
        print(f"error: db not found at {args.db}", file=sys.stderr)
        sys.exit(1)

    conn = sqlite3.connect(args.db)
    trace_ids = find_test_trace_ids(conn)
    print(f"identified {len(trace_ids)} test trace(s) in {args.db}")
    for tid in trace_ids[:10]:
        row = conn.execute(
            "SELECT MIN(start_time) s, COUNT(*) n FROM session_spans WHERE trace_id = ?",
            (tid,),
        ).fetchone()
        print(f"  {tid}  spans={row[1]}  started={row[0]}")
    if len(trace_ids) > 10:
        print(f"  … and {len(trace_ids) - 10} more")

    if not args.apply:
        print("\n(dry-run; pass --apply to delete)")
        return

    stats = purge(conn, trace_ids)
    conn.close()
    print("\ndeleted rows:")
    for table, n in stats.items():
        print(f"  {table}: {n}")


if __name__ == "__main__":
    main()
