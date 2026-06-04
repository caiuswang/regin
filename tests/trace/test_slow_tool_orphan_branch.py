"""Regression: a slow tool's resolved span must keep its assistant-response
branch, not jump to the prompt root.

Reproduces the attribution race behind the "live trace loses a bash branch"
report (session 5c59394f…): a slow Bash emits a `pending-<tu>` placeholder at
PreToolUse and the resolved `tool.Bash` at PostToolUse, both posted
parent-less / turn_uuid-less. If a `turn_trace` attribution pass lands while
ONLY the placeholder exists, the placeholder absorbs the `turn_uuid` +
`resp-<turn>` parent; the later-arriving resolved span never does (the turn is
cached, so it's never re-attributed). At serve time the placeholder is retired
(superseded by tool_use_id) and the orphaned resolved span falls to the
`_graft_orphans_under_prompt` fallback → nests under the prompt root instead of
its assistant-response. The branch visibly collapses.
"""

from __future__ import annotations

import json
import sqlite3

from lib.trace import trace_service
from lib.trace.pending_spans import tool_pending_id
from lib.trace.trace_service.queries import fetch_session_paginated

TRACE = 'trace-slow-bash'
TURN = '48ade465-5c75-4793-8f6d-730000a5e917'
RESP = f'resp-{TURN[:13]}'           # resp-48ade465-5c75
PROMPT = 'prompt-2e8603a3-a69d'
TU = 'toolu_slow001'
PENDING = tool_pending_id(TU)        # pending-toolu_slow0
RESOLVED = 'resolvedbash0001'


def _insert_session(db_path, trace=TRACE):
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute(
            "INSERT INTO sessions (trace_id, started_at, last_seen, model) "
            "VALUES (?, ?, ?, ?)",
            (trace, '2026-06-04T13:00:00', '2026-06-04T13:00:00',
             'claude-opus-4-7'),
        )
        conn.commit()
    finally:
        conn.close()


def _insert_span(db_path, span_id, name, start_time, *, trace=TRACE,
                 parent_id=None, status='OK', turn_uuid=None, attrs=None):
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute(
            "INSERT INTO session_spans (trace_id, span_id, name, start_time, "
            "parent_id, status_code, turn_uuid, attributes) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (trace, span_id, name, start_time, parent_id, status, turn_uuid,
             json.dumps(attrs or {})),
        )
        conn.commit()
    finally:
        conn.close()


def _drive_race(db_path, *, tool='Bash', tu=TU, trace=TRACE, resolved=RESOLVED):
    """Materialize the exact DB state the live race produces for one slow tool.

    Returns the placeholder span_id so callers can assert it was retired."""
    pending = tool_pending_id(tu)
    _insert_session(db_path, trace)
    # 1. turn anchor + its assistant-response span (the proper parent).
    _insert_span(db_path, PROMPT, 'prompt', '2026-06-04T13:00:01', trace=trace,
                 attrs={'text': 'run the slow command'})
    _insert_span(db_path, RESP, 'assistant_response', '2026-06-04T13:00:02',
                 trace=trace, parent_id=PROMPT, turn_uuid=TURN,
                 attrs={'text': 'running…', 'turn_uuid': TURN})
    # 2. PreToolUse → pending placeholder (parent-less, turn_uuid-less).
    _insert_span(db_path, pending, f'tool.{tool}', '2026-06-04T13:00:03',
                 trace=trace, status='PENDING',
                 attrs={'tool_name': tool, 'tool_use_id': tu, 'live': True})
    # 3. attribution pass lands while ONLY the placeholder exists → it
    #    absorbs turn_uuid + the resp- parent.
    trace_service.ingest_tool_attribution({
        'trace_id': trace, 'turn_uuid': TURN, 'parent_span_id': RESP,
        'tool_calls': [{'tool_use_id': tu, 'name': tool,
                        'output_tokens': 5, 'input_tokens': 5}],
    })
    # 4. PostToolUse (command finally returns) → resolved span, parent-less /
    #    turn_uuid-less, and never re-attributed (turn already cached).
    _insert_span(db_path, resolved, f'tool.{tool}', '2026-06-04T13:05:03',
                 trace=trace, status='OK',
                 attrs={'tool_name': tool, 'tool_use_id': tu,
                        'command': 'sleep 300'})
    return pending


