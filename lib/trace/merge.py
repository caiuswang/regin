"""Serve-time merge of the append-only span store into one canonical view.

`session_spans` is written append-only by two independent sources — live
hook events (`source='hook'`: tool timing, permissions, skill reads, the
in-flight `promptlive-` prompt placeholder) and the transcript scan
(`source='transcript'`: the real `prompt-<uuid>` anchor,
assistant_response/thinking, local commands). The two disagree about the
same turn: a placeholder and its real anchor coexist; a pending tool/
permission span coexists with its resolved counterpart.

`merge_spans` is the SINGLE place that reconciles them. It runs at read
time as a pure function over the rows of one window — no DB writes, no
mutation — so the store stays append-only and projection rules can change
without a data migration (they self-heal on the next read).

It owns four things, in order:

  1. **dedup / supersession** — drop the transient placeholder/pending rows
     whose resolved counterpart is present in the window (this replaces the
     old ingest-time DELETE/promote machinery: `_retire_superseded_pending`,
     `_retire_pending_permissions`, `_sweep_superseded_pending_blockers`).
  2. **reparent** — `_graft_orphans` (the 3-tier ladder in projection.py).
  3. **reorder** — `_graft_orphans` already sorts; `_build_span_tree`
     (downstream) does the final stable sort by (start_time, id).
  4. **link** — `_link_background_tasks` joins a backgrounded `Bash` call to
     the `task.notification` its shell emitted on completion, by task id.

When the resolved counterpart is NOT yet present (a prompt still in flight,
a permission still blocking), the placeholder is kept — that is exactly how
the live view shows in-progress work. Over an already-reconciled historical
window (no placeholders/pending left) every drop rule is a no-op, so
`merge_spans(raw) == _graft_orphans(raw)` — the idempotency/no-regression
property the read path relies on.
"""

from __future__ import annotations

import re
from datetime import datetime

from lib.trace.pending_spans import (
    INACTIVE_THRESHOLD_SEC,
    PROMPT_PLACEHOLDER_PREFIX,
    is_agent_scoped_prompt,
    is_human_blocker,
    is_pending_span_id,
    parse_naive_ts as _merge_ts,
    pending_id_for_resolved,
)
from lib.trace.alias import ORIGIN_KEY as _ORIGIN_KEY
from lib.trace.projection import _graft_orphans

# A bare slash command echo, e.g. `/goal-verified`, `/git:commit`. The resolved
# transcript anchor for a slash command holds only this collapsed token; the
# live `promptlive-` placeholder holds its full expansion (the placeholder text
# STARTS WITH this echo). Used to pair the two so the expansion isn't lost when
# the placeholder is otherwise dropped as stale.
_SLASH_COMMAND_RE = re.compile(r'^/[\w:-]+$')

# The suffix a byte-capped prompt anchor carries when its text was truncated at
# post time (`hook_manager/.../span_posters.py::_truncate_utf8_with_marker`, cap
# `_PROMPT_ANCHOR_TEXT_MAX_BYTES`). A resolved anchor ending in this marker is a
# truncated view whose full text still lives on the live `promptlive-`
# placeholder — the same rescue the slash-command echo gets.
_PROMPT_ANCHOR_TRUNC_MARKER = '\n…[truncated]'


def _attrs(span: dict) -> dict:
    a = span.get('attributes')
    return a if isinstance(a, dict) else {}


def _inherit_turn_linkage(survivor: dict, placeholder: dict) -> dict:
    """Hand a retired tool placeholder's `turn_uuid` + `resp-`/`think-` parent
    to the resolved span that supersedes it, when the survivor never got its
    own.

    A slow tool emits its `pending-<tu>` placeholder at PreToolUse and its
    resolved `tool.<Name>` at PostToolUse, both parent-less / turn_uuid-less.
    If a `turn_trace` attribution pass lands while only the placeholder exists,
    the placeholder absorbs the turn linkage and the later resolved span never
    does (the turn is cached, so it's never re-attributed). Dropping the
    placeholder here would then strand the resolved span on the prompt-root
    graft fallback — the assistant-response branch visibly collapses.

    Gate on a NULL `turn_uuid`: attribution always sets `turn_uuid` alongside
    the parent, so its absence is the unambiguous mark of the un-attributed
    survivor. That also makes the transfer materialize-proof — `_persist_
    projection` writes `parent_id` but never `turn_uuid`, so a prompt-root
    parent a prior materialize may have baked onto the survivor is still
    overridden here. Returns a copy when it changes anything (merge stays
    pure)."""
    if survivor.get('turn_uuid') or not placeholder.get('turn_uuid'):
        return survivor
    out = dict(survivor)
    out['turn_uuid'] = placeholder.get('turn_uuid')
    if placeholder.get('parent_id'):
        out['parent_id'] = placeholder.get('parent_id')
    return out


def _classify_supersessions(spans: list[dict], placeholders: dict) -> tuple[set, dict]:
    """Walk the window once: return the placeholder keys each resolved span
    supersedes (`drop`) and, for a resolved *tool* span, the `pending-` tool
    placeholder it should inherit turn linkage from (`inherit`).

    Both sides are gated on `tool.` rather than name-equality: the pending
    span is minted `tool.{raw}` while the resolved one is `tool.{normalize}`,
    so an exact-name guard would silently miss any tool `_normalize_tool_name`
    rewrites (today none, but it's documented to collapse MCP names). The
    placeholder is already matched by the survivor's own tool_use_id, whose
    only two candidates are `pending-<tu>` (tool) and `permreq-<tu>`
    (permission.request) — the `tool.` check keeps the former and drops the
    latter without reparenting the resolved tool span.

    `protect` outranks `drop` across candidates: when one resolved span is a
    placeholder's expansion anchor, the placeholder must live until
    `_absorb_slash_command_expansions` lifts its full text onto that anchor —
    another candidate hitting the same hash (the SDK delivery echo of the same
    prompt) must not retire it first."""
    drop: set[tuple] = set()
    protect: set[tuple] = set()
    inherit: dict = {}  # survivor span_id → placeholder it should inherit from
    for s in spans:
        is_tool = (s.get('name') or '').startswith('tool.')
        for pid in pending_id_for_resolved(s, _attrs(s)):
            key = (s.get('trace_id'), pid)
            _record_supersession(s, is_tool, key, placeholders.get(key),
                                 drop, protect, inherit)
    return drop - protect, inherit


def _record_supersession(
    s: dict, is_tool: bool, key: tuple, ph: dict | None,
    drop: set, protect: set, inherit: dict,
) -> None:
    """Classify one (resolved span, superseded placeholder) pair.

    A resolved prompt anchor that is only a truncated/echo view of its
    placeholder (the placeholder holds the untruncated prompt) is not dropped
    but PROTECTED — left for `_absorb_slash_command_expansions` to lift the
    full text onto the anchor first, even when another candidate votes to
    drop the same key. A non-matching placeholder still drops normally."""
    if ph is not None and _is_expansion_anchor(s, ph, _attrs(ph).get('text') or ''):
        protect.add(key)
        return
    if _foreign_prompt_placeholder(s, ph):
        return
    drop.add(key)
    if is_tool and ph is not None and (ph.get('name') or '').startswith('tool.'):
        inherit[s.get('span_id')] = ph


