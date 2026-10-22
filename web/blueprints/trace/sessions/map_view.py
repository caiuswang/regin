"""`GET /api/sessions/<trace_id>/map` and the shallow structural map it serves."""

from __future__ import annotations

import hashlib
import os

from flask import request, jsonify
from lib.agent_sdk import resumable as resumable_mod
from lib.orm import SessionLocal
from lib.orm.models import Session as SessionModel
from lib.trace import trace_service
from lib.trace.alias import ORIGIN_KEY, strip_origin
from lib.trace.pending_spans import AGENT_ID_SQL
from web.blueprints.trace import trace_bp
from web.blueprints.trace.sessions.common import (
    _alias_group_ids, _stored_group_ids, _total_span_count,
)
from web.blueprints.trace.sessions.phase import _session_summary
from web.blueprints.trace.sessions.roster import (
    _AGENT_MARKER_NAMES, _in_placeholders, _roster_with_activity,
)
from web.blueprints.trace.sessions.steers import _queued_prompts
from web.blueprints.trace.sessions.subtrees import _shallow_nodes
from web.blueprints.trace.sessions.tasks import _fetch_session_task_list


# Attribute keys the light structural map preserves after the strip:
#  * the `/rewind` signal the frontend needs to collapse the discarded branch
#    and label the marker without a content round-trip;
#  * the compact `memory.recall` labels (`hit_count`, plus `source`/`skill_id`
#    that distinguish an injected `<skill_experience>` block from generic
#    recalled experience) so MemoryRecallRow renders its header durably on a
#    fresh load — not just transiently off the live-tail append.
# The heavier attrs (rewind orphan_keys/rolled_back_files, the recall `block`
# and per-hit `hits` list) are dropped here and loaded lazily via
# /spans/<id>/rewind and /spans/<id>/content.
_MAP_KEEP_ATTR_KEYS = (
    'rewound_away', 'rewind_fork_id',
    'abandoned_prompt_count', 'rolled_back_count',
    'hit_count', 'source', 'skill_id',
    # The per-agent scope key: the frontend's attribute partition (orphan
    # roster entries with no start marker to tree-walk from) matches on it.
    # `label` rides along so a workflow subagent's marker stays nameable
    # after the strip.
    'agent_id', 'label',
    # The whole of what a subagent did, which its marker is now the only
    # row here to say — the spans these summarize are served by the child
    # trace `agent_child_trace_id` addresses.
    'agent_span_count', 'agent_tool_count', 'agent_input_tokens',
    'agent_output_tokens', 'agent_cost_usd', 'agent_duration_ms',
    'agent_child_trace_id',
    # Which run of a RESUMED agent this marker is, and what that run alone
    # did. Without them every segment card reads the one roster entry keyed on
    # `agent_id` and they render identically.
    'agent_segment_index', 'agent_segment_count',
    'agent_segment_span_count', 'agent_segment_tool_count',
    'agent_segment_duration_ms',
    # What THIS run was asked and what it answered — the launch prompt and the
    # result only ever describe the first one.
    'agent_segment_prompt', 'agent_segment_result',
    # The whole-agent answer, for the agents that never resumed — written to
    # the stop marker, carried here so the card can show it.
    'agent_result_preview',
    # `harness.api_retry` renders entirely from these: an attempt count with
    # no denominator, or a row with no wait, is the bare spinner this span was
    # added to replace.
    'attempt', 'max_attempts', 'retry_in',
    # ScheduleWakeup label needs these to distinguish "agent finished" (stop)
    # from "paused to resume" — else the row degrades to a bare tool name on a
    # fresh (non-shallow /map) reload. `resume_action`/`poll_*` are the
    # serve-time cross-turn link (wakeup_links.annotate_wakeup_resumes).
    'stop', 'delay_seconds', 'reason',
    'resume_action', 'resume_span_id', 'poll_round', 'poll_total',
    # An SDK run that continued an earlier session names it on session.start;
    # dropped here, the trace would open with no sign it inherited a history.
    'resumed_from',
    # Marks a child trace's `subagent.start` re-served as its opening
    # `prompt`: the /live scope must tell it from a captured prompt, or an
    # agent that captured nothing reads as a turn with no text.
    'agent_root',
)


