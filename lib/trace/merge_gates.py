"""Serve-time retirement of superseded placeholders, resolved permission gates
and denied-call duplicates."""

from __future__ import annotations

from lib.trace.pending_spans import is_pending_span_id, pending_id_for_resolved
from lib.trace.alias import ORIGIN_KEY as _ORIGIN_KEY
from lib.trace.merge_anchors import _attrs, _is_expansion_anchor


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