def _foreign_prompt_placeholder(s: dict, ph: dict | None) -> bool:
    """True when `s` is another writer's row hitting this prompt placeholder's
    text hash without any real claim to it.

    The `promptlive-` hash is a within-writer convention: a foreign resolved
    prompt recomputes the same digest from the same text, and retiring the
    placeholder on that alone loses the untruncated text the absorb rescue
    would have transferred (the bug behind session 891cfee2's duplicate
    prompt). A cross-writer candidate may retire it only when it is
    anchor-identified — `entry_uuid` proves it will collapse with the
    canonical anchor by span id — or names the row outright
    (`pending_span_id`)."""
    if ph is None or ph.get('name') != 'prompt':
        return False
    if s.get(_ORIGIN_KEY) == ph.get(_ORIGIN_KEY):
        return False
    a = _attrs(s)
    if a.get('entry_uuid'):
        return False
    return a.get('pending_span_id') != ph.get('span_id')


def _drop_superseded_placeholders(spans: list[dict]) -> list[dict]:
    """Drop the `promptlive-`/`pending-`/`permreq-` placeholder rows whose
    resolved span is present in the window, keyed by `pending_id_for_resolved`
    (prompt-text hash / tool_use_id). A placeholder with no resolved
    counterpart in the window survives — that's the in-flight prompt / blocking
    tool the live view must still show.

    Before dropping a tool placeholder, hand its turn linkage to the resolving
    tool span (`_inherit_turn_linkage`) so a slow tool keeps its branch."""
    placeholders = {
        (s.get('trace_id'), s.get('span_id')): s
        for s in spans if is_pending_span_id(s.get('span_id'))
    }
    drop, inherit = _classify_supersessions(spans, placeholders)
    if not drop:
        return spans
    out: list[dict] = []
    for s in spans:
        if (s.get('trace_id'), s.get('span_id')) in drop:
            continue
        ph = inherit.get(s.get('span_id'))
        out.append(_inherit_turn_linkage(s, ph) if ph is not None else s)
    return out


def _gate_resolved_tool_name(span: dict) -> str | None:
    """The tool_name whose permission gate this span resolves, or None: a
    non-pending `tool.<X>` means the gate was granted (the tool ran); a
    `permission.denied` means it was denied."""
    name = span.get('name') or ''
    if name.startswith('tool.') and span.get('status_code') != 'PENDING':
        return _attrs(span).get('tool_name') or name[len('tool.'):]
    if name == 'permission.denied':
        return _attrs(span).get('tool_name')
    return None


def _placeholder_calls_by_gate(spans: list[dict]) -> dict:
    """(trace_id, gate_key) → the tool_use_id of the call a single open
    placeholder speaks for. A key two placeholders answer to names nothing and
    is dropped — the same refusal `_pair_open_gates` makes."""
    seen: dict = {}
    for s in spans:
        key = _open_gate_key(s)
        if key is None or not (s.get('name') or '').startswith('tool.'):
            continue
        seen[key] = _tool_use_id(s) if key not in seen else None
    return {k: v for k, v in seen.items() if v}


def _gate_resolutions(spans: list[dict]) -> tuple[set, dict, dict]:
    """What the window says about gates that resolved: the calls named outright,
    and the newest resolving row id per tool, split by whether the resolving row
    could name its call at all.

    A `permission.denied` carries no tool_use_id (0 of 95 rows in the live
    store), so it borrows one from the placeholder its `gate_key` points at —
    otherwise one agent's denial retires every other agent's park on that tool.
    When no placeholder answers for the key the row stays unnamed and retires by
    tool, which is what keeps a denial of a placeholder-less tool working."""
    gate_calls = _placeholder_calls_by_gate(spans)
    calls: set = set()
    named: dict[tuple, int] = {}
    unnamed: dict[tuple, int] = {}
    for s in spans:
        tool_name = _gate_resolved_tool_name(s)
        if not tool_name:
            continue
        trace, row_id = s.get('trace_id'), s.get('id') or 0
        call_id = (_tool_use_id(s)
                   or gate_calls.get((trace, _attrs(s).get('gate_key'))))
        if call_id:
            calls.add((trace, call_id))
        bucket = named if call_id else unnamed
        key = (trace, tool_name)
        if row_id > bucket.get(key, -1):
            bucket[key] = row_id
    return calls, named, unnamed


def _request_call_ids(spans: list[dict]) -> dict:
    """(trace_id, span_id) of each open request → the tool_use_id of the call it
    gates, learned from the placeholder it pairs with. The request's own payload
    never carries one."""
    return {
        ident: _tool_use_id(placeholder)
        for ident, placeholder in _pair_open_gates(spans).items()
        if _tool_use_id(placeholder)
    }


def _drop_resolved_permission_requests(spans: list[dict]) -> list[dict]:
    """Drop PENDING `permission.request` rows whose gate resolved in the window.
    (Replaces ingest's `_retire_pending_permissions`.)

    A request that can name its call — via the placeholder it pairs with — is
    retired only by THAT call's resolution. Two subagents parked on different
    calls of the same tool used to take each other down: whichever was granted
    first retired every earlier request for that tool_name, erasing a park the
    other agent was still waiting on.

    Everything else falls back to correlating by `tool_name`, constrained by
    append-only row order so a resolution retires only a request the store
    recorded before it: a request for a tool that emits no placeholder has
    nothing to pair with, and a denial cannot name the call it denied."""
    calls, named, unnamed = _gate_resolutions(spans)
    if not (named or unnamed):
        return spans
    call_of = _request_call_ids(spans)

    def retired(s: dict) -> bool:
        if s.get('name') != 'permission.request' or s.get('status_code') != 'PENDING':
            return False
        trace, row_id = s.get('trace_id'), s.get('id') or 0
        key = (trace, _attrs(s).get('tool_name'))
        call_id = _tool_use_id(s) or call_of.get((trace, s.get('span_id')))
        if call_id:
            return (trace, call_id) in calls or unnamed.get(key, -1) > row_id
        return max(named.get(key, -1), unnamed.get(key, -1)) > row_id

    return [s for s in spans if not retired(s)]


def _open_gate_key(span: dict) -> tuple | None:
    """The still-open gate this row describes, or None.

    Deliberately NOT scoped per agent, though concurrent subagents can each be
    parked: a `permission.request` never carries an `agent_id` (0 of the last
    500 rows measured, against 77 of 500 placeholders that do), so an agent term
    would make every subagent park miss its twin and keep rendering twice. The
    one-candidate rule in `_pair_open_gates` covers what the agent term would
    have: two agents parked on the identical call are two candidates for the
    same key, so neither pairs. They can never be one candidate — whether a
    placeholder exists at all is a pure function of the tool name
    (`pre_tool_trace._should_emit_pending`), so it holds for both agents or
    neither.
    """
    if span.get('status_code') != 'PENDING':
        return None
    key = _attrs(span).get('gate_key')
    if not isinstance(key, str) or not key:
        return None
    return (span.get('trace_id'), key)


