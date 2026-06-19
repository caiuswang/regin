"""Pure data transforms used to reshape raw session_spans into the tree
the Vue Trace view consumes.

These live in `lib/` because they are pure transforms over span dicts
with zero Flask/request dependency — both the web blueprints and the
lib-side ingest/queries layer need them, so they belong below the web
layer rather than above it. A handful of tests reach them as
`app_module._graft_orphans` etc.; `web/app.py` re-exports the names so
that surface stays intact.

Pairing is deliberate:
    _fetch_spans(conn, trace_id)       # read
    _graft_orphans(spans)              # pure
    _widen_envelopes(spans)            # pure
    _build_span_tree(spans)            # pure — builds the view model
    _persist_projection(conn, …)       # write — turns a projection into DB UPDATEs

The GET /api/sessions/<id> handler composes these as:
    raw = _fetch_spans(conn, id)
    projected = _widen_envelopes(_graft_orphans(raw))
    tree = _build_span_tree(projected)

POST /api/sessions/<id>/materialize adds a trailing _persist_projection
call to commit the shape back to disk.
"""

from __future__ import annotations

import json
from datetime import datetime

# A `turn` span emitted on UserPromptSubmit fires in the same hook
# invocation as the new `prompt` span, so its timestamp lands a handful
# of milliseconds BEFORE the new prompt's timestamp. In older data (and
# in races where handler ordering slips), the turn sorts ahead of the
# new prompt in `_graft_orphans` and gets attached to the PREVIOUS
# prompt — widening that prompt's envelope to the next user input and
# inflating its duration by the entire user-idle gap. Any turn whose
# start_time is within this window of a LATER prompt's start_time is
# re-attributed to the later prompt. 1 s is comfortably larger than
# handler-chain jitter (ms) and comfortably smaller than any real
# per-tool response cadence, so it can't re-attach a legitimate turn.
_TURN_LOOKAHEAD_SECONDS = 1.0


def _fetch_spans(conn, trace_id: str) -> list[dict]:
    rows = conn.execute("""
        SELECT id, trace_id, span_id, parent_id, name, kind,
               start_time, end_time, duration_ms, attributes,
               status_code, status_message,
               output_tokens, input_tokens, image_tokens, cost_usd,
               tool_use_id, turn_uuid, source
        FROM session_spans
        WHERE trace_id = ?
        ORDER BY start_time ASC, id ASC
    """, (trace_id,)).fetchall()
    return [
        {**dict(r), 'attributes': json.loads(r['attributes'])}
        for r in rows
    ]


_SESSION_LIFECYCLE_NAMES = frozenset({'session.start', 'session.end'})
_COMPACT_BOUNDARY_NAMES = frozenset({'compact.pre', 'compact.post'})
# A `/rewind` marker: a conversation-level divider at the fork point. Like
# the other boundaries it stays a top-level node (grafted to the conversation
# root, never under a prompt), but unlike them it ADOPTS the discarded turns
# as children so the UI can collapse the abandoned branch behind it.
_REWIND_BOUNDARY_NAMES = frozenset({'rewind'})
# Boundary spans never nest under a prompt — they delimit conversation-level
# events (session start/end, /compact runs, /rewind). Grafting them under the
# most recent prompt would hide the boundary inside an unrelated turn's
# descendants and lose the visual divider.
_FIRST_CLASS_BOUNDARY_NAMES = (
    _SESSION_LIFECYCLE_NAMES | _COMPACT_BOUNDARY_NAMES | _REWIND_BOUNDARY_NAMES
)


def _is_pending(span: dict) -> bool:
    """A live placeholder span awaiting its resolved counterpart."""
    return span.get('status_code') == 'PENDING'


# Spans the deterministic ladder never re-parents: prompts/conversation
# are roots or anchors, boundaries delimit conversation-level events, and
# subagent markers are re-parented by agent_id in a dedicated pass.
_LADDER_SKIP_NAMES = (
    frozenset({'prompt', 'conversation', 'subagent.start', 'subagent.stop'})
    | _FIRST_CLASS_BOUNDARY_NAMES
)

