"""Orphan grafting: attach parentless spans to their prompt / turn, reparent
nested subagents, and mark rewound branches."""

from __future__ import annotations

from datetime import datetime

from lib.trace.pending_spans import is_agent_scoped_prompt
from lib.trace.projection_agents import AGENT_ROOT_KEY, _owning_start_id


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


def _source_prompt_id_of(span: dict) -> str | None:
    """Issuing-prompt id from the promoted column, falling back to the
    attribute the hook stamped (`attributes.source_prompt_id`) for rows
    written before the column was promoted. Same column-then-attribute
    shape as `_turn_uuid_of`."""
    return span.get('source_prompt_id') or (span.get('attributes') or {}).get('source_prompt_id')


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


def _build_prompt_by_source_id(out: list[dict]) -> dict[str, str]:
    """prompt_id value → prompt anchor span_id, learned from the
    `prompt` anchors that carry an `attributes.prompt_id` (stamped from the
    transcript's `promptId` by the turn_trace poster). The join partner for a
    tool span's `source_prompt_id`: the hook envelope mirrors the same
    `promptId` value onto every tool span, so this lets a NULL-parent tool
    nest under its issuing prompt by CLI ground truth. Legacy anchors emitted
    before the stamp carry no `prompt_id` and never enter this map."""
    by_pid: dict[str, str] = {}
    for s in out:
        if s['name'] != 'prompt':
            continue
        pid = (s.get('attributes') or {}).get('prompt_id')
        if isinstance(pid, str) and pid:
            by_pid[pid] = s['span_id']
    return by_pid


def _rung0_source_prompt_parent(
    s: dict, prompt_by_source_id: dict[str, str], by_id: dict,
) -> str | None:
    """Rung 0 candidate: the prompt anchor a non-anchor orphan's
    `source_prompt_id` names by value (CLI ground truth), or None. The
    `resp-`/`think-` anchors are excluded — they are the turn's prompt-level
    PEERS, so letting one take a prompt parent here would bypass the
    no-2-cycle rule the turn_uuid rungs below enforce."""
    if s['name'] in _RESP_THINK_ANCHOR_NAMES:
        return None
    spid = _source_prompt_id_of(s)
    anchor = prompt_by_source_id.get(spid) if spid else None
    if anchor and anchor in by_id and anchor != s['span_id']:
        return anchor
    return None


def _anchor_orphan_parent(t: str | None, prompt_by_turn: dict, by_id: dict, span_id: str) -> str | None:
    """Candidate parent for an orphan `resp-`/`think-` anchor: the turn's
    prompt anchor only — never rung 0 (see `_rung0_source_prompt_parent`) and
    never the OTHER turn-level anchor, which would form a 2-cycle."""
    if not t:
        return None
    cand = prompt_by_turn.get(t)
    return cand if cand and cand in by_id and cand != span_id else None


def _turn_anchor_parent(t: str | None, by_id: dict, span_id: str) -> str | None:
    """Candidate parent from the turn's own `resp-<turn>` / `think-<turn>`
    anchors, in that order, or None when neither exists in the window."""
    if not t:
        return None
    for cand in (f'resp-{t[:13]}', f'think-{t[:13]}'):
        if cand in by_id and cand != span_id:
            return cand
    return None


def _ladder_parent_for(
    s: dict, t: str | None, prompt_by_turn: dict,
    prompt_by_source_id: dict, by_id: dict,
) -> str | None:
    """Candidate parent for a non-anchor orphan, in precedence order:
    `resp-<turn>` → `think-<turn>` → rung 0 `source_prompt_id` value-join →
    the turn's prompt anchor. The turn-level anchors win first — they are the
    finer-grained parent; rung 0 (CLI ground truth, the hook envelope's
    issuing-prompt id) only fires when neither exists for the turn, but it
    still beats the turn's own coarser prompt anchor. Also catches a tool
    span whose `turn_uuid` never got attributed (which would otherwise skip
    the turn_uuid gate and fall to the chronological fallback)."""
    cand = _turn_anchor_parent(t, by_id, s['span_id'])
    if cand:
        return cand
    rung0 = _rung0_source_prompt_parent(s, prompt_by_source_id, by_id)
    if rung0:
        return rung0
    if not t:
        return None
    cand = prompt_by_turn.get(t)
    return cand if cand and cand in by_id and cand != s['span_id'] else None


