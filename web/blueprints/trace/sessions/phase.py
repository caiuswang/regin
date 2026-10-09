"""Per-agent phase verdicts and the session summary the live views render."""

from __future__ import annotations

import os

from datetime import datetime, timezone
from lib.orm import SessionLocal
from lib.orm.models import Session as SessionModel, SessionRepo
from lib.trace.pending_spans import (
    INACTIVE_THRESHOLD_SEC as _AGENT_STALE_AFTER_SEC,
    parse_naive_ts as _roster_ts,
)
from web.blueprints.trace.sessions.common import (
    _alias_group_ids, _group_ended_reason,
)
from web.blueprints.trace.sessions.roster import _roster_with_activity
from web.blueprints.trace.sessions.turn_state import turn_state


# ── Per-agent phase (server verdict) ─────────────────────────────────
# The /live card renders phase; it never re-derives state. `phase` is the
# session rollup; `agent_phase` maps "main" (agent_id IS NULL) + each
# subagent id → its phase. Constants live here (item 4): the client keeps
# only its poll cadence + cosmetic ticks. WORKING_WINDOW mirrors the client's
# old IDLE_ACTIVITY floor; INACTIVE_THRESHOLD reuses the roster stale window.
WORKING_WINDOW_SEC = 12
IDLE_SETTLE_SEC = 6
INACTIVE_THRESHOLD_SEC = _AGENT_STALE_AFTER_SEC

# Rollup precedence: a blocked subagent surfaces while main is idle.
# `starting` and `stopping` are reachable only for a run regin owns — nothing
# span-derived can see either — and rank beside `working` because both mean the
# session is busy with something the operator cannot type into.
# `retrying` ranks above `working` for the same reason the waiting- verdicts
# do: it is the one a reader would act on. A rollup that let a subagent's
# `working` mask it would hide the fact that the session is stalled on the
# network behind a word that says it is fine.
_PHASE_PRECEDENCE = (
    'waiting-permission', 'waiting-input', 'retrying', 'working', 'starting',
    'stopping', 'idle', 'inactive-stale', 'ended',
)


def _waiting_kind(act) -> str | None:
    """'permission' | 'input' | None — the agent is blocked on a human iff a
    PENDING permission/ask is its NEWEST span (a later resolved span means the
    blocker was answered, which also makes it demotable in merge.py)."""
    last = _roster_ts(act.get('last_ts'))
    perm = _roster_ts(act.get('perm_ts'))
    ask = _roster_ts(act.get('ask_ts'))
    if perm and (last is None or perm >= last):
        return 'permission'
    if ask and (last is None or ask >= last):
        return 'input'
    return None


def _idle_phase(dt, ended) -> str:
    """Non-blocked main phase from activity age alone."""
    if ended:
        return 'ended'
    if dt is None:
        return 'idle'
    if dt > INACTIVE_THRESHOLD_SEC:
        return 'inactive-stale'
    return 'working' if dt <= WORKING_WINDOW_SEC else 'idle'


def _main_phase(act, now, ended) -> str:
    """Main-agent phase. Only `ended` demotes a blocker.

    Silence is NOT evidence against a blocker that is the session's newest
    span: the agent cannot emit anything until a human answers, so the stale
    window measures the wait rather than the session's health. Ageing it out
    turned every question parked longer than INACTIVE_THRESHOLD_SEC into
    'quiet' — the one verdict that hides the sessions this surface exists to
    surface. `_roster_entry_status` has always ranked a subagent's `waiting`
    ahead of its age gate; main now agrees.

    The cost is deliberate: a session hard-killed while parked keeps claiming
    attention until `session.end` arrives or it is closed by hand."""
    last = _roster_ts(act.get('last_ts')) if act else None
    dt = (now - last).total_seconds() if last else None
    kind = _waiting_kind(act) if act else None
    if kind and not ended:
        return 'waiting-permission' if kind == 'permission' else 'waiting-input'
    return _idle_phase(dt, ended)


def _phase_from_roster(status, kind) -> str:
    """Map a subagent roster status into the phase vocabulary for the rollup."""
    if status in ('finished', 'interrupted'):
        return 'ended'
    if status == 'stale':
        return 'inactive-stale'
    if status == 'waiting':
        return 'waiting-permission' if kind == 'permission' else 'waiting-input'
    return 'working'


def _phase_rollup(phases) -> str | None:
    present = set(phases)
    for p in _PHASE_PRECEDENCE:
        if p in present:
            return p
    return None