def _pair_open_gates(spans: list[dict]) -> dict:
    """(trace, span_id) of each surviving request → the placeholder it absorbs.

    A gate that matches more than one row on either side is left ALONE. The key
    is a digest of the gated call (`pending_spans.gate_key`), so a session that
    runs `git status` twice reuses it — harmless while the rows are paired only
    among unresolved candidates (the earlier call's are long retired), and this
    is the guard that keeps it harmless if that ever fails to hold: an ambiguous
    key means do nothing, never bind one of two.
    """
    requests: dict = {}
    placeholders: dict = {}
    for s in spans:
        key = _open_gate_key(s)
        if key is None:
            continue
        name = s.get('name') or ''
        if name == 'permission.request':
            requests.setdefault(key, []).append(s)
        elif name.startswith('tool.'):
            placeholders.setdefault(key, []).append(s)
    return {
        (r[0].get('trace_id'), r[0].get('span_id')): placeholders[k][0]
        for k, r in requests.items()
        if len(r) == 1 and len(placeholders.get(k, ())) == 1
    }


def _absorb_gate_placeholder(survivor: dict, placeholder: dict) -> dict:
    """Lift what only the PreToolUse side recorded — the call's `tool_use_id`
    and its input-derived display attrs — onto the surviving request. Never
    overwrites: where both describe a field, the request's own value is the
    permission-time truth. Copies, so merge stays pure."""
    base = _attrs(survivor)
    extra = {k: v for k, v in _attrs(placeholder).items() if k not in base}
    if not extra:
        return survivor
    out = dict(survivor)
    out['attributes'] = {**base, **extra}
    return out


def _collapse_open_gates(spans: list[dict]) -> list[dict]:
    """Collapse the two rows describing ONE still-open permission gate into one.

    A gated call is announced twice: PreToolUse writes `pending-<tool_use_id>`,
    then the provider's permission event writes `permission.request`. While the
    gate is open both are PENDING, so the live card drew two cards for one
    block — for a Bash prompt that window is a second, for a parked
    `AskUserQuestion` it is the whole human wait.

    The request is the survivor: it owns the operator's decision affordances
    (`options`, `default_option_id`, `requested_permission`) and it is the span
    the inbox blocker cards reference. It absorbs the placeholder's identity
    instead of losing it.

    Only rows sharing a `gate_key` pair — a provider that does stamp a
    `tool_use_id` on its permission event never reaches here, because the
    deterministic `permreq-<tu>`/`pending-<tu>` handoff already covers it.
    """
    absorb = _pair_open_gates(spans)
    if not absorb:
        return spans
    drop = {(p.get('trace_id'), p.get('span_id')) for p in absorb.values()}
    out: list[dict] = []
    for s in spans:
        ident = (s.get('trace_id'), s.get('span_id'))
        if ident in drop:
            continue
        placeholder = absorb.get(ident)
        out.append(_absorb_gate_placeholder(s, placeholder)
                   if placeholder is not None else s)
    return out


def open_gate_placeholder_ids(raw: list[dict]) -> set:
    """span_ids `_collapse_open_gates` hides while their gate is still OPEN.

    Those rows are hidden by live state, not by supersession, so the reaper
    must not read them as settled. Computed over the same prefix `merge_spans`
    applies before the collapse: a placeholder whose own call already resolved
    is dropped there first, so sharing a `gate_key` with someone else's open
    park never spares it.
    """
    spans = _drop_superseded_placeholders(raw)
    spans = _drop_resolved_permission_requests(spans)
    return {p.get('span_id') for p in _pair_open_gates(spans).values()}


def _tool_use_id(span: dict) -> str | None:
    tu = span.get('tool_use_id') or _attrs(span).get('tool_use_id')
    return tu if isinstance(tu, str) and tu else None


def _denied_call_id(span: dict) -> str | None:
    """The tool_use_id this span marks as user-denied, or None. `denied=True`
    is the deny contract every deny path shares (`deny_detection.
    _deny_skeleton`) — the Claude tool_result sentinel and the
    provider-recorded transcript denial alike."""
    if _attrs(span).get('denied') is not True:
        return None
    return _tool_use_id(span)


def _drop_denied_tool_failures(spans: list[dict]) -> list[dict]:
    """Drop a `tool.failure` row whose exact call is already represented by a
    deny span in the window.

    A provider that both reports a rejected call through PostToolUseFailure and
    records the denial in its own transcript (Kimi, historically) leaves two
    rows for ONE call: the red failure card and the deny marker. The deny is
    the survivor — it carries the denial reason and the tool input, and it is
    the shape the UI styles as a rejection. Matching on the shared tool_use_id
    keeps the rule call-scoped: a genuine failure and an unrelated deny in the
    same session never collide. Serve-time only; `session_spans` stays
    append-only."""
    denied = set()
    for s in spans:
        tu = _denied_call_id(s)
        if tu:
            denied.add((s.get('trace_id'), tu))
    if not denied:
        return spans

    def retired(s: dict) -> bool:
        if s.get('name') != 'tool.failure':
            return False
        return (s.get('trace_id'), _tool_use_id(s)) in denied

    return [s for s in spans if not retired(s)]


def _is_live_prompt_placeholder(span: dict) -> bool:
    sid = span.get('span_id')
    return (
        span.get('name') == 'prompt'
        and span.get('status_code') == 'PENDING'
        and isinstance(sid, str)
        and sid.startswith(PROMPT_PLACEHOLDER_PREFIX)
    )


def _slash_echo_text(span: dict) -> str | None:
    """The bare `/command` echo a resolved prompt anchor carries, or None.

    A genuine slash-command anchor (status not PENDING, real `prompt-<uuid>`
    id) holds only the collapsed command token; that's the echo a placeholder's
    expansion must start with to be its same-turn pair."""
    if span.get('name') != 'prompt' or span.get('status_code') == 'PENDING':
        return None
    if is_pending_span_id(span.get('span_id')):
        return None
    text = _attrs(span).get('text')
    if not isinstance(text, str):
        return None
    stripped = text.strip()
    return stripped if _SLASH_COMMAND_RE.match(stripped) else None


def _anchor_match_prefix(span: dict) -> str | None:
    """The text prefix a placeholder's fuller text must start with to be this
    resolved anchor's expansion. Two anchor shapes qualify: a slash-command
    anchor carrying only its bare `/command` echo, and a byte-capped anchor
    whose text was truncated with `_PROMPT_ANCHOR_TRUNC_MARKER` (its pre-marker
    head). Both are prefixes of the full prompt the placeholder holds. None when
    `span` is not a resolved prompt anchor eligible for absorption."""
    echo = _slash_echo_text(span)
    if echo is not None:
        return echo
    text = _attrs(span).get('text')
    if (span.get('name') == 'prompt' and isinstance(text, str)
            and span.get('status_code') != 'PENDING'
            and not is_pending_span_id(span.get('span_id'))
            and text.endswith(_PROMPT_ANCHOR_TRUNC_MARKER)):
        return text[:-len(_PROMPT_ANCHOR_TRUNC_MARKER)]
    return None


