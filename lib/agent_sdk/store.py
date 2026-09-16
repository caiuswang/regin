"""Durable record of regin-launched runs (`agent_runs`)."""

from __future__ import annotations

import os

from sqlalchemy.exc import OperationalError
from sqlmodel import select

from lib.activity_log import get_activity_logger
from lib.orm import SessionLocal
from lib.orm.models import AgentRun, AgentRunSession, Session as TraceSession

log = get_activity_logger("agent_sdk")


def revive_trace_session(*trace_ids: str) -> list[dict]:
    """Clear the end marker on the `sessions` rows a run is relaunching under.

    A run resumed as itself keeps its trace id, so the row already carries the
    `ended_at`/`status='ended'` of its previous life. The runner only clears
    that by emitting `session.start` — and it emits that *after* `connect()`
    has spawned the child, seconds later or never if the spawn fails. Every
    reader in between believes the session is over, and `/live` does more than
    render it wrong: `useLiveTail.scheduleNext()` stops polling for good on an
    ended session, so the card never notices the run start and shows no
    composer until the operator reloads the page.

    Takes EVERY id in the run's alias group, not just the one the launch names.
    A trace read resolves a group to its canonical member — the child's session
    id (`lib/trace/alias.trace_group`) — so reviving the `sdk-…` row alone
    leaves both entry points still reading the child's ended one.

    Called synchronously from the launch, before the route answers, so the
    client's first read already sees a live session. Returns the markers it
    cleared, which `restore_trace_session` puts back if the run never starts.
    """
    wanted = {t for t in trace_ids if t}
    if not wanted:
        return []
    cleared = []
    with SessionLocal() as session:
        rows = session.exec(
            select(TraceSession).where(TraceSession.trace_id.in_(wanted))).all()
        for row in rows:
            # Only a row that actually claims to be over. A session that never
            # recorded a status is not ended, and stamping one 'active' would
            # invent liveness the launch has no evidence for.
            if row.ended_at is None and row.status != "ended":
                continue
            cleared.append({"trace_id": row.trace_id, "status": row.status,
                            "ended_at": row.ended_at,
                            "ended_reason": row.ended_reason})
            row.status = "active"
            row.ended_at = None
            row.ended_reason = None
        if cleared:
            session.commit()
    for marker in cleared:
        log.write("sdk_trace_session_revived", trace_id=marker["trace_id"])
    return cleared


def restore_trace_session(markers: list[dict]) -> None:
    """Put back markers `revive_trace_session` cleared, for a run that is over.

    The revive is optimistic: it declares the session live before the child
    exists, so a launch that dies before `connect()` — no `claude` on PATH, the
    SDK not installed, a capacity refusal on the loop — would strand a
    permanently 'active' session no `session.end` ever closes, green in every
    listing forever.

    Applied only while the row is still unmarked. A run that genuinely ran
    wrote its own `session.end`, and that verdict — the *new* one — must win
    over the stale marker being restored here.

    The caller owns the other half of that check: whether the id still belongs
    to the run being undone. This function cannot tell a row nothing wrote yet
    from one a *newer* run just revived.
    """
    if not markers:
        return
    ids = [m["trace_id"] for m in markers]
    restored = []
    try:
        with SessionLocal() as session:
            rows = {r.trace_id: r for r in session.exec(
                select(TraceSession).where(TraceSession.trace_id.in_(ids))).all()}
            for marker in markers:
                row = rows.get(marker["trace_id"])
                if row is None or row.ended_at is not None:
                    continue
                row.status = marker["status"]
                row.ended_at = marker["ended_at"]
                row.ended_reason = marker["ended_reason"]
                restored.append(row.trace_id)
            if restored:
                session.commit()
    except OperationalError:
        # Runs from the shared SDK loop's done-callback, where an escaping
        # exception is logged by the loop and drops every later callback in
        # the chain. A locked DB costs one stale row; it must not cost the
        # teardown of every other run.
        log.error("sdk_trace_session_restore_failed", trace_ids=",".join(ids))
        return
    for trace_id in restored:
        log.write("sdk_trace_session_end_restored", trace_id=trace_id)