# The phases a terminal screen is allowed to overrule — the ones derived from
# activity age alone, which is exactly the evidence a hook-silent stretch
# starves.
_SCREEN_ARBITRABLE = ('working', 'idle', 'inactive-stale')


def _screen_phase(state, span_phase: str) -> str | None:
    """Main-agent phase from the terminal's own status line, or None.

    A busy screen is the only evidence that survives a hook-silent stretch:
    spans stop while the agent thinks, streams, or sits inside one long tool
    call, so `_main_phase` reads `idle` within WORKING_WINDOW_SEC of a session
    plainly at work. The rendered screen says so directly.

    The reverse is deliberately NOT symmetric. `busy=False` means "no status
    line was recognised above the composer", which a build whose status line
    this parser has not learned yet produces just as readily as a session
    genuinely at the prompt — and an authoritative `idle` over a span that
    says `working` would reinstate the exact bug this exists to remove, with
    more authority than the heuristic it overrode. So a quiet screen may only
    clear `inactive-stale`: a readable claude pane proves the session is
    ALIVE, which is all that verdict was ever claiming otherwise."""
    if state is None:
        return None
    if state.busy:
        return 'working'
    return 'idle' if span_phase == 'inactive-stale' else None


def _session_phase(by_aid: dict, roster, ended: bool,
                   main_override: str | None = None,
                   screen=None) -> tuple:
    """(rollup phase, agent_phase map). Main from its own activity; each
    subagent from its roster status. `by_aid` is the shared per-agent activity
    map (main under None) so no third scan is needed.

    `screen` is the parsed terminal frame (`agent_bridge.screen_state`) when
    one could be read, and outranks the span-derived verdict for the same
    reason `main_override` does — see `_screen_phase`.

    `main_override` is the live runner's own verdict for a session regin
    launched (`agent_sdk.run_phase`), and it *replaces* the span-derived one
    rather than competing with it: the runner knows, and the heuristic's
    staleness window is exactly wrong for it — a long tool call emits no spans,
    so an agent plainly at work reads `inactive-stale`. Subagents keep their
    roster-derived phases either way; the roster is the only source for those.

    Rollup = highest-precedence phase across all agents, EXCEPT that an ended
    session always rolls up to 'ended': once the session itself ended nothing
    can be genuinely waiting, so a ghost unstopped subagent (rolled up as
    inactive-stale) must not mask the 'ended' verdict."""
    agent_phase = {'main': _main_agent_phase(by_aid, ended, main_override, screen)}
    for r in roster:
        aid = r['agent_id']
        agent_phase[aid] = _phase_from_roster(
            r['status'], _waiting_kind(by_aid.get(aid) or {}))
    if agent_phase['main'] == 'ended':
        return 'ended', agent_phase
    votes = _rollup_votes(agent_phase, main_override)
    return _phase_rollup(votes) or agent_phase['main'], agent_phase


def _main_agent_phase(by_aid, ended, main_override, screen):
    span_phase = _main_phase(by_aid.get(None), datetime.now(), ended)
    # The screen only arbitrates between working/idle/inactive-stale. It never
    # overrides 'ended' (a settled session's pane can still show the last frame
    # of a finished turn) nor either 'waiting-' verdict, which come from a
    # structured hook payload and beat reading a menu off the terminal.
    screen_phase = (_screen_phase(screen, span_phase)
                    if span_phase in _SCREEN_ARBITRABLE else None)
    return main_override or screen_phase or span_phase


def _rollup_votes(agent_phase, main_override):
    # A runner reporting `idle` has no turn in flight AND no delegated
    # background agent still running (`run_phase` reports those as
    # `working`) — so a roster still claiming a subagent is RUNNING is a ghost
    # (a lost `subagent.stop`), and the ordinary rollup would let it outrank
    # the runner's honest `idle`. Drop those votes when the runner says
    # nothing is executing.
    #
    # Only the `working` votes. A `waiting-` subagent is a PARK, which outlives
    # the turn that opened it and is exactly what the switcher exists to
    # surface — silence after a blocker measures the wait, not the session's
    # health. And the roster stays the only source for each subagent's own
    # entry in `agent_phase` either way; this narrows what votes, not what is
    # reported per agent.
    if main_override != 'idle':
        return agent_phase.values()
    return [p for aid, p in agent_phase.items()
            if aid == 'main' or p != 'working']