def _is_expansion_anchor(candidate: dict, placeholder: dict, ph_text: str) -> bool:
    """True if `candidate` is a resolved prompt anchor in the same trace whose
    text (its `/command` echo or its truncated head) is a strict prefix of the
    placeholder's fuller text, and whose id is not below the placeholder's (the
    placeholder is minted just before its anchor)."""
    # Same-writer check. `trace_id` alone no longer distinguishes them: an
    # alias group's rows are re-keyed to the canonical id at fetch, so this
    # compared equal across the two writers and let a placeholder pair with the
    # OTHER writer's anchor — parking the expansion on the SDK row and leaving
    # the canonical anchor holding the bare echo.
    if candidate.get(_ORIGIN_KEY) != placeholder.get(_ORIGIN_KEY):
        return False
    if candidate.get('trace_id') != placeholder.get('trace_id'):
        return False
    prefix = _anchor_match_prefix(candidate)
    if prefix is None or not ph_text.startswith(prefix) or len(ph_text) <= len(prefix):
        return False
    ph_id, sid = placeholder.get('id'), candidate.get('id')
    return ph_id is None or sid is None or sid >= ph_id


def _expansion_anchor_for(
    placeholder: dict, spans: list[dict], used: set[tuple],
) -> dict | None:
    """The resolved slash-command anchor whose echo this placeholder expands.

    The placeholder text must START WITH the anchor's bare `/command` echo and
    be strictly longer (the expansion). When several candidate anchors match,
    pick the nearest one by id at-or-after the placeholder; anchors already
    claimed by an earlier placeholder (`used`) are skipped so two `/goal-verified`
    turns ingested back-to-back pair one-to-one instead of both grabbing the
    earliest anchor."""
    ph_text = _attrs(placeholder).get('text')
    if not isinstance(ph_text, str):
        return None
    matches = [
        s for s in spans
        if (s.get('trace_id'), s.get('span_id')) not in used
        and _is_expansion_anchor(s, placeholder, ph_text)
    ]
    if not matches:
        return None
    return min(matches, key=lambda s: (s.get('id') is None, s.get('id') or 0))


def _pair_slash_expansions(
    placeholders: list[dict], spans: list[dict],
) -> tuple[dict[tuple, str], set[tuple]]:
    """Greedily pair each placeholder with its resolved anchor, claiming each
    anchor so it can't be reused. Earliest placeholder takes the earliest
    matching anchor first, so two `/goal-verified` turns ingested back-to-back
    pair one-to-one. Returns (expansion-text by anchor key, placeholder keys to
    drop)."""
    placeholders.sort(key=lambda s: (s.get('id') is None, s.get('id') or 0))
    expansion_by_anchor: dict[tuple, str] = {}
    drop: set[tuple] = set()
    for ph in placeholders:
        anchor = _expansion_anchor_for(ph, spans, set(expansion_by_anchor))
        if anchor is None:
            continue
        key = (anchor.get('trace_id'), anchor.get('span_id'))
        expansion_by_anchor[key] = _attrs(ph).get('text')
        drop.add((ph.get('trace_id'), ph.get('span_id')))
    return expansion_by_anchor, drop


def _absorb_slash_command_expansions(spans: list[dict]) -> list[dict]:
    """Move a placeholder's full prompt text onto its surviving resolved anchor,
    then drop the placeholder.

    Two anchor shapes lose their full text to the merge and are rescued here.
    A slash command (`/goal-verified`) yields a resolved `prompt-<uuid>` anchor
    carrying only the collapsed echo plus a PENDING `promptlive-` placeholder
    with the full expansion. A large prompt (e.g. an external-agent regenerate
    task, >8 KiB) yields a resolved anchor whose text was byte-capped with the
    `\n…[truncated]` marker at post time, while the live placeholder holds the
    untruncated prompt. In both, turn_uuid is NULL on both rows so turn-pairing
    can't help, and `_drop_stale_blockers` would drop the placeholder once a
    later prompt lands — losing the full text. Here we instead transfer it onto
    a COPY of the resolved anchor (status stays OK, so PENDING-excluding
    aggregate readers still see it) and drop the placeholder — mirroring how
    `_inherit_turn_linkage` returns `dict(survivor)` copies to keep the merge
    pure. A stray client-only placeholder (`/workflows`) has no such anchor and
    is left untouched for the existing stale-drop path."""
    placeholders = [s for s in spans if _is_live_prompt_placeholder(s)]
    if not placeholders:
        return spans
    expansion_by_anchor, drop = _pair_slash_expansions(placeholders, spans)
    if not drop:
        return spans
    out: list[dict] = []
    for s in spans:
        sig = (s.get('trace_id'), s.get('span_id'))
        if sig in drop:
            continue
        if sig in expansion_by_anchor:
            survivor = dict(s)
            full = expansion_by_anchor[sig]
            # `chars` travels with the text it counts, as it does when an
            # untruncated body is taken over in `_take_untruncated_text` —
            # otherwise the reader's attribute rail reads `chars 22` beside
            # the 1625-character prompt it is describing.
            survivor['attributes'] = {**_attrs(s), 'text': full,
                                      'chars': len(full)}
            out.append(survivor)
        else:
            out.append(s)
    return out


_STALE_PENDING_NAMES = ('permission.request', 'prompt')


def _is_stale_pending_name(name: str) -> bool:
    return name.startswith('tool.') or name in _STALE_PENDING_NAMES


def _drop_stale_blockers(spans: list[dict], prompt_id_ceiling=None) -> list[dict]:
    """Drop stale PENDING rows superseded by a newer prompt — anything PENDING
    from a prior turn that the user implicitly abandoned by submitting again.
    Keyed on the monotonic `id` (not start_time: anchors are tz-aware,
    placeholders naive). Covers two cases:

      * an *interrupted* blocking tool / permission (AskUserQuestion,
        ExitPlanMode, a permission gate) that never resolved — replaces
        ingest's `_sweep_superseded_pending_blockers`;
      * a stray `promptlive-` prompt placeholder for a client-only command
        (`/workflows`, `/clear`) that never produced a model turn, so no real
        `prompt-<uuid>` anchor ever supersedes it — replaces the
        `reconcile_prompt_spans` deletion (which kept the newest + image-owning
        prompts; real anchors are non-PENDING so they're never touched here).

    The cutoff is the newest prompt id. `prompt_id_ceiling` is the per-trace
    GLOBAL max prompt id, which a windowed reader (fetch_session_paginated)
    passes so a stray that is the newest prompt *within an older scroll-up
    window* still drops — the window-local max alone would wrongly keep it.
    Full-session readers pass None (window == whole session). The genuinely
    newest prompt session-wide equals the ceiling, so it is never < cutoff and
    is always kept."""
    window_max: dict[str, int] = {}
    for s in spans:
        if s.get('name') != 'prompt' or is_agent_scoped_prompt(s):
            continue
        sid = s.get('id')
        if sid is None:
            continue
        tid = s.get('trace_id')
        if sid > window_max.get(tid, -1):
            window_max[tid] = sid
    if not window_max and prompt_id_ceiling is None:
        return spans

    def cutoff_for(tid) -> int | None:
        wm = window_max.get(tid)
        if prompt_id_ceiling is None:
            return wm
        return prompt_id_ceiling if wm is None else max(wm, prompt_id_ceiling)

    def stale(s: dict) -> bool:
        if s.get('status_code') != 'PENDING':
            return False
        if not _is_stale_pending_name(s.get('name') or ''):
            return False
        sid = s.get('id')
        cutoff = cutoff_for(s.get('trace_id'))
        return sid is not None and cutoff is not None and sid < cutoff

    return [s for s in spans if not stale(s)]