# The per-turn response/thinking anchors. They are PEERS of each other under
# the turn's prompt — never parent/child — so the ladder must not let one nest
# under the other (that forms a 2-cycle the tree builder silently drops).
_RESP_THINK_ANCHOR_NAMES = frozenset({'assistant_response', 'assistant.thinking'})


def _turn_uuid_of(span: dict) -> str | None:
    """Turn uuid from the column, falling back to the attribute the live
    hook stamped (`attributes.turn_uuid`)."""
    return span.get('turn_uuid') or (span.get('attributes') or {}).get('turn_uuid')


def _build_prompt_by_turn(out: list[dict]) -> dict[str, str]:
    """turn_uuid → prompt anchor span_id, learned from the
    assistant_response / assistant.thinking spans that already carry a
    `prompt-*` parent (set at write time by the turn_trace poster). Lets a
    turn's tools fall to the prompt rung even when the turn emitted no
    `resp-`/`think-` span (a silent tool-only turn)."""
    by_turn: dict[str, str] = {}
    for s in out:
        if s['name'] not in ('assistant_response', 'assistant.thinking'):
            continue
        t = _turn_uuid_of(s)
        p = s.get('parent_id')
        if t and isinstance(p, str) and p.startswith('prompt-'):
            by_turn[t] = p
    return by_turn


def _ladder_orphans_by_turn(out: list[dict], by_id: dict) -> None:
    """Deterministic parent ladder for NULL-parent, non-boundary spans
    that carry a `turn_uuid`: `resp-<turn>` → `think-<turn>` → the turn's
    prompt anchor, chosen purely by deterministic id existence (no
    timestamps). This is the preference pass; spans it can't place (no
    turn_uuid, or none of the rungs exist) are left NULL for the
    chronological `_graft_orphans_under_prompt` fallback. Mutates in place.

    Load-bearing for migration: an old session (parent_id NULL but
    turn_uuid populated, once repaired so the resp/think anchors carry
    prompt parents) heals here at read time with zero re-emission."""
    prompt_by_turn = _build_prompt_by_turn(out)
    for s in out:
        if s.get('parent_id') is not None:
            continue
        if s['name'] in _LADDER_SKIP_NAMES:
            continue
        t = _turn_uuid_of(s)
        if not t:
            continue
        # The `resp-`/`think-` anchors of a turn are PEERS, not parent/child.
        # An orphan anchor (a turn with no write-time prompt parent — chiefly a
        # `task.notification`/background-completion turn that had no user-prompt
        # anchor) must skip the anchor rungs, or `think-<t>` and `resp-<t>` pick
        # each other and form a 2-cycle that `_build_span_tree` can't root,
        # silently dropping the whole turn (and its child tools). Anchors take
        # the prompt rung only; the chronological `_graft_orphans_under_prompt`
        # fallback catches them when no prompt anchor exists for the turn.
        if s['name'] in _RESP_THINK_ANCHOR_NAMES:
            cands = (prompt_by_turn.get(t),)
        else:
            cands = (f'resp-{t[:13]}', f'think-{t[:13]}', prompt_by_turn.get(t))
        for cand in cands:
            if cand and cand in by_id and cand != s['span_id']:
                s['parent_id'] = cand
                break