def _kept_map_attrs(attrs) -> dict:
    """The small subset of a span's attributes the structural map keeps."""
    if not isinstance(attrs, dict):
        return {}
    return {k: attrs[k] for k in _MAP_KEEP_ATTR_KEYS if k in attrs}


# What labels a tool call that has not reported back: the flat preview keys
# `fullLabel`/`RunningSpanCard` read. Deliberately NOT the bag it rides in — a
# pending row also carries `command` (the full, untruncated command),
# `tool_input`, and for `tool.Agent` the whole sub-agent prompt: measured over
# the 4000 newest PENDING rows in the local store, one `tool.Bash` bag reached
# 18 KB. The chip is one truncated line, so the previews say everything it can
# show.
_LIVE_TOOL_ATTR_KEYS = (
    'tool_name', 'live',
    'command_preview', 'description', 'subagent_type',
    'file_path', 'pattern', 'query', 'url',
    'skill_name', 'task_id', 'subject', 'status',
    'reject_reason',
)

# Not every key above is capped at capture — `description` is written through
# raw (`hook_manager/handlers/post_tool_trace.py`). One chip line is the whole
# consumer, so the ceiling is imposed here rather than trusting each writer.
_LIVE_TOOL_ATTR_MAX_CHARS = 400


def _is_live_tool_row(span) -> bool:
    """A tool call still in flight — the one row kind the lazy content read
    cannot answer for in time.

    Every other row is stripped here and filled by /spans/<id>/content once
    the reader asks. An in-flight row has no result to ask for, and the two
    reads are taken at different instants: a row the merge retires between
    them keeps whatever the map gave it, so a stripped one is stuck rendering
    its bare tool name.
    """
    return (span.get('status_code') == 'PENDING'
            and str(span.get('name') or '').startswith('tool.'))


def _kept_live_tool_attrs(attrs) -> dict:
    """`_kept_map_attrs` widened by the in-flight label keys, each capped."""
    if not isinstance(attrs, dict):
        return {}
    live = {}
    for key in _LIVE_TOOL_ATTR_KEYS:
        if key not in attrs:
            continue
        value = attrs[key]
        live[key] = (value[:_LIVE_TOOL_ATTR_MAX_CHARS]
                     if isinstance(value, str) else value)
    return {**_kept_map_attrs(attrs), **live}


# The columns the structural map merges on and ships. A subagent read comes
# through the projection fetch, which selects a wider row (usage counters, the
# agent tag) than this endpoint has ever carried, so it is narrowed to this.
_MAP_SPAN_KEYS = (
    'id', 'trace_id', 'span_id', 'parent_id', 'name', 'kind',
    'start_time', 'end_time', 'duration_ms', 'status_code', 'status_message',
    'attributes', 'turn_uuid', 'tool_use_id', 'source',
)


def _map_source_spans(group: list[str]) -> list[dict]:
    """The rows the structural map merges, however they are addressed.

    Through the projection fetch, which answers for a subagent id (not one of
    its rows is STORED under `sa-…`, so a stored-trace_id query serves an empty
    map) and stamps each subagent marker's rollup — all this view keeps of the
    agent, and so the only row left in it that can say what the agent did.
    Narrowed afterwards to the columns this endpoint has always carried.
    """
    from lib.trace.projection import _fetch_spans_for_group
    from lib.orm.engine import get_connection
    conn = get_connection()
    try:
        fetched = _fetch_spans_for_group(conn, group)
    finally:
        conn.close()
    return [{**{k: s[k] for k in _MAP_SPAN_KEYS if k in s},
             ORIGIN_KEY: s.get(ORIGIN_KEY, 'hook')} for s in fetched]