def end_trace_session(*trace_ids: str, reason: str) -> list[str]:
    """Settle the `sessions` rows behind a run that died without saying so.

    The inverse of `revive_trace_session`, and the half `reap_orphaned_runs`
    was missing: killing the server takes the SDK child with it, so no
    `session.end` is ever emitted and the row keeps its launch-time
    `status='active'` / `ended_at IS NULL` forever. Every reader answers from
    those two fields — the header's Live pill, the session list, and
    `resumable._offerable` — so the session stays green, unsteerable (the
    registry that made it steerable died with the process) and unofferable.

    `ended_at` is the row's own `last_seen`, not `now`: that is the last moment
    regin has evidence the session existed, and stamping the reap's clock would
    date the session to whenever the server next happened to boot.

    Only rows still claiming to be live are touched. A session that recorded
    its own end wrote a verdict from the process that was actually there, and
    it outranks this inference.
    """
    wanted = {t for t in trace_ids if t}
    if not wanted:
        return []
    ended = []
    with SessionLocal() as session:
        # Already-settled rows are excluded in SQL, not filtered afterwards:
        # the boot sweep asks about every run regin ever launched, and that set
        # only grows, while the rows it may actually change do not.
        rows = session.exec(
            select(TraceSession)
            .where(TraceSession.trace_id.in_(wanted))
            .where(TraceSession.ended_at.is_(None))).all()
        for row in rows:
            # A row can carry `status='ended'` with no `ended_at` — the SQL
            # above cannot see that half of the verdict.
            if row.status == "ended":
                continue
            row.status = "ended"
            row.ended_at = row.last_seen
            row.ended_reason = reason
            ended.append(row.trace_id)
        if ended:
            session.commit()
    for trace_id in ended:
        log.write("sdk_trace_session_ended", trace_id=trace_id, reason=reason)
    return ended


# The two `agent_runs` statuses that mean a process is still behind the row.
# Everything else — `exited`, `failed` — is a run that is over, whether it ended
# by itself or was reaped.
_LIVE_RUN_STATUSES = frozenset({"running", "starting"})


def _still_claimed(trace_id: str) -> bool:
    """Whether a terminal is driving this session right now.

    A session regin once launched can be reopened by the operator themselves
    (`claude --resume <id>` in a terminal), which registers a pane under the id
    the dead run also names. Nothing in `agent_runs` records that — the run
    stays `exited` forever — so without this read the next boot would end a
    session that is genuinely live, with the reason "server restarted".

    Reads through `resolve_reachable_pane`, not `get_reachable_pane`: the
    latter collapses "asked, found nothing" into "could not ask" and answers
    None for both, which here would read a locked DB or a pre-migration
    `bridge_panes` as proof that no terminal holds the session. That is the
    distinction its `answered` flag exists to carry, and this is the caller its
    docstring names — one deciding whether it may report a session stopped.

    An unread registry therefore counts as claimed, as does a raised probe: not
    knowing is not evidence the session is over, and guessing wrong in that
    direction is the very bug this module is fixing, inverted.

    Answers False wherever regin holds no pane rows at all — an install running
    without `REGIN_BRIDGE` set never registers one (`hook_manager/handlers/
    bridge_registry.py`). That is an information gap this cannot close: there
    the live-run subtraction in `_dead_run_candidates` is the only guard.
    """
    from lib.agent_bridge import store as bridge_store

    try:
        pane, answered = bridge_store.resolve_reachable_pane(trace_id)
    except Exception:
        log.error("sdk_settle_pane_probe_failed", trace_id=trace_id,
                  exc_info=True)
        return True
    return pane is not None or not answered


def _children_by_run(session) -> dict[str, list[str]]:
    """`{run trace_id: its child session ids}` — the whole alias group.

    Every child, not just the one `agent_runs.cli_session_id` pins: a run's
    later children are the ones a `/clear` opened, and a sweep that cannot see
    them leaves each heir's `sessions` row green forever after the run dies.
    """
    groups: dict[str, list[str]] = {}
    for run, child in session.exec(
            select(AgentRunSession.trace_id,
                   AgentRunSession.cli_session_id)).all():
        if run and child:
            groups.setdefault(run, []).append(child)
    return groups