def _graft_orphans_under_prompt(sorted_spans: list[dict], by_id: dict) -> None:
    """Graft each orphan span under the most recent `prompt`. Mutates the
    dicts in `by_id` in place.

    A live PENDING placeholder (promptlive-…) acts as a real prompt here: it
    starts at submit time, so it only becomes `current_prompt` for spans that
    come AFTER it — its own turn's in-flight tools/response, which haven't yet
    been parented (their `resp-`/`prompt-` anchors land on Stop). Without this
    the in-flight tools would orphan to the PREVIOUS prompt (or to root) and the
    placeholder itself would nest under the previous prompt instead of opening a
    new turn. The pre-P2b steal concern (grabbing a prior turn's work) no longer
    applies: assistant/tool spans now carry deterministic write-time parents, so
    a completed turn's spans are never orphans the placeholder could capture.
    First-class boundary spans never anchor orphans."""
    current_prompt = None
    for span in sorted_spans:
        resolved = by_id[span['span_id']]
        if resolved['name'] == 'prompt':
            current_prompt = resolved
        elif resolved['name'] in _FIRST_CLASS_BOUNDARY_NAMES:
            continue
        elif current_prompt is not None and not resolved.get('parent_id'):
            resolved['parent_id'] = current_prompt['span_id']


def _graft_conversation_roots(out: list[dict], conv_span_id: str) -> None:
    """Graft orphan prompts + first-class boundary spans under the
    conversation span (when one exists). Never nests them under a
    prompt — a boundary must stay a top-level divider."""
    grafted_names = {'prompt', *_FIRST_CLASS_BOUNDARY_NAMES}
    for s in out:
        if s['name'] in grafted_names and not s.get('parent_id'):
            s['parent_id'] = conv_span_id


def _heal_dangling_parents(out: list[dict], by_id: dict) -> None:
    """Clear parent_ids pointing to a non-existent span. Happens when a
    write-time parent referenced an anchor that was never emitted (e.g.
    an unreadable transcript tail). The cleared span then falls to the
    ladder / chronological fallback like any other orphan."""
    valid_ids = set(by_id)
    for s in out:
        pid = s.get('parent_id')
        if pid and pid not in valid_ids:
            s['parent_id'] = None


def _attach_turn_to_following_prompt(
    turn_span: dict, s_start_dt: datetime, prompts_by_start: list[dict],
) -> None:
    for p in prompts_by_start:
        try:
            p_start_dt = datetime.fromisoformat(p['start_time'])
        except (TypeError, ValueError):
            continue
        delta = (p_start_dt - s_start_dt).total_seconds()
        if 0 <= delta < _TURN_LOOKAHEAD_SECONDS:
            turn_span['parent_id'] = p['span_id']
            return


# Spans that fire on UserPromptSubmit a few ms BEFORE the new prompt's
# anchor lands, so a chronological graft would wrongly attach them to the
# PREVIOUS prompt. `turn`: the per-turn usage marker; `memory.recall`: the
# `<recalled_experience>` injection span (emitted by the memory_recall
# handler, which runs before prompt_trace). Both carry no deterministic
# parent, so they need this chronological nudge onto the following prompt.
_SUBMIT_LOOKAHEAD_NAMES = frozenset({'turn', 'memory.recall'})


def _relabel_turns_by_lookahead(out: list[dict]) -> None:
    """Re-attribute submit-time spans that landed a few ms before a LATER
    prompt back onto that prompt. See `_TURN_LOOKAHEAD_SECONDS` and
    `_SUBMIT_LOOKAHEAD_NAMES`. These spans carry no deterministic parent,
    so this chronological nudge stays."""
    prompts_by_start = sorted(
        (s for s in out if s['name'] == 'prompt' and not _is_pending(s)),
        key=lambda s: s['start_time'],
    )
    for s in out:
        if s['name'] not in _SUBMIT_LOOKAHEAD_NAMES:
            continue
        try:
            s_start_dt = datetime.fromisoformat(s['start_time'])
        except (TypeError, ValueError):
            continue
        _attach_turn_to_following_prompt(s, s_start_dt, prompts_by_start)


def _subagent_starts_by_agent(out: list[dict]) -> dict[str, str]:
    """agent_id → its `subagent.start` span_id."""
    starts: dict[str, str] = {}
    for s in out:
        if s['name'] != 'subagent.start':
            continue
        aid = (s.get('attributes') or {}).get('agent_id')
        if aid:
            starts[aid] = s['span_id']
    return starts