def _map_session_activity(stored: list[str]) -> dict | None:
    """`{'status', 'last_seen'}` for the session behind the map, or None."""
    from sqlmodel import select as _select
    with SessionLocal() as session:
        act_row = session.exec(
            _select(SessionModel.status, SessionModel.last_seen)
            .where(SessionModel.trace_id.in_(stored))
            .order_by(SessionModel.last_seen.desc())
        ).first()
    if not act_row:
        return None
    from lib.orm.engine import get_connection
    from lib.trace.pending_spans import never_ran_pending_agents
    conn = get_connection()
    try:
        never_ran = never_ran_pending_agents(conn, stored)
    finally:
        conn.close()
    from lib.agent_acp.registry import open_tool_use_ids
    return {'status': act_row[0], 'last_seen': act_row[1],
            'never_ran': never_ran, 'open_tool_ids': open_tool_use_ids(stored)}


def _structural_map_spans(trace_id: str) -> list[dict]:
    """The full structural span list for the non-shallow `/map` (Terminal tab).

    The store is append-only, so a placeholder and its real anchor coexist —
    `merge_spans` selects the winner. It correlates by prompt-text hash /
    tool_name, so it needs `attributes` (and `turn_uuid` for the reparent
    ladder); read them from session_spans rather than the attribute-less
    session_trace_map. After merging we STRIP attributes/turn_uuid so the map
    stays the light, structure-only payload the frontend expects (descendants
    fetch content lazily via /spans/<id>/content)."""
    from lib.trace.merge import merge_spans
    from lib.trace.trace_service.queries import _attach_prompt_expansions

    group = _alias_group_ids(trace_id)
    # The session-level joins follow the STORED ids: a subagent trace has no
    # `sessions` row and no workflow spans of its own, and reading them under
    # `sa-…` would call a live run inactive and leave its markers unlabelled.
    stored = _stored_group_ids(trace_id)
    spans = _map_source_spans(group)
    session_activity = _map_session_activity(stored)
    # `tool_use_id` is fetched only so the cross-source reconcile can join on
    # it; this endpoint's span dict never carried it, and a guard test pins
    # that shape. Dropped with the origin marker rather than widened here.
    grafted = [{k: v for k, v in s.items() if k != 'tool_use_id'}
               for s in strip_origin(
                   merge_spans(spans, session_activity=session_activity))]
    _attach_prompt_expansions(stored[0], grafted)
    from lib.trace.workflow_labels import attach_workflow_agent_attrs
    # Canonical, like the spans it annotates — the URL id would make the two
    # entry points diverge for a session carrying bare workflow markers.
    attach_workflow_agent_attrs(stored[0], grafted)
    from lib.trace.wakeup_links import annotate_wakeup_resumes
    annotate_wakeup_resumes(grafted)
    from lib.trace.projection import drop_delegated_spans
    grafted = drop_delegated_spans(grafted)
    for s in grafted:
        attrs = s.get('attributes')
        kept = (_kept_live_tool_attrs(attrs) if _is_live_tool_row(s)
                else _kept_map_attrs(attrs))
        s.pop('attributes', None)
        s.pop('turn_uuid', None)
        if kept:
            s['attributes'] = kept
    return grafted


def _bridge_reachability(trace_id: str) -> dict:
    """Per-session steerability for the /live composer and answer sheet.

    Rides the existing shallow-map poll so the client needs no extra
    polling loop. Exposes only a boolean + the pane label — never the
    bridge bearer token (that credential must not reach the browser).

    Reachable means "regin can steer this session", not "this session has a
    tmux pane": a session regin launched itself is answered over its typed
    channel and has no pane at all, so gating the sheet on a pane would leave
    the tier's own questions unanswerable.

    A `/clear`ed session is reached through the conversation that replaced it.
    Its own pane read cannot answer — `_SELF_NOT_ENDED` refuses a row whose
    session has ended, and a cleared session has ended by definition — so
    without this fallback the page would carry a `successor_trace_id` it had
    no composer to use. The pane label is the same physical pane either way.

    `resumable` is the other half of the same question, and rides here for the
    same reason: what the page may offer when nothing can steer the session. A
    reachable session is never resumable — continuing a conversation that is
    still running would put two processes on one session id.
    """
    state = _steerability(trace_id)
    # Only the conversation the run is on NOW. Ownership resolves through the
    # run, so every child of a live run reports `sdk_owned` — including ones
    # it genuinely finished with, whose `ended_at` is a true verdict the heal
    # would erase.
    if state['sdk_owned'] and _sdk_driving(trace_id):
        _heal_ended_while_owned(trace_id)
    refusal = '' if state['bridge_reachable'] else _resume_refusal(trace_id)
    state['resumable'] = not state['bridge_reachable'] and not refusal
    # The one refusal a reader needs: neither steerable nor resumable is an
    # empty rail either way, and only this says the conversation is alive.
    state['cli_held'] = refusal == resumable_mod.REFUSED_CLI_HELD
    state['driven_by'] = (_provider_name(trace_id)
                          if refusal == resumable_mod.REFUSED_NOT_LAUNCHABLE
                          else None)
    return state