def _run_ids(rows, children: dict[str, list[str]]) -> set[str]:
    """Every id these run rows name, blanks dropped."""
    return {t for r in rows
            for t in (r.trace_id, *children.get(r.trace_id, ())) if t}


def _dead_run_candidates(reason: str) -> list[tuple[str, str]]:
    """`(why, id)` for the dead runs, minus every id a LIVE run also names.

    A child id can be named by both an older `exited` row and a currently
    `running` one — the shape a relaunch leaves behind. Settling on the
    strength of the stale row would end a conversation this process is running,
    which is this module's own bug inverted.
    """
    with SessionLocal() as session:
        children = _children_by_run(session)
        live = _run_ids(session.exec(select(AgentRun).where(
            AgentRun.status.in_(_LIVE_RUN_STATUSES))).all(), children)
        dead = session.exec(select(AgentRun).where(
            AgentRun.status.notin_(_LIVE_RUN_STATUSES))).all()
        return [(r.detail or reason, t) for r in dead
                for t in (r.trace_id, *children.get(r.trace_id, ()))
                if t and t not in live]


def _settleable(reason: str) -> dict[str, list[str]]:
    """`{why: ids}` — the candidates no terminal is still driving either."""
    by_reason: dict[str, list[str]] = {}
    for why, trace_id in _dead_run_candidates(reason):
        if not _still_claimed(trace_id):
            by_reason.setdefault(why, []).append(trace_id)
    return by_reason


def settle_ended_run_sessions(reason: str = "server restarted") -> int:
    """Close the trace rows of every run that is over but whose session never
    said so. Returns how many rows were settled.

    Keyed on the run's *state*, not on the transition into it, and therefore
    re-runnable: the reap only touches rows it finds `running`, so a session
    orphaned by an earlier boot — its run row already `exited` — would never be
    reached by a settle wired to the reap alone, and would sit in the UI as a
    permanently live session no later boot could correct.

    A run still `running` or `starting` is skipped whatever pid holds it: the
    reap runs first and has already demoted the ones no process backs, so a row
    still claiming a live state at this point is one this process owns. Its
    *ids* are spared too, not just its row — see `_dead_run_candidates` — and
    so is any session a terminal is still driving (`_still_claimed`).

    Each session is stamped with its own run's `detail` — why *that* run
    stopped. A sweep that wrote one blanket reason would tell an operator their
    session died in a restart when the run had failed on its own.
    """
    return sum(len(end_trace_session(*ids, reason=why))
               for why, ids in _settleable(reason).items())


def upsert_run(trace_id: str, *, status: str, pid: int | None = None,
               cwd: str | None = None, model: str | None = None,
               detail: str | None = None) -> None:
    """Create or advance the run row for `trace_id`."""
    with SessionLocal() as session:
        row = session.exec(
            select(AgentRun).where(AgentRun.trace_id == trace_id)).first()
        if row is None:
            row = AgentRun(trace_id=trace_id, status=status)
            session.add(row)
        row.status = status
        if pid is not None:
            row.pid = pid
        if cwd is not None:
            row.cwd = cwd
        if model is not None:
            row.model = model
        if detail is not None:
            row.detail = detail
        elif status == "running":
            # `detail` explains how a run ended, so a revived row would keep
            # answering pollers with why its *previous* life stopped.
            row.detail = None
        session.commit()
    log.write("sdk_run_status", trace_id=trace_id, status=status)


def _process_alive(pid: int | None) -> bool:
    """Whether `pid` still names a running process.

    `EPERM` counts as alive: the process exists, this user merely may not
    signal it. Only `ESRCH` is proof of absence, and a pid of None — a row
    written before the process was known — cannot be probed at all.
    """
    if not pid:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except OSError:
        return True
    return True


