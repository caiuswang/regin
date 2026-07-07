"""Serve-time rung 0: a NULL-parent tool span value-joins to its issuing
`prompt-<uuid>` anchor via `source_prompt_id` ↔ `attributes.prompt_id`
(design Move 1b).

Goes through the real read path — `_fetch_spans` against a temp sqlite DB, then
`_graft_orphans` — NOT hand-built dicts, so a missing SELECT column or a broken
attribute fallback fails the test rather than silently no-op'ing.

Every value-join case uses TWO prompts: the tool span sits chronologically
after PROMPT2 but carries the promptId of the EARLIER PROMPT1. The
chronological fallback (`_graft_orphans_under_prompt`) would nest it under
PROMPT2; only a working rung 0 nests it under PROMPT1. So the assertion
isolates rung 0 from the fallback — a dropped SELECT column or broken
attribute fallback makes rung 0 miss and the tool lands on PROMPT2, failing
the test.

Key namespace invariant (from the verification rounds): the hook `prompt_id` /
transcript `promptId` VALUE is what joins — never the anchor's own `uuid` (the
`prompt-<uuid[:13]>` span id is keyed on uuid, a different value).
"""

from __future__ import annotations

import json
import sqlite3

from lib.orm.engine import get_connection
from lib.trace.projection import _fetch_spans, _graft_orphans

TRACE = 'trace-srcprompt'
P1_UUID = '2e8603a3-a69d-4c11-9f00-000000000001'
P2_UUID = '7b1140fe-c2aa-4d22-8e00-000000000002'
P1_SPAN = f'prompt-{P1_UUID[:13]}'   # prompt-2e8603a3-a69d
P2_SPAN = f'prompt-{P2_UUID[:13]}'   # prompt-7b1140fe-c2aa
PID1 = 'pr-3f9c-411a-submission-one'  # promptId of PROMPT1, != its own uuid
PID2 = 'pr-8ad0-9c31-submission-two'


def _insert(db_path, span_id, name, start_time, *, parent_id=None,
            attrs=None, source_prompt_id=None, turn_uuid=None, status='OK'):
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute(
            "INSERT INTO session_spans (trace_id, span_id, name, start_time, "
            "parent_id, status_code, source_prompt_id, turn_uuid, attributes) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (TRACE, span_id, name, start_time, parent_id, status,
             source_prompt_id, turn_uuid, json.dumps(attrs or {})),
        )
        conn.commit()
    finally:
        conn.close()


def _two_prompts(db_path):
    """PROMPT1 (earlier, promptId=PID1) then PROMPT2 (later, current)."""
    _insert(db_path, P1_SPAN, 'prompt', '2026-06-04T13:00:01',
            attrs={'text': 'first', 'prompt_id': PID1})
    _insert(db_path, P2_SPAN, 'prompt', '2026-06-04T13:00:10',
            attrs={'text': 'second', 'prompt_id': PID2})


def _project():
    """Run the real read path and return {span_id: projected span}."""
    conn = get_connection()
    try:
        raw = _fetch_spans(conn, TRACE)
    finally:
        conn.close()
    return {s['span_id']: s for s in _graft_orphans(raw)}


def test_rung0_parents_tool_to_prompt_by_value_via_column(tmp_db):
    """A NULL-parent tool span whose `source_prompt_id` COLUMN matches an
    earlier anchor's `attributes.prompt_id` parents to THAT anchor — beating
    the chronological fallback that would nest it under the current prompt.
    The value lives ONLY in the column (not attrs), so this fails if the
    `_fetch_spans` SELECT omits the promoted column."""
    _two_prompts(tmp_db)
    _insert(tmp_db, 'toolA', 'tool.Edit', '2026-06-04T13:00:15',
            attrs={'tool_name': 'Edit', 'tool_use_id': 'tu_a'},
            source_prompt_id=PID1)
    by_id = _project()
    assert by_id['toolA']['parent_id'] == P1_SPAN


def test_rung0_parents_tool_to_prompt_by_attribute_fallback(tmp_db):
    """A pre-migration row carries the value only in attributes.source_prompt_id
    (column NULL). Rung 0's column-then-attribute fallback still fires and
    beats the chronological fallback."""
    _two_prompts(tmp_db)
    _insert(tmp_db, 'toolB', 'tool.Write', '2026-06-04T13:00:15',
            attrs={'tool_name': 'Write', 'tool_use_id': 'tu_b',
                   'source_prompt_id': PID1},
            source_prompt_id=None)
    by_id = _project()
    assert by_id['toolB']['parent_id'] == P1_SPAN


