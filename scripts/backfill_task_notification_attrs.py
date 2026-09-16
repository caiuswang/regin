#!/usr/bin/env python
"""One-shot: re-parse stored `<task-notification>` envelopes into span attrs.

Rows captured before the parser learned `<result>` / `<usage>` still hold the
whole envelope in `text`, so the agent report, token count, tool-call count and
duration are read back out of it. Idempotent.

    .venv/bin/python scripts/backfill_task_notification_attrs.py [--dry-run]
"""

from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lib.orm.engine import get_connection  # noqa: E402
from lib.trace.task_notification import parse_task_notification  # noqa: E402

FIELDS = ('result', 'subagent_tokens', 'tool_uses', 'duration_ms')


def merged_attrs(row) -> str | None:
    """The row's attributes JSON with the newly-parsed fields folded in, or
    None when the envelope adds nothing it doesn't already carry."""
    attrs = json.loads(row['attributes'] or '{}')
    parsed = parse_task_notification(attrs.get('text') or '') or {}
    added = {k: parsed[k] for k in FIELDS
             if k in parsed and attrs.get(k) != parsed[k]}
    return json.dumps({**attrs, **added}) if added else None


def main() -> int:
    dry_run = '--dry-run' in sys.argv
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT trace_id, span_id, attributes FROM session_spans "
            "WHERE name = 'task.notification'"
        ).fetchall()
        updated = 0
        for row in rows:
            merged = merged_attrs(row)
            if merged is None:
                continue
            updated += 1
            if not dry_run:
                conn.execute(
                    "UPDATE session_spans SET attributes = ? "
                    "WHERE trace_id = ? AND span_id = ?",
                    (merged, row['trace_id'], row['span_id']),
                )
        if not dry_run:
            conn.commit()
        verb = 'Would update' if dry_run else 'Updated'
        print(f'{verb} {updated} of {len(rows)} task.notification span(s).')
    finally:
        conn.close()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