def run_is_live(trace_id: str) -> bool:
    """Whether the durable row for `trace_id` is backed by a process that still
    exists.

    The row alone cannot answer it: `status` outlives the runner, so a server
    killed mid-run leaves `running` behind forever. The pid is what confirms
    it, and it is probed rather than compared — a row belonging to another pid
    is a *peer's* live run, not a dead one, which is the distinction
    `reap_orphaned_runs` exists to keep. This process's own pid is spared
    without a probe for the same reason it is there.
    """
    row = get_run(trace_id)
    if row is None or row["status"] not in ("starting", "running"):
        return False
    return row["pid"] == os.getpid() or _process_alive(row["pid"])


def reap_orphaned_runs(detail: str = "server restarted") -> int:
    """Close out `running` rows no live process backs any more.

    A runner only exists inside the process that started it, so a row still
    claiming `running` under a dead pid is a leftover — the run died with that
    process. Left alone they accumulate as sessions the UI offers to steer and
    nothing can answer.

    A different pid is NOT enough to condemn a row, and reading it that way is
    what let a foreign process — a CLI, a test that builds the app against the
    real DB — reap the runs of a `regin serve` that was driving them, taking
    their `sessions` rows down with it in the sweep below. Every reader then
    met the state this function exists to prevent, on a session that was alive.
    So liveness is asked of the pid itself; rows owned by **this** pid stay
    spared without a probe (an app built twice in one process — tests, an
    embedded server — would otherwise mark its own live runs dead).

    The trace rows behind every dead run are settled with it, in the sweep
    below. Closing only the `agent_runs` row left the session itself claiming
    to be live, which is the state an operator meets — a green session with no
    composer, no pane, and no resume.
    """
    mine = os.getpid()
    with SessionLocal() as session:
        rows = [
            r for r in session.exec(
                select(AgentRun).where(AgentRun.status == "running")).all()
            if r.pid != mine and not _process_alive(r.pid)
        ]
        for row in rows:
            row.status = "exited"
            row.detail = detail
        session.commit()
        count = len(rows)
    if count:
        log.write("sdk_runs_reaped", count=count)
    settle_ended_run_sessions(detail)
    return count


def set_cli_session(trace_id: str, cli_session_id: str) -> str | None:
    """Record a child `claude` session this run is traced as.

    Called for every message the runner sees, and a run names more than one
    child over its life: `/clear` ends the child conversation and the CLI opens
    another under a fresh id while this same runner keeps driving it. Every one
    of them joins the run's alias group (`agent_run_sessions`), which is what
    `find_run`, the dual-writer guard, the resume picker and the heal sweep
    read — so an heir is recognised as regin's rather than looking like a
    session nothing launched.

    `agent_runs.cli_session_id` takes only the FIRST child and then stays put.
    A `/clear` opens a new conversation rather than continuing the old one, so
    moving the column would fuse two sessions a person deliberately separated
    and orphan the spans of the half it moved off. Each heir reads its own half
    of the SDK stream through a segment trace instead
    (`lib.trace.alias.sdk_span_trace_id`).

    Nothing here is a conflict. A child named by another run is the relaunch
    shape — resuming keeps the child's session id and the previous `exited` row
    goes on naming it — and `reap_orphaned_runs` already resolves that by
    subtracting the live run's ids before settling the dead one's.

    Returns the run's first child — what the column holds once this call is
    done — or None when nothing could be recorded. The caller needs it to tell
    an heir from the original, and taking it from the row that was already
    loaded here saves the runner a read per message.
    """
    if not trace_id or not cli_session_id or trace_id == cli_session_id:
        return None
    try:
        with SessionLocal() as session:
            row = session.exec(
                select(AgentRun).where(AgentRun.trace_id == trace_id)).first()
            if row is None:
                return None
            first_child = row.cli_session_id or cli_session_id
            linked = session.exec(
                select(AgentRunSession.id)
                .where(AgentRunSession.trace_id == trace_id)
                .where(AgentRunSession.cli_session_id == cli_session_id)).first()
            if linked:
                return first_child
            session.add(AgentRunSession(trace_id=trace_id,
                                        cli_session_id=cli_session_id))
            if not row.cli_session_id:
                row.cli_session_id = cli_session_id
            session.commit()
    except OperationalError:
        # A DB predating migration 0012 has no column, one predating 0016
        # no table. This runs on the first message of the first turn, so
        # raising would propagate out of the turn and kill the run — an
        # unmigrated install could not hold a session at all. The alias is an
        # optimisation for the trace view; the run itself does not need it.
        # Booting the server does not migrate, so this state is reachable, and
        # every reader already defends it.
        log.error("sdk_cli_session_link_unavailable", trace_id=trace_id,
                  detail="agent_runs.cli_session_id / agent_run_sessions "
                         "missing — run `regin migrate`")
        return None
    log.write("sdk_cli_session_linked", trace_id=trace_id,
              cli_session_id=cli_session_id)
    return first_child