def _sdk_driving(trace_id: str) -> bool:
    """Whether a run regin holds is on THIS conversation — the registry alone,
    since the tmux tier holds no runs and only the SDK tier can answer it.

    Fails closed: the caller uses this to decide whether to clear an end
    marker, and the cost of a wrong yes is a finished session shown as live.
    """
    from lib import agent_sdk
    try:
        return agent_sdk.is_driving(trace_id)
    except Exception:  # noqa: BLE001 - a map poll must never 500 on this
        from lib.activity_log import get_activity_logger
        get_activity_logger("agent_sdk").error(
            "sdk_driving_probe_failed", trace_id=trace_id, exc_info=True)
        return False


def _heal_ended_while_owned(trace_id: str) -> None:
    """Clear an end marker on a session a run is driving right now.

    Ownership is the stronger evidence: it means the runner object exists in
    THIS process, while `ended_at` is a claim some earlier writer left on the
    row. When they disagree the row is wrong, and the disagreement renders as
    nothing at all — the composer is gated on `!ended_at`, Resume on
    `!bridge_reachable`, and the cli-held notice on neither — so an operator
    meets a live conversation with no way to type into it and no way to
    reopen it.

    Healed rather than patched into this one response: every other reader
    (the session list, the header pill, `/live`'s poll, which stops for good
    on an ended session) answers from the same row and would go on disagreeing
    with the composer beside it. Idempotent — `revive_trace_session` touches
    only a row that claims to be over — so the poll that finds nothing wrong
    writes nothing.

    The whole alias group, for the reason the launch revives both halves: a
    run and the child it drives each own a `sessions` row, and reviving only
    the id this request named leaves the other still ended for whichever
    entry point resolves to it.
    """
    from lib.agent_sdk import store as agent_sdk_store
    try:
        agent_sdk_store.revive_trace_session(*_alias_group_ids(trace_id))
    except Exception:
        from lib.activity_log import get_activity_logger
        get_activity_logger("agent_sdk").error(
            "sdk_ended_while_owned_heal_failed", trace_id=trace_id,
            exc_info=True)


def _provider_name(trace_id: str) -> str:
    from lib.providers.base import provider_for_trace
    return provider_for_trace(trace_id).display_name


def _resume_refusal(trace_id: str) -> str:
    """`resumable.resume_refusal`, degraded to a generic refusal on failure.

    The check reaches the filesystem (the provider's transcript glob), the tmux
    pane registry and the CLI, none of which the map poll may be broken by: a
    session that renders without a Resume button is a missing affordance, one
    that fails to render at all is a dead page.
    """
    try:
        return resumable_mod.resume_refusal(trace_id) or ''
    except Exception:
        from lib.activity_log import get_activity_logger
        get_activity_logger("agent_sdk").error(
            "sdk_resumable_probe_failed", trace_id=trace_id, exc_info=True)
        return 'probe_failed'