# A stuck PENDING that the stale-blocker sweep can't reach — it is NEWER than
# the last prompt, so no later prompt supersedes it — is instead demoted to a
# resolved-interrupted rendering here. `_STALE_PENDING_OLDER_THAN_SEC` guards
# the session-inactive path so a tool that only just started on a
# just-abandoned session isn't prematurely called dead. `INACTIVE_THRESHOLD`
# mirrors the roster stale window (web/blueprints/trace/sessions.py).
INTERRUPT_SOURCE_STALE = 'stale'
_STALE_PENDING_OLDER_THAN_SEC = 60
# Rule (a) grace: a pending is only demoted-as-interrupted once the same agent
# has demonstrably moved on for this long. Set ABOVE the 60s transcript
# backfill gate (repair.has_stuck_pending_tools) so a merely-lost PostToolUse
# always gets a shot at being superseded by its true OK span before we render
# it "⏹ interrupted".
_MOVED_ON_GRACE_SEC = 90


def _is_demotable_pending(span: dict) -> bool:
    """A PENDING tool / permission-request / ask placeholder that could be a
    stuck blocker. Prompt placeholders are excluded from the clock rules —
    they retire via `_drop_stale_blockers` / `_absorb_slash_command_expansions`,
    plus the event-based `session.end` ceiling in `_demote_stale_pending`."""
    if span.get('status_code') != 'PENDING':
        return False
    name = span.get('name') or ''
    return name.startswith('tool.') or name == 'permission.request'


def _latest_move_on_ts(spans: list[dict]) -> dict:
    """`(trace_id, agent_id) → latest assistant_response/prompt start_time`
    (naive local) in ONE pass, so `_moved_on_past_grace` is an O(1) lookup
    rather than a rescan of the whole window per pending span."""
    out: dict = {}
    for s in spans:
        if s.get('name') not in ('assistant_response', 'prompt'):
            continue
        ts = _merge_ts(s.get('start_time'))
        if ts is None:
            continue
        key = (s.get('trace_id'), _attrs(s).get('agent_id'))
        prev = out.get(key)
        if prev is None or ts > prev:
            out[key] = ts
    return out


def _moved_on_past_grace(pending: dict, move_on_ts: dict) -> bool:
    """True when a same-agent `assistant_response`/`prompt` started at least
    `_MOVED_ON_GRACE_SEC` after the pending — Claude demonstrably moved past the
    blocking call AND enough time has elapsed that a lost PostToolUse would have
    been backfilled with the true OK span (rule a). The grace keeps a merely-
    lost result from being mislabeled '⏹ interrupted' seconds after the next
    response lands."""
    p_ts = _merge_ts(pending.get('start_time'))
    if p_ts is None:
        return False
    key = (pending.get('trace_id'), _attrs(pending).get('agent_id'))
    newer = move_on_ts.get(key)
    return newer is not None and (newer - p_ts).total_seconds() >= _MOVED_ON_GRACE_SEC


def _session_is_inactive(session_activity: dict | None, now: datetime) -> bool:
    if not session_activity:
        return False
    if session_activity.get('status') == 'ended':
        return True
    last_seen = _merge_ts(session_activity.get('last_seen'))
    if last_seen is None:
        return False
    return (now - last_seen).total_seconds() > INACTIVE_THRESHOLD_SEC


def _blocks_on_a_human(pending: dict, session_activity: dict | None) -> bool:
    """True for an unresolved permission/ask/plan that is the session's NEWEST
    span on a still-open session — the state rule (b)'s clock cannot see.

    Such a span is why the session went quiet, so its own age is not evidence
    it is stuck; it is the length of the wait. A stuck `tool.Bash` gets no such
    exemption — nothing about it explains the silence. Newest-ness is read off
    the session row's `last_seen` rather than the merge window, which may be a
    scroll-up page that ends before the blocker."""
    if not session_activity or session_activity.get('status') == 'ended':
        return False
    if not is_human_blocker(pending.get('name'), pending.get('span_id')):
        return False
    p_ts = _merge_ts(pending.get('start_time'))
    last_seen = _merge_ts(session_activity.get('last_seen'))
    return p_ts is not None and last_seen is not None and p_ts >= last_seen


def _demote(pending: dict) -> dict:
    """Copy of the pending rendered as resolved-interrupted (status ERROR +
    interrupt markers the frontend already reads). Merge stays pure — a copy,
    never a mutation."""
    out = dict(pending)
    out['status_code'] = 'ERROR'
    out['attributes'] = {
        **_attrs(pending),
        'is_interrupt': True,
        'interrupted': True,
        'interrupt_source': INTERRUPT_SOURCE_STALE,
    }
    return out


def _demote_stale_pending(
    spans: list[dict], session_activity: dict | None = None,
) -> list[dict]:
    """Demote stuck PENDING tool/permission/ask placeholders to a
    resolved-interrupted rendering (see `merge_spans`). Two triggers:

      (a) a same-agent newer `assistant_response`/`prompt` started at least
          `_MOVED_ON_GRACE_SEC` after the pending — Claude moved on and the
          backfill has had its shot, OR
      (b) the session is inactive (ended, or silent past INACTIVE_THRESHOLD)
          and the pending is older than `_STALE_PENDING_OLDER_THAN_SEC` —
          EXCEPT for a human blocker that is the session's newest span on a
          still-open session (`_blocks_on_a_human`), whose age measures the
          wait, not a fault.

    INVARIANT: a legitimately-running long tool on an ACTIVE session with no
    same-agent completion activity after it is never demoted — (a) needs a
    later same-agent move-on past the grace, (b) needs an inactive session.

    A `promptlive-` placeholder gets one extra, event-based rule: a
    `session.end` row with a later monotonic id proves the session ended
    after the submission, so the prompt can never resolve — it demotes
    regardless of any clock. This is the only path that resolves a stuck
    in-flight prompt whose delivery echo never arrived (the run died between
    submit and echo); the last prompt of a live session is untouched."""
    end_ceiling = _session_end_ceiling(spans)
    if not end_ceiling and not any(_is_demotable_pending(s) for s in spans):
        return spans
    now = (session_activity or {}).get('now') or datetime.now()
    inactive = _session_is_inactive(session_activity, now)
    move_on_ts = _latest_move_on_ts(spans)

    def demote(s: dict) -> bool:
        if _moved_on_past_grace(s, move_on_ts):
            return True
        if not inactive or _blocks_on_a_human(s, session_activity):
            return False
        p_ts = _merge_ts(s.get('start_time'))
        return p_ts is not None and \
            (now - p_ts).total_seconds() > _STALE_PENDING_OLDER_THAN_SEC

    def undelivered(s: dict) -> bool:
        if not _is_live_prompt_placeholder(s) or s.get('id') is None:
            return False
        return s['id'] < end_ceiling.get(s.get('trace_id'), -1)

    return [
        _demote(s) if (_is_demotable_pending(s) and demote(s))
        or undelivered(s) else s
        for s in spans
    ]


def _session_end_ceiling(spans: list[dict]) -> dict:
    """Per-trace max id of `session.end` rows — the event that outdates any
    still-pending prompt submitted before it."""
    out: dict = {}
    for s in spans:
        if s.get('name') != 'session.end' or s.get('id') is None:
            continue
        tid = s.get('trace_id')
        out[tid] = max(out.get(tid, -1), s['id'])
    return out