def latest_cli_session(trace_id: str) -> str | None:
    """The newest child conversation this run named, or None.

    `agent_runs.cli_session_id` pins the FIRST child and never moves
    (`set_cli_session` explains why), so it is the wrong answer to "which
    conversation is this run on now": a run that has since opened another —
    a `/clear`, or a resume the CLI renamed — would be continued into the half
    the operator left behind while the one they were reading is stranded with
    neither a composer nor a Resume button.

    Newest by insert order, which is the order `set_cli_session` records
    children in. Falls back to the pinned column so a run predating the alias
    table still answers.
    """
    if not trace_id:
        return None
    try:
        with SessionLocal() as session:
            row = session.exec(
                select(AgentRunSession.cli_session_id)
                .where(AgentRunSession.trace_id == trace_id)
                .order_by(AgentRunSession.id.desc())).first()
        if row:
            return row
    except OperationalError:
        pass
    return cli_session_for(trace_id)


def sdk_child_session_ids() -> set[str]:
    """Every CLI session id that is (or was) an SDK run's child.

    Membership is not status-gated: a finished run's child id stays claimed —
    no future hook-only session can legitimately reuse it, and treating it as
    hook-owned again would revive the dual-writer split this exists to close.

    Read from the alias group, so every child counts. Taking only
    `agent_runs.cli_session_id` saw the first one and left each `/clear` heir
    looking hook-owned — the split, on exactly the sessions a run drives last.
    """
    try:
        with SessionLocal() as session:
            rows = session.exec(select(AgentRunSession.cli_session_id)).all()
        return {r for r in rows if r}
    except OperationalError:
        # Pre-0016 DB: no table, so no children to speak of.
        return set()


def cli_session_for(trace_id: str) -> str | None:
    """The CLI child session this run is also traced as, or None."""
    if not trace_id:
        return None
    try:
        with SessionLocal() as session:
            row = session.exec(
                select(AgentRun.cli_session_id)
                .where(AgentRun.trace_id == trace_id)).first()
        return row or None
    except OperationalError:
        return None


def sdk_trace_for_cli_session(session_id: str) -> str | None:
    """The SDK run a CLI session belongs to, or None when it is hook-only —
    the DB-backed alias for processes (hooks) that cannot see the registry."""
    if not session_id or session_id.startswith('sdk-'):
        return None
    try:
        with SessionLocal() as session:
            row = session.exec(
                select(AgentRunSession.trace_id)
                .where(AgentRunSession.cli_session_id == session_id)).first()
        return row or None
    except OperationalError:
        return None


# How far apart the two sessions' `started_at` may be and still be the same
# run. The child is spawned from `connect()` and its SessionStart hook fires
# before the runner emits its own `session.start`, so the real gap is
# sub-second; 15s is slack for a loaded machine, still far tighter than the
# gap between two separately launched runs.
_HEAL_WINDOW_SEC = 15.0