def _steerability(trace_id: str) -> dict:
    """Which tier, if any, can type into `trace_id` right now."""
    from lib import agent_sdk
    from lib.settings import settings
    if agent_sdk.is_sdk_owned(trace_id):
        return {'bridge_reachable': True, 'bridge_pane': None,
                'sdk_owned': True}
    if not settings.agent_bridge.enabled:
        return {'bridge_reachable': False, 'bridge_pane': None,
                'sdk_owned': False}
    from lib.agent_bridge import store as bridge_store
    pane = bridge_store.get_reachable_pane(trace_id)
    if pane is None:
        successor = bridge_store.clear_successor(trace_id)
        if successor:
            pane = bridge_store.get_reachable_pane(successor)
    return {'bridge_reachable': pane is not None,
            'bridge_pane': pane['pane_id'] if pane else None,
            'sdk_owned': False}


def _shallow_map_response(trace_id: str):
    """Paginated structural map for the live trace view (`?shallow=1`): root
    spans + lazy tree nodes + cursors + the merge's `retired_span_ids` (rows the
    serve-time merge dropped from this window, which the client must prune from
    its append-only `session.spans` or the cards show a duplicate)."""
    from sqlmodel import select as _select
    from sqlalchemy import func as _func
    try:
        limit = int(request.args.get('limit', 50))
    except (TypeError, ValueError):
        return jsonify({'error': 'limit must be an integer'}), 400
    limit = max(1, min(limit, 200))
    before_id = request.args.get('before_id', type=int)
    after_id = request.args.get('after_id', type=int)
    if before_id is not None and after_id is not None:
        return jsonify({'error': 'before_id and after_id are mutually exclusive'}), 400

    # Live tail (initial / additive reload, not scroll-up): kick a background
    # transcript rescan so assistant turns flushed since the last hook scan get
    # ingested and show within a poll or two (esp. subagent reasoning, which
    # otherwise batches at SubagentStop). Fire-and-forget; this response still
    # serves the current DB state, the next poll picks up the new turns.
    if before_id is None:
        from lib.trace.live_rescan import trigger_rescan
        trigger_rescan(trace_id)

    from lib.trace.trace_service import projection_cache

    structural = _shallow_structural_half(trace_id, limit, before_id, after_id)
    # The client names the drop-list version it has already applied. Unchanged
    # means it has pruned exactly these rows and re-sending them would only
    # make it re-derive a no-op — so send the tag alone. A version it has NOT
    # seen always arrives in full, which is what keeps a late retirement (a
    # placeholder promoted long after it was loaded) reaching the cards.
    if structural.get('retired_digest') \
            and request.args.get('retired_seen') == structural['retired_digest']:
        structural = {k: v for k, v in structural.items()
                      if k != 'retired_span_ids'}
    roster, activity, ended = _roster_with_activity(trace_id)
    return jsonify({
        'trace_id': trace_id,
        # The authoritative count of span changes, against which the client
        # checks the push frames it received. It is what makes the frame
        # stream's losses detectable: a writer that cannot name the spans it
        # touched (a reconciler, a materialize reparent) still advances this,
        # so a reader comparing it against its own tally re-asks for bodies
        # instead of trusting a cache nothing told it was stale.
        'span_epoch': projection_cache.epoch(_stored_group_ids(trace_id)),
        **structural,
        # Prompts typed while the agent is busy fire no hook; surface what's
        # currently queued (derived live from the transcript, ephemeral).
        'queued_prompts': _queued_prompts(trace_id),
        'task_list': _fetch_session_task_list(trace_id),
        'agent_roster': roster,
        # Whole-session, deliberately not derived from `structural`: a shell
        # running for half an hour has scrolled out of the loaded page, and
        # that is precisely the shell the reader still needs to see.
        'background_shells': _background_shells(trace_id, ended),
        **_bridge_reachability(trace_id),
        **_session_summary(trace_id, roster=roster, activity=activity),
    })


def _background_shells(trace_id: str, ended: bool = False) -> list:
    """The session's background shells, newest state first-class.

    `ended` settles the shells nothing ever reported on: a shell is a child of
    the process that launched it, so a session that is over cannot still have
    one running.
    """
    from lib.orm.engine import get_connection
    from lib.trace.background_shells import shell_roster
    conn = get_connection()
    try:
        return shell_roster(conn, _alias_group_ids(trace_id), bool(ended))
    finally:
        conn.close()