def _ladder_orphans_by_turn(out: list[dict], by_id: dict) -> None:
    """Deterministic parent ladder for NULL-parent, non-boundary spans,
    chosen purely by deterministic id existence / stamped value (no
    timestamps) — see `_ladder_parent_for` for the precedence. This is the
    preference pass; spans it can't place (no source_prompt_id, no
    turn_uuid, or none of the rungs exist) are left NULL for the
    chronological `_graft_orphans_under_prompt` fallback. Mutates in place.

    The `resp-`/`think-` anchors of a turn are PEERS, not parent/child. An
    orphan anchor (a turn with no write-time prompt parent — chiefly a
    `task.notification`/background-completion turn that had no user-prompt
    anchor) must skip the anchor rungs, or `think-<t>` and `resp-<t>` pick
    each other and form a 2-cycle that `_build_span_tree` can't root,
    silently dropping the whole turn (and its child tools) — `_anchor_orphan_
    parent` takes the prompt rung only.

    Load-bearing for migration: an old session (parent_id NULL but
    turn_uuid populated, once repaired so the resp/think anchors carry
    prompt parents) heals here at read time with zero re-emission. A span
    whose anchor carries no stamped `prompt_id` falls straight through rung 0
    to the turn's prompt anchor unchanged."""
    prompt_by_turn = _build_prompt_by_turn(out)
    prompt_by_source_id = _build_prompt_by_source_id(out)
    for s in out:
        if s.get('parent_id') is not None:
            continue
        if s['name'] in _LADDER_SKIP_NAMES:
            continue
        t = _turn_uuid_of(s)
        if s['name'] in _RESP_THINK_ANCHOR_NAMES:
            cand = _anchor_orphan_parent(t, prompt_by_turn, by_id, s['span_id'])
        else:
            cand = _ladder_parent_for(s, t, prompt_by_turn, prompt_by_source_id, by_id)
        if cand:
            s['parent_id'] = cand


def _fallback_orphan_anchor(out: list[dict]) -> dict | None:
    """The span orphans graft under when a trace has no `prompt` of its own,
    or None when the trace has to stay flat.

    Only a subagent trace (`sa-<agent_id>`, see lib/trace/subagent_trace) has
    one. Three quarters of stored agents never got a `prompt-sa-…` launch
    prompt, and without an anchor every one of their spans projects as a root
    — a flat pile instead of a conversation. The turn derived from the agent's
    own start marker (`_agent_root_span`) is emitted for exactly that case and
    holds the tree. A launch prompt still wins where one exists, and an agent
    with neither stays flat rather than getting a synthesized anchor.

    Only that derived turn qualifies, never any `prompt` in the window: a
    NESTED agent's `prompt-sa-…` is another run's opening, and anchoring on it
    would hang this agent's work inside the one it delegated to.

    A parent trace is deliberately excluded: there a `subagent.start` is one
    marker among many agents', and grafting main-loop orphans under it would
    nest unrelated work inside a subagent.
    """
    from lib.trace.subagent_trace import is_subagent_trace_id

    if not out or not is_subagent_trace_id(out[0].get('trace_id')):
        return None
    roots = [s for s in out
             if (s.get('attributes') or {}).get(AGENT_ROOT_KEY)]
    return roots[0] if roots else None