def _shared_call_candidates(conn, run) -> list[str]:
    """Sessions sharing a `toolu_*` with this run — the strongest signal there
    is, though not proof.

    A `toolu_*` is minted once by the model, so two traces holding one recorded
    the same call. They are still not necessarily the same *session*: a resume
    or a fork copies the call into a second trace, and this store has 459 such
    ids spread across sibling traces. So the caller's "exactly one candidate"
    rule stays load-bearing — with a fork twin present the run is left
    unaliased rather than linked to the wrong half.

    Silent on a run that called no tools (a one-line answer, a smoke test);
    those fall back to `_heal_candidates`.
    """
    mine = _call_ids(conn, run)
    if not mine:
        return []
    # Checked per candidate rather than as one self-join across the store: the
    # join form is quadratic over `session_spans` (measured 80s on a 2k-session
    # DB) and this runs at every server start.
    return [tid for tid in _nearby_sessions(conn, run)
            if _call_ids(conn, tid) & mine]


def _call_ids(conn, trace_id: str) -> set:
    """Every `toolu_*` this trace recorded.

    COALESCE because only the hook writer fills the promoted column; the runner
    leaves it NULL and carries the id in `attributes` alone, so reading the
    column alone would come back empty for exactly the rows this has to match.
    """
    return {r[0] for r in conn.execute("""
        SELECT DISTINCT COALESCE(tool_use_id,
                                 json_extract(attributes, '$.tool_use_id'))
        FROM session_spans WHERE trace_id = ?
    """, (trace_id,)).fetchall() if r[0]}


# Wider than `_HEAL_WINDOW_SEC` because it only narrows which sessions get
# checked — the shared-call proof, not this bound, decides the match.
_HEAL_PROOF_WINDOW_SEC = 120.0


def _nearby_sessions(conn, run) -> list[str]:
    """Unclaimed non-SDK sessions that started near this run."""
    return [r["trace_id"] for r in conn.execute("""
        SELECT child.trace_id
        FROM sessions AS child
        JOIN sessions AS run ON run.trace_id = ?
        WHERE child.trace_id <> run.trace_id
          AND child.trace_id NOT LIKE 'sdk-%'
          AND ABS(julianday(child.started_at)
                  - julianday(run.started_at)) * 86400.0 < ?
          AND child.trace_id NOT IN (
              SELECT cli_session_id FROM agent_run_sessions)
    """, (run, _HEAL_PROOF_WINDOW_SEC)).fetchall()]


def _heal_candidates(conn, run) -> list[str]:
    """Hook sessions that could be `run`'s child, closest-first.

    Compared through the two rows' `sessions.started_at` rather than
    `agent_runs.created_at`: that column is `datetime('now')` (UTC) while
    `sessions.started_at` is local, and correlating across the two would drift
    by the machine's whole UTC offset.

    Title equality is the load-bearing condition, not the timestamp. Both rows
    title themselves from the same first prompt, so an unrelated session the
    user happened to start seconds later in the same directory — which time and
    cwd alone cannot rule out — does not match. `cwd` is compared NULL-safely
    with no escape hatch: a run with no recorded cwd matches only a child that
    also has none, because a mis-link fuses two unrelated sessions into one
    trace and nothing in the data can undo it afterwards.
    """
    return [r["trace_id"] for r in conn.execute("""
        SELECT child.trace_id
        FROM sessions AS child
        JOIN sessions AS run ON run.trace_id = ?
        WHERE child.trace_id <> run.trace_id
          AND child.trace_id NOT LIKE 'sdk-%'
          AND child.cwd IS run.cwd
          AND child.title IS run.title
          AND run.title IS NOT NULL
          AND ABS(julianday(child.started_at)
                  - julianday(run.started_at)) * 86400.0 < ?
          AND child.trace_id NOT IN (
              SELECT cli_session_id FROM agent_run_sessions)
        ORDER BY ABS(julianday(child.started_at)
                     - julianday(run.started_at)) ASC
    """, (run, _HEAL_WINDOW_SEC)).fetchall()]


def _resolve_children(conn, pending: list[str]) -> dict:
    """`{run: child}` for the runs that resolve to exactly one candidate."""
    linked: dict = {}
    for run in pending:
        # Proof first, correlation only for a run that called no tools.
        candidates = (_shared_call_candidates(conn, run)
                      or _heal_candidates(conn, run))
        # Both queries exclude children already claimed in the DB, but two runs
        # in THIS pass can still reach for the same one — a claim made a moment
        # ago isn't committed yet.
        claimed = set(linked.values())
        candidates = [c for c in candidates if c not in claimed]
        # Exactly one, or nothing: see the ambiguity note in the caller.
        if len(candidates) == 1:
            linked[run] = candidates[0]
    return linked