# One screenful of a growing output file. A background shell can emit
# megabytes (a dev server, a `tail -f`); the block exists to show what it is
# doing NOW, so the tail is the whole useful part and the cap is what keeps a
# runaway log off the wire.
_SHELL_OUTPUT_TAIL_BYTES = 64 * 1024


@trace_bp.route('/api/sessions/<trace_id>/background/<task_id>/output')
def api_background_shell_output(trace_id, task_id):
    """The tail of one background shell's output file.

    The path is resolved server-side from what this session's own rows state
    — never taken from the caller, and never assembled from a directory
    convention — so this endpoint cannot be pointed at an arbitrary file.
    `available: false` is a first-class answer: a session that has not yet
    stated its task directory has no readable path, which is a gap to report
    rather than to guess around.
    """
    from lib.orm.engine import get_connection
    from lib.trace.background_shells import output_path
    conn = get_connection()
    try:
        path = output_path(conn, _alias_group_ids(trace_id), task_id)
    finally:
        conn.close()
    if not path:
        return jsonify({'task_id': task_id, 'available': False, 'text': '',
                        'reason': 'no output file recorded for this task'})
    try:
        size = os.path.getsize(path)
        with open(path, 'rb') as fh:
            if size > _SHELL_OUTPUT_TAIL_BYTES:
                fh.seek(size - _SHELL_OUTPUT_TAIL_BYTES)
            body = fh.read()
    except OSError as exc:
        return jsonify({'task_id': task_id, 'available': False, 'text': '',
                        'reason': str(exc)})
    return jsonify({
        'task_id': task_id,
        'available': True,
        'text': body.decode('utf-8', 'replace'),
        'size': size,
        'truncated': size > _SHELL_OUTPUT_TAIL_BYTES,
    })


def _delegated_count_field(trace_id) -> dict:
    """`span_count_delegated`: how many of `span_count_total` a child trace
    serves instead of this view, so the two numbers name their difference
    rather than leaving it as an unexplained gap.

    Counted over the same population `_total_span_count` counts, and narrowed
    to the agents a marker names — the two conditions that make the three
    numbers reconcile. PENDING is what `agent_span_count` gets wrong on its
    own (a pre-merge figure: 4,165 against 2,708 on one measured session), and
    an agent with no marker is still rendered inline, so counting it here
    would report a row as both shown and delegated.

    Omitted when nothing was delegated, and for a subagent trace, which sends
    nothing anywhere: a session that never launched an agent answers exactly
    as it did before.
    """
    from lib.orm.engine import get_connection
    from lib.trace.alias import sql_in
    from lib.trace.pending_spans import AGENT_ID_SQL
    from lib.trace.subagent_trace import is_subagent_trace_id
    if is_subagent_trace_id(trace_id):
        return {}
    placeholders, params = sql_in(_stored_group_ids(trace_id))
    marker_ph = _in_placeholders(_AGENT_MARKER_NAMES)
    scope = f"trace_id IN {placeholders} AND name IN ({marker_ph})"
    conn = get_connection()
    try:
        row = conn.execute(
            f"SELECT COUNT(*) AS n FROM session_spans "
            f"WHERE trace_id IN {placeholders} AND status_code != 'PENDING' "
            f"AND name NOT IN ({marker_ph}) "
            f"AND {AGENT_ID_SQL} IN ("
            f"  SELECT {AGENT_ID_SQL} FROM session_spans WHERE {scope} "
            "   UNION SELECT json_extract(attributes, '$.launch_tool_use_id') "
            f"  FROM session_spans WHERE {scope})",
            [*params, *_AGENT_MARKER_NAMES, *params, *_AGENT_MARKER_NAMES,
             *params, *_AGENT_MARKER_NAMES]).fetchone()
    finally:
        conn.close()
    return {'span_count_delegated': row['n']} if row and row['n'] else {}