def test_slow_tool_resolved_span_keeps_resp_parent(tmp_db):
    pending = _drive_race(tmp_db)

    widened, _tree, _more, retired = fetch_session_paginated(TRACE, limit=50)
    by_id = {s['span_id']: s for s in widened}

    # The placeholder is retired (superseded by the resolved span).
    assert pending in retired
    assert pending not in by_id
    # The resolved span survives — nothing is lost.
    assert RESOLVED in by_id

    # …and it must stay under its assistant-response, NOT jump to the prompt
    # root. This is the assertion the bug fails.
    assert by_id[RESOLVED]['parent_id'] == RESP, (
        f"resolved slow-tool span reparented to "
        f"{by_id[RESOLVED]['parent_id']!r}; expected {RESP!r} "
        f"(branch under the assistant response was lost)"
    )
    # turn_uuid is transferred too, so the ladder + cost rollups stay coherent.
    assert by_id[RESOLVED]['turn_uuid'] == TURN


def test_slow_mcp_tool_keeps_resp_parent(tmp_db):
    """The fix is name-normalization-proof: an `mcp__*` slow tool (placeholder
    minted raw, resolved minted via `_normalize_tool_name`) must inherit too,
    not just Bash."""
    _drive_race(tmp_db, tool='mcp__gitnexus__query',
                tu='toolu_mcp001', resolved='resolvedmcp00001')
    widened, *_ = fetch_session_paginated(TRACE, limit=50)
    by_id = {s['span_id']: s for s in widened}
    assert by_id['resolvedmcp00001']['parent_id'] == RESP


def test_attributed_resolved_span_is_not_overridden(tmp_db):
    """A fast tool whose resolved span was itself attributed (has its own
    turn_uuid + parent) must be left untouched — the transfer is gated on a
    NULL turn_uuid, so a coexisting placeholder can't clobber a good parent."""
    _insert_session(tmp_db)
    _insert_span(tmp_db, PROMPT, 'prompt', '2026-06-04T13:00:01',
                 attrs={'text': 'x'})
    _insert_span(tmp_db, RESP, 'assistant_response', '2026-06-04T13:00:02',
                 parent_id=PROMPT, turn_uuid=TURN, attrs={'turn_uuid': TURN})
    other_resp = 'resp-deadbeef0000'
    _insert_span(tmp_db, other_resp, 'assistant_response', '2026-06-04T13:00:02',
                 parent_id=PROMPT, turn_uuid='deadbeef-0000')
    # Placeholder carries one resp; the resolved span was already attributed to
    # a *different* resp (its own, correct turn).
    _insert_span(tmp_db, PENDING, 'tool.Bash', '2026-06-04T13:00:03',
                 status='PENDING', parent_id=RESP, turn_uuid=TURN,
                 attrs={'tool_name': 'Bash', 'tool_use_id': TU})
    _insert_span(tmp_db, RESOLVED, 'tool.Bash', '2026-06-04T13:00:04',
                 status='OK', parent_id=other_resp, turn_uuid='deadbeef-0000',
                 attrs={'tool_name': 'Bash', 'tool_use_id': TU})

    widened, *_ = fetch_session_paginated(TRACE, limit=50)
    by_id = {s['span_id']: s for s in widened}
    assert by_id[RESOLVED]['parent_id'] == other_resp  # not overridden to RESP
    assert by_id[RESOLVED]['turn_uuid'] == 'deadbeef-0000'


def test_permission_placeholder_does_not_reparent_tool(tmp_db):
    """A `permreq-<tu>` permission.request placeholder shares the tool's
    tool_use_id and is dropped when the tool resolves — but it must never
    donate ITS parent to the resolved tool span (only a `tool.` placeholder
    may). With no tool placeholder present, the orphaned tool span falls to
    the prompt-root graft, NOT the permission row's bogus parent."""
    _insert_session(tmp_db)
    _insert_span(tmp_db, PROMPT, 'prompt', '2026-06-04T13:00:01',
                 attrs={'text': 'x'})
    _insert_span(tmp_db, RESP, 'assistant_response', '2026-06-04T13:00:02',
                 parent_id=PROMPT, turn_uuid=TURN, attrs={'turn_uuid': TURN})
    permreq = f'permreq-{TU[:13]}'
    _insert_span(tmp_db, permreq, 'permission.request', '2026-06-04T13:00:03',
                 status='PENDING', parent_id=RESP, turn_uuid=TURN,
                 attrs={'tool_name': 'Bash', 'tool_use_id': TU})
    _insert_span(tmp_db, RESOLVED, 'tool.Bash', '2026-06-04T13:00:04',
                 status='OK',
                 attrs={'tool_name': 'Bash', 'tool_use_id': TU})

    widened, _tree, _more, retired = fetch_session_paginated(TRACE, limit=50)
    by_id = {s['span_id']: s for s in widened}
    assert permreq in retired           # the permission row is still dropped
    assert by_id[RESOLVED]['parent_id'] != RESP   # but it did NOT donate parent
    assert by_id[RESOLVED]['turn_uuid'] is None