_CROSS_SOURCE_SINGLETON_NAMES = frozenset({'session.start', 'session.end'})
# Assistant spans both writers derive from one API message, so the message's own
# id (`msg_…`, the same string on both) buckets AND pairs them exactly — see
# `_cross_source_key`. Wall-clock distance is never consulted: the two writers'
# stamps for one message sit a whole generation apart by construction (measured
# +0.03s at 73 chars rising past +16s at 3418 chars), and a queued steer's two
# stamps sit *minutes* apart, so any finite window is a duplicate waiting on a
# long enough gap. Rows predating `message_id` capture carry no key and pass
# through unpaired.
_CROSS_SOURCE_MESSAGE_NAMES = frozenset({'assistant_response',
                                         'assistant.thinking'})
# How many head characters key an in-flight prompt placeholder pair. Matches
# the head `prompt_placeholder_id` hashes, so two writers' placeholders for one
# submission — which hold the identical untruncated text — share a key.
_PENDING_PROMPT_KEY_CHARS = 512
# Attribute values that carry no information, so a twin's concrete value wins.
# `session.end` is the case that matters: the child's hook reports the generic
# 'other' for a run the SDK ended deliberately, and only the runner knows it was
# an idle timeout. Dropping the SDK row without this would delete the only
# record of WHY a session stopped.
_VAGUE_ATTR_VALUES = frozenset({None, '', 'other', 'unknown'})


def _tool_span_class(name: str, attrs: dict) -> str:
    """One name for a call, whichever writer described it.

    A FAILED call has two shapes: the hook writes `tool.failure` carrying the
    real tool in `attrs.tool_name`, while the SDK keeps `tool.<Name>` and sets
    ERROR (`post_tool_failure.py`; the same split that made
    `lib/grader/evidence.py` blind to hook-side failures). Keying on the raw
    name filed one call's two rows under different keys, so the failure rendered
    twice — once per writer.
    """
    if name != 'tool.failure':
        return name
    tool = attrs.get('tool_name')
    return f'tool.{tool}' if isinstance(tool, str) and tool else name


def _row_identity(span: dict, name: str, attrs: dict):
    """The identifier this row shares with its twin, or None.

    Decides PAIRING (`_may_pair`): two rows that both name themselves must
    agree; a missing id on either side is "no opinion", never disagreement.
    For assistant rows the same `message_id` is also the bucket key — safe
    because both writers stamp it on every row they emit today, and a row
    without one carries no key at all (it passes through unpaired rather than
    landing in a single-origin bucket that reconciles to itself). The one key
    that must NOT bucket is `permission.request`'s `tool_use_id`: Claude
    Code's PermissionRequest payload omits it on most hook rows, so it can
    only ever refuse a pair, not file rows.
    """
    if name in _CROSS_SOURCE_MESSAGE_NAMES:
        return attrs.get('message_id') or None
    if name == 'permission.request':
        return _tool_use_id(span)
    return None


def _cross_source_identity(span: dict, name: str, attrs: dict):
    """The key that names one EVENT outright, or None.

    Only `tool_use_id` on a tool span qualifies: it is the same `toolu_*` on
    both sides, globally unique, and — unlike the identities above — recorded
    by both writers on every row, so bucketing on it strands nothing.
    """
    call_id = _tool_use_id(span)
    if call_id and name.startswith('tool.'):
        return ('id', _tool_span_class(name, attrs), call_id)
    return None


def _cross_source_class(name: str, attrs: dict):
    """The key that names a CLASS of event, or None.

    A class can repeat within a session (three `session.start`s after two
    resumes), so pairing inside it is positional — see `_pair_cross_source`.
    A row that names no class passes through unpaired: an event only one
    writer described, or one that predates identity capture.
    """
    if name == 'permission.request':
        # Deliberately NOT keyed on `tool_use_id` even when one is present:
        # Claude Code's PermissionRequest payload omits it (32 of 947 hook rows
        # carry one), so an id key would file the SDK's row apart from the
        # hook's and pair neither. `tool_name` is what both always record, and
        # `_may_pair` still refuses two rows whose ids disagree.
        tool = attrs.get('tool_name')
        # Attributes are arbitrary JSON: an unhashable value here would raise
        # inside the bucket dict and 500 the whole trace read.
        return ('cls', name, tool if isinstance(tool, str) else '')
    if name in _CROSS_SOURCE_MESSAGE_NAMES:
        # The API message id is the same `msg_…` string on both writers, so it
        # names the emission outright. `output_tokens`, `turn_index`, text and
        # thinking shape are all writer-skewed views of it and key nothing.
        mid = attrs.get('message_id')
        return ('cls', name, mid) if isinstance(mid, str) and mid else None
    if name in _CROSS_SOURCE_SINGLETON_NAMES:
        return ('cls', name)
    return None


def _cross_source_key(span: dict):
    """What makes a span the same EVENT across the two writers, or None.

    An identity wins where one exists; otherwise the span falls back to its
    class. A `permission.request` is class-keyed even when it carries an id,
    for the reason given in `_cross_source_class`.

    Resolved `prompt` rows carry NO key here: the ones that are the same event
    share `(trace_id, span_id)` outright (both writers derive it from the
    transcript entry uuid) and were already collapsed by
    `_collapse_shared_span_ids`. An in-flight placeholder pair — one per
    writer, holding the identical untruncated submission — is keyed on its
    text head instead, the same head `prompt_placeholder_id` hashes.
    """
    name = span.get('name') or ''
    attrs = _attrs(span)
    if name == 'prompt':
        if span.get('status_code') != 'PENDING':
            return None
        text = (attrs.get('text') or '').strip()
        return (('cls', 'prompt.pending', text[:_PENDING_PROMPT_KEY_CHARS])
                if text else None)
    return (_cross_source_identity(span, name, attrs)
            or _cross_source_class(name, attrs))


def _prefer_cross_source(a: dict, b: dict) -> tuple[dict, dict]:
    """(survivor, dropped) for one duplicated pair.

    A resolved row always beats a pending one, whichever writer produced it —
    that is what keeps the SDK's liveness: its span lands the moment the model
    emits it, while the hook twin can still be a placeholder waiting on the
    next hook to fire. Only when the two are at the same stage does the hook
    row win, for its richer attributes and turn linkage.
    """
    a_pending = is_pending_span_id(a.get('span_id') or '')
    b_pending = is_pending_span_id(b.get('span_id') or '')
    if a_pending != b_pending:
        return (b, a) if a_pending else (a, b)
    return (a, b) if a.get(_ORIGIN_KEY) == 'hook' else (b, a)


def _is_vague(value) -> bool:
    """True for a value that carries no information.

    Attributes are arbitrary JSON, so dicts and lists reach here and a bare
    `in` against a set raises `TypeError` on them. They are never vague.
    """
    return isinstance(value, (str, type(None))) and value in _VAGUE_ATTR_VALUES