def _reparent_subagents(out: list[dict]) -> None:
    """Nest subagent-owned spans under their `subagent.start`. Claude Code
    tags every hook payload fired inside a subagent with `agent_id`,
    persisted onto the resulting span; here we redirect any such span onto
    its subagent so the tree becomes prompt → subagent → its tool calls.
    Structural (keyed on agent_id), not chronological."""
    starts_by_agent = _subagent_starts_by_agent(out)
    if not starts_by_agent:
        return
    for s in out:
        if s['name'] == 'subagent.start':
            continue
        aid = (s.get('attributes') or {}).get('agent_id')
        target = starts_by_agent.get(aid) if aid else None
        if target and target != s['span_id']:
            s['parent_id'] = target


def _span_id_suffix(span_id: str) -> str:
    """The `<uuid[:13]>` tail of a deterministic span id (`prompt-<u13>`,
    `resp-<u13>`, …). Empty for ids without a prefix. The tail is matched
    against a rewind's orphan key set."""
    return span_id.split('-', 1)[1] if '-' in span_id else ''


def _belongs_to_branch(span: dict, keys: set) -> bool:
    """True when `span` is part of a rewind's discarded branch: its
    deterministic-id tail is an orphan key (`prompt-`/`resp-`/`think-`/`cmd-`),
    or its `turn_uuid` belongs to an orphan turn — the path for tool spans,
    which are keyed by their issuing turn rather than their own id. Both match
    against the same `<uuid[:13]>` key set."""
    if _span_id_suffix(span['span_id']) in keys:
        return True
    tu = _turn_uuid_of(span)
    return bool(tu and tu[:13] in keys)


def _stamp_rewound(span: dict, fork_id: str, abandoned_prompt_ids: set) -> None:
    """Mark one span as belonging to a discarded branch; re-parent it under
    the marker when it is one of the branch's abandoned prompt anchors."""
    attrs = dict(span.get('attributes') or {})
    attrs['rewound_away'] = True
    attrs['rewind_fork_id'] = fork_id
    span['attributes'] = attrs
    if span['span_id'] in abandoned_prompt_ids:
        span['parent_id'] = fork_id


def _apply_rewind_marker(out: list[dict], marker: dict) -> None:
    """Stamp `rewound_away` + `rewind_fork_id` onto every span belonging to
    one rewind's discarded branch, and re-parent its abandoned prompt anchors
    under the marker so the branch collapses behind it."""
    a = marker.get('attributes') or {}
    keys = set(a.get('orphan_keys') or ())
    abandoned_prompt_ids = {f'prompt-{k}' for k in (a.get('abandoned_prompt_keys') or ())}
    fork_id = marker['span_id']
    for s in out:
        if s['span_id'] == fork_id or s['name'] in _REWIND_BOUNDARY_NAMES:
            continue
        if _belongs_to_branch(s, keys):
            _stamp_rewound(s, fork_id, abandoned_prompt_ids)


def _mark_rewound_away(out: list[dict]) -> None:
    """Apply every `/rewind` marker present in the window. No-op (one cheap
    scan) on the overwhelming majority of sessions that never rewind."""
    markers = [s for s in out if s['name'] in _REWIND_BOUNDARY_NAMES]
    for marker in markers:
        _apply_rewind_marker(out, marker)