def _retired_digest(retired_span_ids) -> str:
    """Version tag for the merge's drop list.

    The list is what the client prunes from its append-only span store, and it
    is deterministic and monotonic — the same rows drop on every recompute over
    the same window. So once a client has applied a version it never needs that
    version again, and on a big session that matters: the drop list reached
    1544 ids, 37KB, HALF the whole response, re-sent every few seconds to a
    client that had already pruned every one of them.
    """
    if not retired_span_ids:
        return ''
    digest = hashlib.sha1('\n'.join(sorted(retired_span_ids)).encode())
    return digest.hexdigest()[:16]


def _shallow_structural_half(trace_id, limit, before_id, after_id) -> dict:
    """The span-derived fields of the shallow map — the expensive half.

    Split out because it is the ONLY part of the response that is a pure
    function of the stored rows, which is what lets it be memoised on an
    identifier fingerprint (`projection_cache`). Everything left in the
    caller reads a clock, a tmux pane or the transcript, and so is recomputed
    on every request even when this half is served from the memo.
    """
    from lib.orm.engine import get_connection
    from lib.trace.trace_service import projection_cache

    group = _alias_group_ids(trace_id)
    # Fingerprinted and filed under the ids the rows are WRITTEN to, which for
    # a subagent trace are its parent's: `invalidate()` names the id the writer
    # touched, so an entry filed under `sa-…` is never dropped and a live child
    # would serve its first answer forever. The served id stays in the key, so
    # parent and child keep separate entries.
    stored = _stored_group_ids(trace_id)
    conn = get_connection()
    try:
        key = ('shallow', tuple(group),
               projection_cache.fingerprint(conn, stored),
               limit, before_id, after_id)
    finally:
        conn.close()
    hit = projection_cache.get(key)
    if hit is not None:
        return hit

    widened, tree, has_more_older, retired_span_ids = trace_service.fetch_session_paginated(
        trace_id, limit=limit, before_id=before_id, after_id=after_id,
        drop_delegated=True,
    )
    by_id = {s['span_id']: s for s in widened}
    root_ids = {n['data']['span_id'] for n in tree}
    # Cursors use DB `id`: stable, monotonic, unambiguous on ties.
    ids = [n['data']['id'] for n in tree if 'id' in n.get('data', {})]
    with SessionLocal() as session:
        # span_count_total excludes PENDING placeholders (append-only keeps them).
        span_count_total = _total_span_count(session, trace_id, len(widened))
    half = {
        'spans': [s for i, s in by_id.items() if i in root_ids],
        'tree': _shallow_nodes(tree),
        'span_count': len(widened),
        'span_count_total': span_count_total,
        # `span_count_total` stays the session's true total, so it exceeds
        # what is on screen by exactly this — the rows each subagent's own
        # trace serves. Absent when nothing was delegated.
        **_delegated_count_field(trace_id),
        'has_more_older': has_more_older,
        'oldest_loaded_id': min(ids) if ids else None,
        'newest_loaded_id': max(ids) if ids else None,
        'retired_span_ids': retired_span_ids,
        'retired_digest': _retired_digest(retired_span_ids),
    }
    projection_cache.put(key, half, group)
    return half


@trace_bp.route('/api/sessions/<trace_id>/map')
def api_session_map(trace_id):
    """Return the structural map for a session.

    Every span is included, sorted chronologically. No content
    (attributes) — fetch those via /spans/<span_id>/content.

    Parent IDs are grafted server-side (orphan tool spans are re-attached
    to their owning prompt) so the frontend gets the correct tree shape
    without re-implementing the projection logic.

    Session summary (model, token counters, etc.) is included so the
    frontend can render the header without a second round-trip.

    Pass `?shallow=1` to return only root spans plus shallow tree nodes
    for lazy child expansion. Default stays backward-compatible.
    """
    if request.args.get('shallow', '').lower() in ('1', 'true'):
        return _shallow_map_response(trace_id)

    spans = _structural_map_spans(trace_id)
    summary = _session_summary(trace_id)
    return jsonify({
        'trace_id': trace_id,
        'spans': spans,
        'span_count': len(spans),
        **_delegated_count_field(trace_id),
        **summary,
    })