def _fill_vague_attrs(survivor: dict, dropped: dict) -> dict:
    """Carry the dropped twin's concrete attributes onto the survivor."""
    kept, lost = _attrs(survivor), _attrs(dropped)
    fills = {k: v for k, v in lost.items()
             if not _is_vague(v) and (k not in kept or _is_vague(kept.get(k)))}
    if not fills:
        return survivor
    return {**survivor, 'attributes': {**kept, **fills}}


def _identity_agreement(a: dict, b: dict):
    """`True`/`False` when both rows name themselves, else `None` (unknown).

    Asymmetry is the normal state during an upgrade — one writer stamps the id
    for rows written from now on, the other's older rows have none — so a
    missing id must read as "no opinion", never as disagreement.
    """
    ia = _row_identity(a, a.get('name') or '', _attrs(a))
    ib = _row_identity(b, b.get('name') or '', _attrs(b))
    if ia is None or ib is None:
        return None
    return ia == ib


def _may_pair(a: dict, b: dict) -> bool:
    """Are these two rows the same event?

    Only identity speaks: two rows that both name themselves must agree —
    DIFFERING ids stay two rows however close in time. Rows inside one bucket
    already share their class key (message_id, tool_name, text head …), so
    with no row-level disagreement the n-th on one side IS the n-th on the
    other. Wall-clock distance is never evidence: a queued steer's two stamps
    sit minutes apart by construction, and the writers' stamps for one API
    message drift with generation length, so any finite window is a duplicate
    waiting on a long enough gap.
    """
    agreed = _identity_agreement(a, b)
    return True if agreed is None else agreed


def _pair_cross_source(hooks: list[dict], sdks: list[dict]) -> list[dict]:
    """Reconcile one key's rows from the two writers, positionally.

    Both lists are the SAME class of event (see `_cross_source_key`), each in
    its writer's own order, so the n-th occurrence on one side is the n-th on
    the other. A row-level identity disagreement refuses the pair and retires
    whichever row sorts earlier on its own, keeping the walk aligned.

    An unpaired row on either side survives untouched: it is an event only
    that writer saw, which is the whole reason both traces are kept.
    """
    out, i, j = [], 0, 0
    while i < len(hooks) and j < len(sdks):
        if not _may_pair(hooks[i], sdks[j]):
            older = hooks if _starts_before(hooks[i], sdks[j]) else sdks
            out.append(hooks[i] if older is hooks else sdks[j])
            i, j = (i + 1, j) if older is hooks else (i, j + 1)
            continue
        keep, drop = _prefer_cross_source(hooks[i], sdks[j])
        out.append(_fill_vague_attrs(keep, drop))
        i, j = i + 1, j + 1
    return out + hooks[i:] + sdks[j:]


def _starts_before(a: dict, b: dict) -> bool:
    return (a.get('start_time') or '') <= (b.get('start_time') or '')


def _dedup_cross_source(spans: list[dict]) -> list[dict]:
    """Collapse the same event recorded by both writers of one session.

    A regin-launched run is traced twice (`lib/trace/alias.py`), so the union
    the read path hands us holds two rows for most events. Runs LATE in
    `merge_spans` — after each writer's own placeholder/pending rules have
    settled — because those rules read span-id conventions that only hold
    within one writer: collapsing a cross-writer pair first would strip the
    `pending-` row that `_inherit_turn_linkage` reads turn linkage from, and
    hand `_absorb_slash_command_expansions` a survivor it can't pair.

    The cross-source BUCKETING is a no-op unless the window actually mixes
    writers, which is what keeps every ordinary session's output identical.
    The shared-span-id collapse runs unconditionally: it needs no origins to be
    correct, and a single-origin window has no same-id pairs to collapse.
    """
    spans = _collapse_shared_span_ids(spans)
    origins = {s.get(_ORIGIN_KEY) for s in spans}
    if 'sdk' not in origins or 'hook' not in origins:
        return spans
    spans = _mark_queued_sdk_placeholders(spans)
    merged, buckets = _bucket_by_cross_source_key(spans)
    for rows in buckets.values():
        merged.extend(_reconcile_bucket(rows))
    return sorted(merged, key=lambda s: (s.get('start_time') or '',
                                         s.get('id') or 0))


QUEUED_STATUS_MESSAGE = 'queued'


def _mark_queued_sdk_placeholders(spans: list[dict]) -> list[dict]:
    """Render an SDK-only `pending-` tool placeholder as queued, not running.

    The runner posts a placeholder the moment the model STREAMS a tool_use
    block, so a batch of three parallel calls lands as three PENDING rows at
    once. The harness executes them one at a time, and the hook's PreToolUse
    twin (same `pending-<tool_use_id>` id, hook origin) is what marks the
    moment a call actually starts — `_collapse_shared_span_ids` has just made
    the hook row the survivor wherever that twin exists. A runner row still
    standing alone is therefore a call the model asked for that the harness
    has not begun, and it must not read as a running one.

    Only reached for a dual-writer window (the caller checks origins), so a
    hook-less SDK session keeps its placeholders as its only liveness signal.
    Stamped on `status_message`, a top-level column, so the client's keyed
    merge replaces it the moment the hook twin arrives — an attribute would
    survive the shallow map's attribute strip and read queued forever.
    """
    out = []
    for s in spans:
        if (s.get(_ORIGIN_KEY) == 'sdk' and s.get('status_code') == 'PENDING'
                and (s.get('name') or '').startswith('tool.')
                and is_pending_span_id(s.get('span_id'))):
            s = {**s, 'status_message': QUEUED_STATUS_MESSAGE}
        out.append(s)
    return out


def _fill_capped_text(survivor: dict, dropped: dict) -> dict:
    """Replace a byte-capped survivor text with its twin's full text.

    Reached only for rows already proved to be one event, so the longer text
    is the same prompt uncapped — the wrapper stores the full submission while
    the transcript anchor is truncated at post time."""
    kept, lost = _attrs(survivor), _attrs(dropped)
    text, full = kept.get('text'), lost.get('text')
    if not (kept.get('text_truncated') and isinstance(full, str)
            and isinstance(text, str) and len(full) > len(text)):
        return survivor
    attrs = {**kept, 'text': full, 'chars': len(full)}
    attrs.pop('text_truncated', None)
    return {**survivor, 'attributes': attrs}


def _collapse_shared_span_ids(spans: list[dict]) -> list[dict]:
    """Collapse rows sharing `(trace_id, span_id)` across writers.

    Within one stored trace the unique index forbids the collision, so two
    rows under one id in an aliased window are by construction the two
    writers describing one event — both prompt anchors derive their id from
    the same transcript entry uuid. No text, shape, or clock comparison: the
    id IS the identity. The hook row survives for its richer attributes; a
    byte-capped survivor takes the twin's full text."""
    keep: dict[tuple, dict] = {}
    order: list[tuple] = []
    for s in spans:
        k = (s.get('trace_id'), s.get('span_id'))
        prev = keep.get(k)
        if prev is None:
            keep[k] = s
            order.append(k)
            continue
        survivor, dropped = _prefer_cross_source(prev, s)
        survivor = _fill_vague_attrs(survivor, dropped)
        keep[k] = _fill_capped_text(survivor, dropped)
    if len(order) == len(spans):
        return spans
    return [keep[k] for k in order]


