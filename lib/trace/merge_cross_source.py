"""Cross-source de-duplication: pair the same logical row reported by hook,
transcript and SDK sources, and collapse shared span ids."""

from __future__ import annotations

from lib.trace.pending_spans import is_pending_span_id
from lib.trace.alias import ORIGIN_KEY as _ORIGIN_KEY
from lib.trace.merge_anchors import _attrs
from lib.trace.merge_gates import _tool_use_id


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
    for key, rows in buckets.items():
        merged.extend(_reconcile_bucket(key, rows))
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


def _collapse_one_message(hooks: list[dict], sdks: list[dict]) -> list[dict]:
    """One API message's rows from both writers, as ONE writer's rows.

    The writers cut a message at different grain: the SDK stream stores a row
    per content BLOCK, the transcript writer one row per MESSAGE
    (`thinking_blocks: 2`). Positional pairing matched the SDK's first block —
    often an empty one — to the whole-message row and left the second block
    rendered as a duplicate card. The bucket key is the message id itself, so
    every row here is the same emission: keep the preferred writer's rows and
    let each dropped row fill what the survivor left vague.
    """
    keep, _ = _prefer_cross_source(hooks[0], sdks[0])
    kept, dropped = (hooks, sdks) if keep is hooks[0] else (sdks, hooks)
    head = kept[0]
    for row in dropped:
        head = _fill_vague_attrs(head, row)
    return [head, *kept[1:]]


def _reconcile_bucket(key: tuple, rows: list[dict]) -> list[dict]:
    """The surviving rows for one cross-source key."""
    hooks = _settled([r for r in rows if r.get(_ORIGIN_KEY) == 'hook'])
    sdks = _settled([r for r in rows if r.get(_ORIGIN_KEY) != 'hook'])
    if not hooks or not sdks:
        return rows
    if key[0] == 'cls' and key[1] in _CROSS_SOURCE_MESSAGE_NAMES:
        return _collapse_one_message(hooks, sdks)
    # Positional pairing for BOTH kinds. A `tool_use_id` identifies the call,
    # not the row: one call legitimately leaves several rows per writer (real
    # traces carry up to 31 under one id), and collapsing only the first pair
    # left the rest standing as duplicates.
    return _pair_cross_source(hooks, sdks)