def _graft_orphans(spans: list[dict]) -> list[dict]:
    """Return a copy of `spans` with parentage filled in. Does not mutate
    inputs or any DB row.

    Parentage is resolved deterministically wherever possible, with a
    chronological fallback only for spans that have no turn linkage:
      1. orphan prompts + boundaries → the conversation span (if any)
      2. dangling-parent self-heal
      3. `_ladder_orphans_by_turn` — the PREFERENCE pass: NULL-parent
         spans with a turn_uuid nest under `resp-`/`think-`/prompt anchor
         by deterministic id existence (the write-time parents from
         turn_trace usually mean there's nothing left to do here).
      4. `_graft_orphans_under_prompt` — chronological FALLBACK for the
         spans the ladder couldn't place (attachments, rule.check,
         permission.request, local commands, …): nest under the current
         real prompt. Harmless to the bug-prone spans because step 3 and
         the write-time posters already parented them — this only touches
         genuinely turn-less session events.
      5. `turn`-span lookahead re-attribution
      6. subagent re-parent by agent_id
    """
    if not spans:
        return spans
    out = [dict(s) for s in spans]
    sorted_spans = sorted(out, key=lambda s: s['start_time'])

    conversations = [s for s in out if s['name'] == 'conversation']
    if conversations:
        _graft_conversation_roots(out, conversations[0]['span_id'])

    by_id = {s['span_id']: s for s in out}
    _heal_dangling_parents(out, by_id)
    _ladder_orphans_by_turn(out, by_id)
    _graft_orphans_under_prompt(sorted_spans, by_id)
    _relabel_turns_by_lookahead(out)
    _reparent_subagents(out)
    # Last: override the parentage above for discarded branches so abandoned
    # prompts collapse under their `rewind` marker instead of sitting as
    # conversation roots. Pure no-op when the window has no rewind markers.
    _mark_rewound_away(out)
    return out


def _widen_envelopes(spans: list[dict]) -> list[dict]:
    """Return a copy where every parent span's start/end covers all of its
    children. Pure — does not touch the DB.
    """
    if not spans:
        return spans
    out = [dict(s) for s in spans]
    by_id = {s['span_id']: s for s in out}

    children_by_parent: dict[str, list[str]] = {}
    for s in out:
        pid = s.get('parent_id')
        if pid:
            children_by_parent.setdefault(pid, []).append(s['span_id'])

    for parent_id, child_ids in children_by_parent.items():
        parent = by_id.get(parent_id)
        if not parent:
            continue
        child_spans = [by_id[c] for c in child_ids if c in by_id]
        if not child_spans:
            continue
        child_starts = [c['start_time'] for c in child_spans]
        child_ends = [c.get('end_time') or c['start_time'] for c in child_spans]
        new_start = min(parent['start_time'], *child_starts)
        new_end = max(parent.get('end_time') or parent['start_time'], *child_ends)
        parent['start_time'] = new_start
        parent['end_time'] = new_end
        # Widen start/end for layout, but never shrink a semantic duration.
        # Point-in-time spans (assistant_response / assistant.thinking carry
        # inference latency with start==end) would otherwise have their real
        # duration_ms overwritten by the trivial child-envelope width when a
        # server-side tool nests under them. Children fire during inference,
        # so the envelope never legitimately exceeds the semantic value.
        envelope_ms = int(
            (datetime.fromisoformat(new_end) - datetime.fromisoformat(new_start)).total_seconds() * 1000
        )
        parent['duration_ms'] = max(int(parent.get('duration_ms') or 0), envelope_ms)
    return out


def _persist_projection(conn, trace_id: str, raw: list[dict], projected: list[dict]) -> dict:
    """Apply the differences between `raw` and `projected` back to the DB.

    Returns counts of the writes made. Caller is responsible for commit().
    """
    raw_by_id = {s['span_id']: s for s in raw}
    parent_updates = 0
    envelope_updates = 0

    for s in projected:
        prev = raw_by_id.get(s['span_id'])
        if prev is None:
            continue
        if s.get('parent_id') != prev.get('parent_id'):
            conn.execute(
                "UPDATE session_spans SET parent_id = ? WHERE span_id = ? AND trace_id = ?",
                (s.get('parent_id'), s['span_id'], trace_id),
            )
            parent_updates += 1
        changed_start = s['start_time'] != prev['start_time']
        changed_end = s.get('end_time') != prev.get('end_time')
        if changed_start or changed_end:
            conn.execute(
                """UPDATE session_spans
                   SET start_time = ?, end_time = ?, duration_ms = ?
                   WHERE span_id = ? AND trace_id = ?""",
                (s['start_time'], s.get('end_time'), s.get('duration_ms'),
                 s['span_id'], trace_id),
            )
            envelope_updates += 1
    return {'parent_updates': parent_updates, 'envelope_updates': envelope_updates}


