"""Regression: a turn triggered by a background-task completion must not be
silently dropped from the trace by a `resp-`/`think-` parent cycle.

Reproduces the "WebUI misses some spans" report (session ef837d77…): when a
turn is driven by a `task.notification` (a background workflow finishing
re-invokes the agent) instead of a user prompt, its `assistant_response` /
`assistant.thinking` spans never receive a write-time `prompt-` parent — they
land as orphans (parent_id NULL; turn_uuid only in `attributes`). The
deterministic ladder (`_ladder_orphans_by_turn`) then offers each anchor the
OTHER anchor of the same turn as a parent (`think → resp`, `resp → think`),
forming a 2-cycle. `_build_span_tree` only roots spans whose parent is
null/missing, so neither cyclic node is reachable from any root and the whole
turn — anchors plus the tools nested under them — vanishes from the tree.

The fix makes anchor spans take the prompt rung only, so they fall through to
the chronological `_graft_orphans_under_prompt` fallback (exactly the path a
thinking-less task turn already took, which is why the LAST response of the
real session survived while the earlier ones did not).
"""

from __future__ import annotations

import json
import sqlite3

from lib.trace.trace_service import fetch_session_projection

TRACE = 'trace-task-notif'
PROMPT = 'prompt-2e8603a3-a69d'
# A background-completion turn (no user-prompt anchor). Span ids embed the
# turn slug `turn_uuid[:13]`, so the anchors share it: resp-…/think-….
TURN2 = '8ecde7bc-6cd8-4e69-a4f0-7be566b07afc'
RESP2 = f'resp-{TURN2[:13]}'      # resp-8ecde7bc-6cd8
THINK2 = f'think-{TURN2[:13]}'    # think-8ecde7bc-6cd8
READ2 = 'toolread00000001'


def _insert_session(db_path):
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute(
            "INSERT INTO sessions (trace_id, started_at, last_seen, model) "
            "VALUES (?, ?, ?, ?)",
            (TRACE, '2026-06-12T07:00:00', '2026-06-12T07:50:00',
             'claude-opus-4-8'),
        )
        conn.commit()
    finally:
        conn.close()


def _insert_span(db_path, span_id, name, start_time, *, parent_id=None,
                 status='OK', turn_uuid=None, attrs=None):
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute(
            "INSERT INTO session_spans (trace_id, span_id, name, start_time, "
            "parent_id, status_code, turn_uuid, attributes) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (TRACE, span_id, name, start_time, parent_id, status, turn_uuid,
             json.dumps(attrs or {})),
        )
        conn.commit()
    finally:
        conn.close()


def _flatten(tree):
    """span_id → parent_id over every node reachable in the rendered tree."""
    out: dict[str, str | None] = {}

    def walk(nodes):
        for n in nodes:
            d = n['data']
            out[d['span_id']] = d.get('parent_id')
            walk(n.get('children', []))

    walk(tree)
    return out


def _seed_task_notification_turn(db_path):
    """A real user prompt, then a background-completion turn whose anchors are
    orphans (the exact shape that produced the cycle)."""
    _insert_session(db_path)
    _insert_span(db_path, PROMPT, 'prompt', '2026-06-12T07:20:00',
                 attrs={'text': 'kick off the background research'})
    _insert_span(db_path, 'task-wf01', 'task.notification',
                 '2026-06-12T07:47:23')
    # Background-completion turn: anchors carry turn_uuid ONLY in attributes
    # (the column is NULL in the wild), no write-time prompt parent.
    _insert_span(db_path, THINK2, 'assistant.thinking', '2026-06-12T07:47:29',
                 attrs={'turn_uuid': TURN2})
    _insert_span(db_path, RESP2, 'assistant_response', '2026-06-12T07:47:29',
                 attrs={'turn_uuid': TURN2, 'text': 'the fan-out completed'})
    # A tool nested under the response — would be dragged into the void too.
    _insert_span(db_path, READ2, 'tool.Read', '2026-06-12T07:47:33',
                 parent_id=RESP2,
                 attrs={'tool_name': 'Read', 'turn_uuid': TURN2})


def test_task_notification_turn_is_not_dropped(tmp_db):
    _seed_task_notification_turn(tmp_db)

    _widened, tree = fetch_session_projection(TRACE)
    placed = _flatten(tree)

    # All three spans of the background turn render — nothing is lost.
    assert THINK2 in placed, "assistant.thinking dropped (cycle)"
    assert RESP2 in placed, "assistant_response dropped (cycle)"
    assert READ2 in placed, "tool.Read dropped with its cyclic parent"

    # The anchors must NOT parent each other (that was the 2-cycle).
    assert placed[RESP2] != THINK2
    assert placed[THINK2] != RESP2
    # They fall through to the prompt graft, like any anchorless turn.
    assert placed[RESP2] == PROMPT
    assert placed[THINK2] == PROMPT
    # The tool keeps its real branch under the response.
    assert placed[READ2] == RESP2


def test_thinkless_task_turn_still_survives(tmp_db):
    """A background turn with a response but no thinking sibling already
    survived (it can't be paired into a cycle); guard that the fix keeps it
    grafted under the prompt rather than regressing it."""
    _insert_session(tmp_db)
    _insert_span(tmp_db, PROMPT, 'prompt', '2026-06-12T07:20:00',
                 attrs={'text': 'x'})
    lone = 'resp-8957954c-28dd'
    _insert_span(tmp_db, lone, 'assistant_response', '2026-06-12T07:49:50',
                 attrs={'turn_uuid': '8957954c-28dd-47fb-ba62-9d78cb2e41b0',
                        'text': 'the survey is complete'})

    _widened, tree = fetch_session_projection(TRACE)
    placed = _flatten(tree)
    assert lone in placed
    assert placed[lone] == PROMPT


def test_normal_turn_anchors_stay_siblings_under_prompt(tmp_db):
    """A prompt-driven turn carries write-time prompt parents on its anchors,
    so they never reach the ladder; the fix must leave them untouched —
    siblings under the prompt, not nested one-under-the-other."""
    _insert_session(tmp_db)
    _insert_span(tmp_db, PROMPT, 'prompt', '2026-06-12T07:20:00',
                 attrs={'text': 'x'})
    t = 'aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee'
    think = f'think-{t[:13]}'
    resp = f'resp-{t[:13]}'
    _insert_span(tmp_db, think, 'assistant.thinking', '2026-06-12T07:20:05',
                 parent_id=PROMPT, turn_uuid=t, attrs={'turn_uuid': t})
    _insert_span(tmp_db, resp, 'assistant_response', '2026-06-12T07:20:06',
                 parent_id=PROMPT, turn_uuid=t, attrs={'turn_uuid': t})

    _widened, tree = fetch_session_projection(TRACE)
    placed = _flatten(tree)
    assert placed[think] == PROMPT
    assert placed[resp] == PROMPT