def _pct(value, window) -> float | None:
    """`value / window` as a percentage (1 dp), or None when not computable."""
    if isinstance(value, int) and window and window > 0:
        return round(value * 100.0 / window, 1)
    return None


def _workflow_total_tokens(trace_id: str) -> int | None:
    """A workflow run's authoritative grand total (manifest ``totalTokens``)
    from its run-root span; None for non-workflow sessions."""
    from lib.orm.engine import get_connection

    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT json_extract(attributes, '$.total_tokens') FROM session_spans "
            "WHERE trace_id = ? AND name = 'session.start' "
            "AND json_extract(attributes, '$.total_tokens') IS NOT NULL LIMIT 1",
            (trace_id,)).fetchone()
    finally:
        conn.close()
    return int(row[0]) if row and row[0] is not None else None


def _display_title_fields(trace_id, title, title_source) -> dict:
    """The same resolved title the session list serves for this row."""
    from lib.features import session_display
    task = session_display.tasks_for_sessions([trace_id]).get(trace_id)
    shown, source = session_display.display_title(title, title_source, task)
    return {'task': task, 'display_title': shown, 'display_title_source': source}


def _session_repo_name(trace_id: str, cwd) -> str | None:
    """Short repo label for the session header: the primary registered repo
    name, falling back to the basename of the session cwd."""
    from sqlmodel import select as _sel
    from lib.orm.models import Repo, SessionRepo
    with SessionLocal() as session:
        name = session.exec(
            _sel(Repo.name)
            .join(SessionRepo, SessionRepo.repo_id == Repo.id)
            .where(SessionRepo.trace_id == trace_id)
            .order_by(SessionRepo.is_primary.desc(), Repo.name.asc())
        ).first()
    if name:
        return name
    if isinstance(cwd, str) and cwd.strip():
        return os.path.basename(cwd.rstrip('/')) or None
    return None


def _owned_run_phase(trace_id: str) -> str | None:
    """The live runner's own phase for a run regin launched, else None.

    Best-effort like every other SDK read on this path: the tier is optional
    and gated off by default, and a session's status line must not depend on it
    being importable.
    """
    try:
        from lib import agent_sdk
        return agent_sdk.run_phase(trace_id)
    except Exception:
        return None


def _live_child_of(trace_id: str) -> str | None:
    """The still-running child of a run whose session `trace_id` names is over.

    `/clear` retires a child and opens a new one, so the id the operator is
    holding — the run's own, or the child that was just cleared — describes an
    ended session while the agent keeps working under an id nothing on the page
    points at. Best-effort like every other SDK read on this path.

    Both tiers answer it, because the operator asking is not thinking about
    which one they are on. The SDK registry is asked first and wins: a run
    regin owns holds the fact in process, where a `bridge_panes` row for the
    same session could only ever be a weaker copy of it. The tmux read sits
    behind `agent_bridge.enabled` like every other pane read: with the bridge
    off there is no terminal to send the successor a prompt through, so naming
    one would only be a field the page cannot act on.
    """
    from lib import agent_sdk
    try:
        child = agent_sdk.live_child(trace_id)
    except Exception:
        child = None
    if child:
        return child
    from lib.settings import settings
    if not settings.agent_bridge.enabled:
        return None
    try:
        from lib.agent_bridge import store as bridge_store
        return bridge_store.clear_successor(trace_id)
    except Exception:  # noqa: BLE001 - a summary must never 500 on this
        from lib.activity_log import get_activity_logger
        get_activity_logger("bridge").error(
            "bridge_successor_read_failed", exc_info=True)
        return None


def _screen_state_mod():
    from lib.agent_bridge import screen_state
    return screen_state


def _live_screen_state(trace_id: str, group):
    """The session's live status, or None when there is no verdict.

    Which source answers is decided once, from the tier regin recorded when
    the session was launched: a run regin owns is answered by its runner in
    this process, and every other session by its terminal. The two are never
    merged nor tried in turn — a run's child is spawned with the bridge off
    and has no pane at all, so probing tmux for one spends a subprocess to
    learn nothing. Asked over the whole alias group, since a run is reachable
    under either of the two ids it is traced as.

    Fail-soft by construction: the bridge is off by default and reaches only
    tmux-launched sessions, so "no screen" is the ordinary case, not an error.
    Any failure below the API — bridge disabled, no pane, stale registry row,
    a tmux that will not answer — resolves to None and the caller keeps the
    span-derived phase.
    """
    try:
        from lib import agent_sdk
        for tid in group:
            owned = agent_sdk.run_live_state(tid)
            if owned.sdk:
                return owned.state
        from lib.agent_bridge import delivery
        return delivery.live_state(trace_id, accept=set(group))
    except Exception:  # noqa: BLE001 - a screen read must never fail a summary
        from lib.activity_log import get_activity_logger
        get_activity_logger("bridge").error(
            "live_screen_read_failed", exc_info=True)
        return None