def _bucket_by_cross_source_key(spans: list[dict]):
    """`(unkeyed, {key: rows})`. A span with no cross-source identity — most of
    the enrichment the hooks add — passes straight through."""
    unkeyed: list[dict] = []
    buckets: dict = {}
    for span in spans:
        key = _cross_source_key(span)
        if key is None:
            unkeyed.append(span)
        else:
            buckets.setdefault(key, []).append(span)
    return unkeyed, buckets


def _settled(rows: list[dict]) -> list[dict]:
    """One writer's rows for an event, minus its own placeholders once it has
    resolved the event.

    Positional pairing would otherwise match the OTHER writer's resolved row
    against this writer's leftover placeholder — which wins on
    `_prefer_cross_source` for being resolved — and leave this writer's real
    anchor unpaired and rendered a second time. Keeping the placeholders when
    NOTHING resolved is what preserves liveness: the SDK's resolved row still
    pairs with a hook side that is still only a placeholder.
    """
    resolved = [r for r in rows
                if not is_pending_span_id(r.get('span_id') or '')]
    return resolved or rows


def _reconcile_bucket(rows: list[dict]) -> list[dict]:
    """The surviving rows for one cross-source key."""
    hooks = _settled([r for r in rows if r.get(_ORIGIN_KEY) == 'hook'])
    sdks = _settled([r for r in rows if r.get(_ORIGIN_KEY) != 'hook'])
    if not hooks or not sdks:
        return rows
    # Positional pairing for BOTH kinds. A `tool_use_id` identifies the call,
    # not the row: one call legitimately leaves several rows per writer (real
    # traces carry up to 31 under one id), and collapsing only the first pair
    # left the rest standing as duplicates.
    return _pair_cross_source(hooks, sdks)


# Attribute keys the background-shell join stamps on a launching `tool.Bash`
# span. Named apart from the Bash call's own `stdout`/`duration_ms` because
# they describe the SHELL, which outlives the call that started it.
_BG_STATUS = 'background_status'
_BG_RUNNING = 'running'

#: Row id of the OTHER span a serve-time-derived body was built from.
#:
#: `_body_needed` (web/blueprints/trace/sessions.py) withholds a span the
#: client already holds once it is below the watermark and settled — resting
#: on "a body only changes when its own row changes". That holds for every
#: attribute a hook wrote and fails for one derived here: the source row is an
#: INSERT with a fresh id, while the span it enriches keeps the id it had. The
#: reader ships the body when THIS id is above the watermark, which is the same
#: identifier test applied to the row the body actually came from — so it
#: settles as soon as the client has read past it, rather than re-sending
#: forever.
DERIVED_FROM_ID_KEY = 'derived_from_id'


def _background_completions(spans: list[dict]) -> dict:
    """`{task_id: notification span}` for every `task.notification` in the
    window. Agent reports land here too; they simply match no shell."""
    done: dict = {}
    for s in spans:
        if s.get('name') != 'task.notification':
            continue
        task_id = _attrs(s).get('task_id')
        if isinstance(task_id, str) and task_id:
            done[task_id] = s
    return done


def _elapsed_ms(started: str | None, ended: str | None) -> int | None:
    """Milliseconds between two RECORDED events. Not a window, threshold or
    skew allowance — both endpoints are stamps the harness already wrote."""
    a, b = _merge_ts(started), _merge_ts(ended)
    if a is None or b is None or b < a:
        return None
    return int((b - a).total_seconds() * 1000)


def _link_background_tasks(spans: list[dict]) -> list[dict]:
    """Join each backgrounded `tool.Bash` call to its completion notification.

    A `run_in_background` call returns in milliseconds and its shell keeps
    running; the completion arrives later as an independent
    `task.notification` row. Without this the trace shows an instant
    no-output success and an unrelated notification elsewhere, and a shell
    still running shows nothing at all.

    Keyed on `background_task_id == task_id` — an identifier equality, never a
    timestamp or ordering test, so parallel shells cannot be crossed. The
    launching span's own `duration_ms` is left alone: 3 ms is the true cost of
    the tool call and the cost aggregates read it.
    """
    done = _background_completions(spans)
    out = []
    for s in spans:
        attrs = _attrs(s)
        task_id = attrs.get('background_task_id')
        if s.get('name') != 'tool.Bash' or not isinstance(task_id, str) or not task_id:
            out.append(s)
            continue
        out.append({**s, 'attributes': _background_attrs(attrs, done.get(task_id), s)})
    return out


def _background_attrs(attrs: dict, notification: dict | None, bash: dict) -> dict:
    if notification is None:
        return {**attrs, _BG_STATUS: _BG_RUNNING}
    note = _attrs(notification)
    ended = notification.get('start_time')
    merged = {**attrs,
              _BG_STATUS: note.get('status') or _BG_RUNNING,
              'background_ended_at': ended}
    source_id = notification.get('id')
    if source_id is not None:
        merged[DERIVED_FROM_ID_KEY] = source_id
    for src, dst in (('summary', 'background_summary'),
                     ('output_file', 'background_output_file')):
        value = note.get(src)
        if value:
            merged[dst] = value
    elapsed = _elapsed_ms(bash.get('start_time'), ended)
    if elapsed is not None:
        merged['background_elapsed_ms'] = elapsed
    return merged


def merge_spans(
    raw: list[dict], prompt_id_ceiling=None, session_activity=None,
) -> list[dict]:
    """Reconcile one window of append-only rows into the canonical span list.

    Pure: returns a new list, mutates neither `raw` nor the DB. Dedup runs
    first (so superseded placeholders can't open phantom turns), then the
    double-rendered-rejection drop (`_drop_denied_tool_failures`), then a
    slash-command rescue (`_absorb_slash_command_expansions` moves an
    expansion onto its resolved echo before the stale sweep could drop it),
    the stale-blocker drop, then the stuck-pending demotion. Drop-in
    replacement for `_graft_orphans` at the read path — identical over
    already-reconciled windows.

    `prompt_id_ceiling` is the per-trace GLOBAL max prompt id; a windowed
    reader passes it so stray prompt placeholders drop even in an older
    scroll-up window (see `_drop_stale_blockers`). Whole-session readers omit
    it. `session_activity` ({'status', 'last_seen'}, optional) enables the
    session-inactive demotion path (`_demote_stale_pending`); without it only
    the same-agent-moved-on path fires."""
    if not raw:
        return raw
    spans = _drop_superseded_placeholders(raw)
    spans = _drop_resolved_permission_requests(spans)
    # After the resolved-gate drop, so only a gate that is still open — the
    # one case that renders twice — can reach the collapse.
    spans = _collapse_open_gates(spans)
    spans = _drop_denied_tool_failures(spans)
    spans = _absorb_slash_command_expansions(spans)
    # After each writer's own placeholder rules have settled: they key on
    # span-id conventions that only hold within one writer, so collapsing a
    # cross-writer pair earlier strips the `pending-` row `_inherit_turn_linkage`
    # reads from and leaves a slash expansion stranded on the SDK row.
    spans = _dedup_cross_source(spans)
    spans = _drop_stale_blockers(spans, prompt_id_ceiling=prompt_id_ceiling)
    spans = _demote_stale_pending(spans, session_activity=session_activity)
    # Last: it reads the survivors, and stamps attributes no earlier rule keys on.
    spans = _link_background_tasks(spans)
    return _graft_orphans(spans)