_ACTIVE_GAP_THRESHOLD_MS = 60_000   # gaps ≤ this count as active processing
_ACTIVE_CONTAINER_NAMES = frozenset({
    'prompt', 'task.notification', 'conversation',
    'session.start', 'session.end',
    'compact.pre', 'compact.post', 'rewind',
    'subagent.start', 'subagent.stop',
})


def _compute_active_work_ms(spans: list[dict]) -> int:
    """Sum of short inter-event gaps among operation spans.

    Tool/turn/skill/rule spans in this codebase are point-in-time
    events — PostToolUse hooks fire after the tool returns, so
    `start_time == end_time`. Summing widths therefore yields zero,
    while summing root-prompt widths overcounts (prompts that the
    user walked away from mid-turn end up spanning days).

    Instead we sort the operation events by timestamp and add up
    consecutive gaps that are ≤ `_ACTIVE_GAP_THRESHOLD_MS`. Short
    gaps represent the agent doing something (model thinking, tool
    overhead, hook plumbing); longer gaps mean the user stepped
    away. Container spans (prompts, conversations, subagent markers,
    session lifecycle) are excluded — they're envelopes around the
    real events, not operations themselves.

    Pure — no DB. Caller passes the already-grafted+widened spans for
    consistency with what the UI renders, but the answer only depends
    on operation timestamps.
    """
    if not spans:
        return 0
    has_children: set[str] = set()
    by_id = {s['span_id']: s for s in spans}
    for s in spans:
        pid = s.get('parent_id')
        if pid and pid in by_id:
            has_children.add(pid)
    timestamps: list[datetime] = []
    for s in spans:
        if s['span_id'] in has_children:
            continue
        if s.get('name') in _ACTIVE_CONTAINER_NAMES:
            continue
        try:
            timestamps.append(datetime.fromisoformat(s['start_time']))
        except (TypeError, ValueError):
            continue
    if len(timestamps) < 2:
        return 0
    timestamps.sort()
    total_ms = 0
    for i in range(1, len(timestamps)):
        gap_ms = int((timestamps[i] - timestamps[i - 1]).total_seconds() * 1000)
        if 0 < gap_ms <= _ACTIVE_GAP_THRESHOLD_MS:
            total_ms += gap_ms
    return total_ms


def _build_span_tree(spans: list[dict]) -> list[dict]:
    """Produce the TreeTable-shaped node list the Vue frontend consumes."""
    if not spans:
        return []
    span_lookup = {s['span_id']: s for s in spans}
    children_map: dict[str, list[dict]] = {}
    roots: list[dict] = []
    for span in spans:
        pid = span.get('parent_id')
        if pid and pid in span_lookup:
            children_map.setdefault(pid, []).append(span)
        else:
            roots.append(span)

    for pid in children_map:
        children_map[pid].sort(key=lambda s: (s['start_time'], s['id']))
    roots.sort(key=lambda s: (s['start_time'], s['id']))

    def _to_nodes(spans_list):
        nodes = []
        for span in spans_list:
            child_spans = children_map.get(span['span_id'], [])
            child_nodes = _to_nodes(child_spans) if child_spans else []

            start_time = span['start_time']
            end_time = span['end_time'] or span['start_time']
            for child in child_spans:
                c_start = child['start_time']
                c_end = child.get('end_time') or c_start
                if c_start < start_time:
                    start_time = c_start
                if c_end > end_time:
                    end_time = c_end

            data = {
                **span,
                'start_time': start_time,
                'end_time': end_time,
            }
            if child_nodes:
                data['isGroup'] = True
                data['spans'] = child_spans
            else:
                data['isSpan'] = True

            node = {'key': f"span-{span['id']}", 'data': data}
            if child_nodes:
                node['children'] = child_nodes
            nodes.append(node)
        return nodes

    return _to_nodes(roots)