def _record_screen_retry(group, screen) -> None:
    """Persist a retry the screen is showing, so the turn keeps a record of it.

    `live_screen` only ever describes NOW; the span is what the operator can
    still read once the retry has resolved.
    """
    from lib.agent_bridge import retry_events
    retry_events.record_retry(group, screen)


def _subagent_session_summary(trace_id: str) -> dict:
    """The header for a subagent trace, synthesised because no `sessions` row
    exists under an `sa-…` id (see `lib.trace.subagent_trace.subagent_summary`
    for what each field is derived from).

    Deliberately narrower than the ordinary summary: the phase rollup, the
    live screen read and the steer-channel fields all describe a whole CLI
    session, and a child is a slice of one — reporting them here would answer
    for the parent under the child's name.
    """
    from lib.orm.engine import get_connection
    from lib.trace.subagent_trace import subagent_summary
    conn = get_connection()
    try:
        return subagent_summary(conn, trace_id) or {}
    finally:
        conn.close()


def _session_summary(trace_id: str, roster=None, activity=None) -> dict:
    """Read the session-row fields that aren't derivable from spans alone
    (model + transcript-derived token counters) plus the server phase verdict.
    Safe to call for a non-existent trace_id — returns an empty dict.

    `roster` + `activity` (the whole-session agent roster and its shared
    per-agent activity map) are threaded from the shallow-map hot path so the
    phase rollup reuses them instead of recomputing; other callers pass None
    and both are computed here in one grouped pass.
    """
    from sqlmodel import select as _select
    from lib.tokens.model_windows import infer_window as _infer_window
    from lib.trace.subagent_trace import is_subagent_trace_id
    if is_subagent_trace_id(trace_id):
        return _subagent_session_summary(trace_id)
    group = _alias_group_ids(trace_id)
    with SessionLocal() as session:
        row = session.exec(
            _select(
                SessionModel.model,
                SessionModel.cwd,
                SessionModel.input_tokens, SessionModel.output_tokens,
                SessionModel.cache_read_tokens, SessionModel.cache_creation_tokens,
                SessionModel.peak_context_tokens,
                SessionModel.peak_main_context_tokens,
                SessionModel.live_context_tokens,
                SessionModel.active_work_ms,
                SessionModel.started_at,
                SessionModel.ended_at,
                SessionModel.last_seen,
                SessionModel.status,
                SessionModel.ended_reason,
                SessionModel.title,
                SessionModel.title_source,
                SessionModel.origin,
            )
            # Prefer the canonical row, but fall back to whichever member of
            # the group HAS one. A regin-launched run whose child never wrote a
            # hook trace — an install where regin's hooks aren't wired for that
            # cwd — has no canonical row at all, and keying on it blindly wiped
            # the whole header (status, title, model, phase all null) for every
            # such run.
            .where(SessionModel.trace_id.in_(group))
            .order_by((SessionModel.trace_id == group[0]).desc())
        ).first()
    if not row:
        return {}
    (model, cwd, input_tokens, output_tokens,
     cache_read, cache_creation, peak, peak_main, live, active_work_ms,
     started_at, ended_at, last_seen, status, ended_reason,
     title, title_source, origin) = row
    ended_reason = _group_ended_reason(group, ended_reason)
    # Compute window at read time from the session's richer `model` id —
    # see _row_to_dict() for the rationale. Window inference uses the
    # all-inclusive peak; the headline `context_pct` divides the *live*
    # peak by it (main-flow high-water mark since the last `/compact`, so
    # it drops when the session compacts), `context_pct_all` divides the
    # full peak (shown alongside only when they diverge).
    window = _infer_window(model, peak) if isinstance(peak, int) else None
    main_for_pct = next(
        (v for v in (live, peak_main, peak) if isinstance(v, int)), peak)
    if roster is None or activity is None:
        roster, activity, ended = _roster_with_activity(trace_id)
    screen = _live_screen_state(trace_id, group)
    _record_screen_retry(group, screen)
    phase, agent_phase = _session_phase(activity, roster, status == 'ended',
                                        _owned_run_phase(trace_id), screen)
    return {
        # What the real terminal says is happening right now — the verb,
        # turn elapsed, tokens streamed and in-flight tool the operator would
        # read off the pane. Null whenever the screen could not be read, which
        # the card renders as "open the terminal", never as idle.
        'live_screen': _screen_state_mod().as_payload(screen),
        # The id this session is really keyed on — the child's for a
        # regin-launched run. `/live` navigates to the run's own `sdk-…` id at
        # launch (the only id that exists then) and rewrites onto this one as
        # soon as the child names itself, so a URL that is shared, bookmarked
        # or reloaded is the one every other reader already uses.
        'canonical_trace_id': group[0],
        # The child a live run is driving, when that is not the session this id
        # names — so it is set for a run's own id, and for a child `/clear`
        # retired. That second case is the one it exists for: the page the
        # operator is on reads `ended` while the agent works on elsewhere, and
        # the composer stays open on the strength of this (a steer sent to
        # either id reaches the same runner, and on the tmux tier the same
        # pane). Null for every ordinary session.
        'successor_trace_id': _live_child_of(trace_id),
        'model': model,
        # Server phase verdict — the /live card selects on this, never
        # re-derives it. `phase` = session rollup; `agent_phase` = "main"
        # + each subagent id → its phase.
        'phase': phase,
        'agent_phase': agent_phase,
        # The same runner-only verdict the session LIST carries, under the
        # same name, so the header and the row it was opened from resolve
        # their status through one field. Null for a session regin only
        # traces; `phase` above is always present and is the richer,
        # heuristic-backed rollup the /live card renders.
        'runner_phase': _owned_run_phase(trace_id),
        # Mid-turn or parked, from the session's own turn boundaries — what
        # the list's recency fallback reads, so the header agrees with it.
        'turn_state': turn_state(group[0]),
        'repo': _session_repo_name(trace_id, cwd),
        # The working directory the session ran in. `repo` is only the short
        # label; the header shows the path itself, which is what tells two
        # checkouts of the same repo apart.
        'cwd': cwd,
        # Workflow runs have no single context window (peak is NULL), so the
        # ctx% chip is absent; surface the run's authoritative grand total
        # (manifest totalTokens, stamped on the run-root span) for a header
        # total chip. None for non-workflow sessions. The per-turn split in
        # the columns below can't be summed to it — cache is counted
        # differently — so it's read straight off the span, not derived.
        'total_tokens': _workflow_total_tokens(trace_id),
        'input_tokens': input_tokens,
        'output_tokens': output_tokens,
        'cache_read_tokens': cache_read,
        'cache_creation_tokens': cache_creation,
        'peak_context_tokens': peak,
        'peak_main_context_tokens': peak_main,
        'live_context_tokens': live,
        'context_window_tokens': window,
        'context_pct': _pct(main_for_pct, window),
        'context_pct_all': _pct(peak, window),
        'active_work_ms': active_work_ms,
        'started_at': started_at,
        'ended_at': ended_at,
        'last_seen': last_seen,
        # status/ended_reason ride the summary so the /live poll can flip a
        # session to "finished" (or back, on resume) without a separate row
        # fetch — the row endpoint alone left the header frozen at its
        # page-load value.
        'status': status,
        'ended_reason': ended_reason,
        # Server wall-clock at read time (naive local, same basis as every
        # span start_time). The /live NOW-zone elapsed anchors to THIS instead
        # of the viewer's Date.now(): span timestamps are the server's local
        # time, so a phone in a different timezone subtracting its own clock
        # leaks the offset (a tool reads "4h00m"). server_now − start_time is
        # server−server, so the offset cancels.
        'server_now': datetime.now().isoformat(),
        # The UTC twin is what serverAgeMs anchors against when the row's own
        # timestamp carries an explicit zone; without it a zoned last_seen is
        # aged against a naive-local anchor and the offset leaks into the age.
        'server_now_utc': datetime.now(timezone.utc).strftime(
            '%Y-%m-%dT%H:%M:%S.%f')[:-3] + 'Z',
        'title': title,
        'title_source': title_source,
        **_display_title_fields(group[0], title, title_source),
        # Same axis the sessions list exposes (origin == 'workflow'): a
        # workflow run's turn_usage rows are per-agent API responses, so the
        # trace view must not read them as user-prompt turns.
        'origin': origin,
        'is_workflow': origin == 'workflow',
    }