def _graft_orphans_under_prompt(sorted_spans: list[dict], by_id: dict,
                                anchor: dict | None = None) -> None:
    """Graft each orphan span under the most recent `prompt`, or under
    `anchor` until one appears. Mutates the dicts in `by_id` in place.

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
    current_prompt = anchor
    for span in sorted_spans:
        resolved = by_id[span['span_id']]
        if resolved['name'] == 'prompt' and not is_agent_scoped_prompt(resolved):
            current_prompt = resolved
        elif resolved['name'] in _FIRST_CLASS_BOUNDARY_NAMES:
            continue
        elif (current_prompt is not None and resolved is not current_prompt
                and not resolved.get('parent_id')):
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
        (s for s in out if s['name'] == 'prompt' and not _is_pending(s)
         and not is_agent_scoped_prompt(s)),
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


def _subagent_starts_by_agent(out: list[dict]) -> dict[str, list[dict]]:
    """agent_id → its `subagent.start` spans, in ingest (DB id) order."""
    starts: dict[str, list[dict]] = {}
    for s in out:
        if s['name'] != 'subagent.start':
            continue
        aid = (s.get('attributes') or {}).get('agent_id')
        if aid:
            starts.setdefault(aid, []).append(s)
    for lst in starts.values():
        lst.sort(key=lambda s: s.get('id') or 0)
    return starts


def _raw_parent_agent_edges(starts_by_agent: dict[str, list[dict]]) -> dict[str, str]:
    """agent_id → parent_agent_id, one edge per agent, read straight off its
    first start's `attributes.parent_agent_id` (self-references excluded).
    May still contain cycles — `_drop_cyclic_agent_edges` filters those."""
    edges: dict[str, str] = {}
    for agent_id, starts in starts_by_agent.items():
        for st in starts:
            parent = (st.get('attributes') or {}).get('parent_agent_id')
            if parent and parent != agent_id:
                edges[agent_id] = parent
                break
    return edges


def _drop_cyclic_agent_edges(edges: dict[str, str]) -> dict[str, str]:
    """Drop any agent→parent edge whose parent chain walks back to the agent
    itself (mutual/2-cycle or longer)."""
    dropped: set[str] = set()
    for child in edges:
        visited = {child}
        cur = edges.get(child)
        while cur is not None:
            if cur in visited:
                dropped.add(child)
                break
            visited.add(cur)
            cur = edges.get(cur)
    return {c: p for c, p in edges.items() if c not in dropped}


def _nested_parent_agent_edges(starts_by_agent: dict[str, list[dict]]) -> dict[str, str]:
    """agent_id → parent_agent_id, one edge per agent, with any self-reference
    or cycle dropped — the same precision-over-recall guard `claude_subagents.
    _resolve_parent_agents` applies at write time, reapplied here so LEGACY
    rows stamped before that guard existed (or any other bad data) can't feed
    `_build_span_tree` a mutually-referencing pair that vanishes entirely
    (neither end has a reachable root)."""
    return _drop_cyclic_agent_edges(_raw_parent_agent_edges(starts_by_agent))


def _reparent_nested_start(
    start_span: dict, starts_by_agent: dict, safe_parent_edges: dict[str, str],
) -> None:
    """A depth>=2 subagent's `subagent.start` nests under its SPAWNING agent's
    owning `subagent.start` segment (the latest such start ingested at/before
    it) when `attributes.parent_agent_id` resolves — the nested-spawn edge
    stamped by lib/trace/claude_subagents. Missing/legacy (no parent_agent_id,
    the edge didn't survive `_nested_parent_agent_edges`'s cycle guard, or the
    parent's start isn't in the window) → left as-is (flat under the main
    thread), preserving prior behavior."""
    parent_agent = (start_span.get('attributes') or {}).get('parent_agent_id')
    if not parent_agent:
        return
    agent_id = (start_span.get('attributes') or {}).get('agent_id')
    if safe_parent_edges.get(agent_id) != parent_agent:
        return
    target = _owning_start_id(starts_by_agent.get(parent_agent, []), start_span)
    if target and target != start_span['span_id']:
        start_span['parent_id'] = target


def _reparent_subagents(out: list[dict]) -> None:
    """Nest subagent-owned spans under their `subagent.start`. Claude Code
    tags every hook payload fired inside a subagent with `agent_id`,
    persisted onto the resulting span; here we redirect any such span onto
    its subagent so the tree becomes prompt → subagent → its tool calls.
    A resumed agent (SendMessage) emits a NEW subagent.start under the SAME
    agent_id, so ownership is per SEGMENT — the latest start preceding the
    span in ingest order. A flat agent_id → last-start map would pull the
    whole history (including the first segment's stop) under the resumed
    start, and the envelope widening would then drag that start's
    start_time back to the first segment — breaking every consumer that
    anchors an elapsed to it.

    A `subagent.start` is not reparented by `agent_id` (that would nest it
    under itself); instead, a nested (depth>=2) start reparents under its
    SPAWNING agent's start via `parent_agent_id` — `_reparent_nested_start`."""
    starts_by_agent = _subagent_starts_by_agent(out)
    if not starts_by_agent:
        return
    safe_parent_edges = _nested_parent_agent_edges(starts_by_agent)
    for s in out:
        if s['name'] == 'subagent.start':
            _reparent_nested_start(s, starts_by_agent, safe_parent_edges)
            continue
        aid = (s.get('attributes') or {}).get('agent_id')
        target = _owning_start_id(starts_by_agent.get(aid, []), s) if aid else None
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
         real prompt, or under `_fallback_orphan_anchor` where a trace has
         no prompt at all. Harmless to the bug-prone spans because step 3 and
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
    _graft_orphans_under_prompt(sorted_spans, by_id, _fallback_orphan_anchor(out))
    _relabel_turns_by_lookahead(out)
    _reparent_subagents(out)
    # Last: override the parentage above for discarded branches so abandoned
    # prompts collapse under their `rewind` marker instead of sitting as
    # conversation roots. Pure no-op when the window has no rewind markers.
    _mark_rewound_away(out)
    return out
