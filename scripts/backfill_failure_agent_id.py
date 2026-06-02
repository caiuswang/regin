"""One-shot backfill: attach `agent_id`/`agent_type` to orphan `tool.failure`
spans captured before post_tool_failure learned to persist them.

Before the fix, a tool call that FAILED inside a subagent produced a
`tool.failure` span with no `agent_id`, so the serve-time projection couldn't
re-parent it under its `subagent.start` — it rendered flat under the main
prompt instead of inside (collapsed) its subagent. The raw PostToolUseFailure
payloads DID carry `agent_id`/`agent_type`; we recover them here by joining the
span's `tool_use_id` against the hook payload log.

Disposable: delete this script once the existing traces have been backfilled.
The forward fix lives in hook_manager/handlers/post_tool_failure.py.
"""

from __future__ import annotations

import json
from pathlib import Path

from lib.orm.engine import get_connection


def _payload_log_index() -> dict[str, dict]:
    """tool_use_id -> {agent_id, agent_type} from the rotating hook payload log."""
    index: dict[str, dict] = {}
    base = Path.home() / '.claude'
    for name in ('hook-payloads.jsonl', 'hook-payloads.jsonl.1'):
        path = base / name
        if not path.exists():
            continue
        for line in path.read_text(errors='replace').splitlines():
            try:
                entry = json.loads(line)
            except (ValueError, TypeError):
                continue
            if entry.get('hook_event') != 'PostToolUseFailure':
                continue
            payload = entry.get('payload') or {}
            tu_id = payload.get('tool_use_id')
            agent_id = payload.get('agent_id')
            if tu_id and agent_id:
                index[tu_id] = {
                    'agent_id': agent_id,
                    'agent_type': payload.get('agent_type'),
                }
    return index


def backfill() -> tuple[int, int]:
    """Returns (patched, skipped_unrecoverable)."""
    index = _payload_log_index()
    conn = get_connection()
    patched = skipped = 0
    try:
        rows = conn.execute(
            "SELECT id, attributes FROM session_spans WHERE name = 'tool.failure'"
        ).fetchall()
        for row in rows:
            attrs = json.loads(row['attributes'] or '{}')
            if attrs.get('agent_id'):
                continue
            tu_id = attrs.get('tool_use_id')
            found = index.get(tu_id) if tu_id else None
            if not found:
                skipped += 1
                continue
            attrs['agent_id'] = found['agent_id']
            if found.get('agent_type'):
                attrs['agent_type'] = found['agent_type']
            conn.execute(
                "UPDATE session_spans SET attributes = ? WHERE id = ?",
                (json.dumps(attrs), row['id']),
            )
            patched += 1
        conn.commit()
    finally:
        conn.close()
    return patched, skipped


if __name__ == '__main__':
    n, miss = backfill()
    print(f'backfilled agent_id onto {n} tool.failure span(s); '
          f'{miss} had no recoverable tool_use_id in the payload log')