def test_legacy_tool_without_source_prompt_falls_to_current_prompt(tmp_db):
    """A tool span with no source_prompt_id (and no turn linkage) is NOT
    rung-0'd; it falls through to the chronological fallback → the CURRENT
    prompt (PROMPT2), not the earlier one. Confirms rung 0 never fabricates a
    parent for a value-less span."""
    _two_prompts(tmp_db)
    _insert(tmp_db, 'toolC', 'tool.Read', '2026-06-04T13:00:15',
            attrs={'tool_name': 'Read', 'tool_use_id': 'tu_c'})
    by_id = _project()
    assert by_id['toolC']['parent_id'] == P2_SPAN


def test_source_prompt_no_matching_anchor_falls_to_current_prompt(tmp_db):
    """When no anchor carries the matching prompt_id, rung 0 does not fire; the
    tool span falls to the chronological fallback (current prompt), unchanged."""
    _two_prompts(tmp_db)
    _insert(tmp_db, 'toolD', 'tool.Edit', '2026-06-04T13:00:15',
            attrs={'tool_name': 'Edit', 'tool_use_id': 'tu_d'},
            source_prompt_id='pr-no-such-anchor')
    by_id = _project()
    assert by_id['toolD']['parent_id'] == P2_SPAN


TURN1 = 'turn-live-attributed-0001'


def test_turn_uuid_and_source_prompt_id_prefers_live_resp_anchor(tmp_db):
    """A tool span carrying BOTH `turn_uuid` (ingest_tool_attribution's normal
    backfill) AND `source_prompt_id`, whose turn has a live `resp-<turn>`
    anchor in the window, must nest under that anchor — the finer-grained
    turn-level parent — NOT the rung-0 join. Regression: rung 0 firing
    unconditionally before the turn_uuid anchors were consulted flattened
    turn-level nesting for the common case (a tool span attributed to its
    turn) by parenting straight to the coarser prompt."""
    _two_prompts(tmp_db)
    resp_span = f'resp-{TURN1[:13]}'
    _insert(tmp_db, resp_span, 'assistant_response', '2026-06-04T13:00:12',
            parent_id=P2_SPAN, attrs={'text': 'reply'}, turn_uuid=TURN1)
    _insert(tmp_db, 'toolE', 'tool.Edit', '2026-06-04T13:00:15',
            attrs={'tool_name': 'Edit', 'tool_use_id': 'tu_e'},
            source_prompt_id=PID1, turn_uuid=TURN1)
    by_id = _project()
    assert by_id['toolE']['parent_id'] == resp_span


def test_turn_uuid_with_no_anchor_falls_to_source_prompt_join(tmp_db):
    """A tool span carrying both `turn_uuid` and `source_prompt_id`, but whose
    turn has NEITHER a `resp-`/`think-` anchor nor a `prompt_by_turn` entry in
    the window, parents via the rung-0 `source_prompt_id` join — not the
    chronological fallback (which would land it on the CURRENT prompt,
    PROMPT2, instead of the earlier PROMPT1 the join names)."""
    _two_prompts(tmp_db)
    _insert(tmp_db, 'toolF', 'tool.Write', '2026-06-04T13:00:15',
            attrs={'tool_name': 'Write', 'tool_use_id': 'tu_f'},
            source_prompt_id=PID1, turn_uuid=TURN1)
    by_id = _project()
    assert by_id['toolF']['parent_id'] == P1_SPAN


def test_anchor_span_does_not_take_rung0(tmp_db):
    """An `assistant_response` anchor carrying a source_prompt_id must NOT be
    value-joined to the earlier prompt (that would bypass the resp/think
    no-2-cycle guard). It falls through to the chronological fallback → the
    CURRENT prompt (PROMPT2), unlike a tool span which rung-0's to PROMPT1."""
    _two_prompts(tmp_db)
    _insert(tmp_db, 'resp-xxxxxxxxxxxxx', 'assistant_response',
            '2026-06-04T13:00:15',
            attrs={'text': 'reply', 'turn_uuid': 'turn-unresolved'},
            source_prompt_id=PID1)
    by_id = _project()
    assert by_id['resp-xxxxxxxxxxxxx']['parent_id'] == P2_SPAN