def heal_cli_session_ids(conn=None) -> int:
    """Link `sdk-*` runs to their child session where the runner never did.

    Re-runnable and EXISTS-gated (only `cli_session_id IS NULL` rows are
    considered) rather than a one-shot migration backfill: rows keep arriving
    from code paths that predate the write — a run whose child died before its
    first message, a server killed mid-run, and every run recorded before this
    column existed. A migration-gated backfill would heal the rows present the
    moment it ran and silently leak every one after.

    Ambiguity is refused, not guessed: a run matching two plausible children is
    left NULL, because mis-linking merges two unrelated sessions into one trace
    and there is no signal in the data to undo that later. Returns how many
    rows were linked.
    """
    from lib.orm.engine import get_connection

    owned = conn is None
    conn = get_connection() if owned else conn
    try:
        pending = [r["trace_id"] for r in conn.execute("""
            SELECT trace_id FROM agent_runs
            WHERE cli_session_id IS NULL AND trace_id LIKE 'sdk-%'
        """).fetchall()]
        linked = _resolve_children(conn, pending)
        for run, child in linked.items():
            conn.execute(
                "UPDATE agent_runs SET cli_session_id = ? WHERE trace_id = ?",
                (child, run))
            # The group is what every membership reader asks; a link that only
            # lands in the column is invisible to all of them.
            conn.execute(
                "INSERT OR IGNORE INTO agent_run_sessions "
                "(trace_id, cli_session_id) VALUES (?, ?)", (run, child))
        conn.commit()
    finally:
        if owned:
            conn.close()
    if linked:
        log.write("sdk_cli_sessions_healed", count=len(linked))
    return len(linked)


def get_run(trace_id: str) -> dict | None:
    with SessionLocal() as session:
        row = session.exec(
            select(AgentRun).where(AgentRun.trace_id == trace_id)).first()
        if row is None:
            return None
        return {
            "trace_id": row.trace_id,
            "status": row.status,
            "pid": row.pid,
            "cwd": row.cwd,
            "model": row.model,
            "detail": row.detail,
            "cli_session_id": row.cli_session_id,
        }


def find_run(session_id: str) -> dict | None:
    """The run `session_id` names, by *either* of the two ids it is traced as.

    A merged run's canonical id is the child's — the session list hides the
    `sdk-…` half — so every id an operator can arrive with has to resolve here,
    not just the one the row is keyed on.

    `cli_session_id` carries a plain index, not a unique one, and rows claiming
    one child already exist from before the alias was written. The tiebreak is
    therefore the same `ORDER BY trace_id LIMIT 1` as
    `lib.trace.alias.trace_group`: resolving to a different row than the one
    the trace view treats as canonical would resume a conversation into a trace
    nothing renders.

    A DB that predates migration 0012 answers "no run" rather than raising:
    booting the server does not migrate, and callers read that as an id regin
    never launched — which is what such an install can honestly say, and leaves
    a terminal session resumable there instead of failing the whole launch.
    """
    if not session_id:
        return None
    try:
        exact = get_run(session_id)
        if exact is not None:
            return exact
        with SessionLocal() as session:
            aliased = session.exec(
                select(AgentRunSession.trace_id)
                .where(AgentRunSession.cli_session_id == session_id)
                .order_by(AgentRunSession.trace_id)).first()
        return get_run(aliased) if aliased else None
    except OperationalError as exc:
        # Answering "no run" sends the caller down the pass-through path, which
        # mints a second trace against a child that already has one. That is
        # the right trade for a missing column — the alternative is failing
        # every launch — but a transient error must not do it invisibly.
        log.error("sdk_run_lookup_unavailable", session_id=session_id,
                  detail=f"{exc} — if the column is missing, run `regin migrate`")
        return None
