"""Durable record of regin-launched runs (`agent_runs`)."""

from __future__ import annotations

import os

from sqlalchemy.exc import OperationalError
from sqlmodel import select

from lib.activity_log import get_activity_logger
from lib.orm import SessionLocal
from lib.orm.models import AgentRun, Session as TraceSession

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


def reap_orphaned_runs(detail: str = "server restarted") -> int:
    """Close out `running` rows no live process backs any more.

    A runner only exists inside the process that started it, so a row still
    claiming `running` under a *different* pid is a leftover — the run died
    with that process. Left alone they accumulate as sessions the UI offers to
    steer and nothing can answer.

    Rows owned by **this** pid are spared: an app built twice in one process
    (tests, an embedded server) would otherwise mark its own live runs dead.
    Returns how many rows were closed.
    """
    mine = os.getpid()
    with SessionLocal() as session:
        rows = [
            r for r in session.exec(
                select(AgentRun).where(AgentRun.status == "running")).all()
            if r.pid != mine
        ]
        for row in rows:
            row.status = "exited"
            row.detail = detail
        session.commit()
        count = len(rows)
    if count:
        log.write("sdk_runs_reaped", count=count)
    return count


def set_cli_session(trace_id: str, cli_session_id: str) -> None:
    """Record the child `claude` session this run is also traced as.

    Written the first time the child names itself, which is what lets the
    serve-time reader union the run's SDK-stream spans with the trace the
    child's own hooks write. Idempotent: the runner calls it for every message
    it sees, so a value that hasn't changed must not churn `updated_at`.

    A *different* id on an already-linked row is refused rather than written.
    Resuming keeps the child's session id (`fork_session=False`), so a second
    id means either the CLI forked anyway or two runs claimed one row —
    overwriting would silently re-point the trace group at a session the run's
    earlier half was never part of, and the spans it leaves behind become
    unreachable from either id.
    """
    if not trace_id or not cli_session_id or trace_id == cli_session_id:
        return
    try:
        with SessionLocal() as session:
            row = session.exec(
                select(AgentRun).where(AgentRun.trace_id == trace_id)).first()
            if row is None or row.cli_session_id == cli_session_id:
                return
            if row.cli_session_id:
                log.error("sdk_cli_session_conflict", trace_id=trace_id,
                          detail=f"holds {row.cli_session_id}, "
                                 f"refused {cli_session_id}")
                return
            row.cli_session_id = cli_session_id
            session.commit()
    except OperationalError:
        # A DB that predates migration 0012 has no such column. This runs on
        # the first message of the first turn, so raising would propagate out
        # of the turn and kill the run — an unmigrated install could not hold a
        # session at all. The alias is an optimisation for the trace view; the
        # run itself does not need it. Booting the server does not migrate, so
        # this state is reachable, and every reader already defends it.
        log.error("sdk_cli_session_link_unavailable", trace_id=trace_id,
                  detail="agent_runs.cli_session_id missing — run `regin migrate`")
        return
    log.write("sdk_cli_session_linked", trace_id=trace_id,
              cli_session_id=cli_session_id)


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
              SELECT cli_session_id FROM agent_runs
              WHERE cli_session_id IS NOT NULL)
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
              SELECT cli_session_id FROM agent_runs
              WHERE cli_session_id IS NOT NULL)
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
                select(AgentRun.trace_id)
                .where(AgentRun.cli_session_id == session_id)
                .order_by(AgentRun.trace_id)).first()
        return get_run(aliased) if aliased else None
    except OperationalError as exc:
        # Answering "no run" sends the caller down the pass-through path, which
        # mints a second trace against a child that already has one. That is
        # the right trade for a missing column — the alternative is failing
        # every launch — but a transient error must not do it invisibly.
        log.error("sdk_run_lookup_unavailable", session_id=session_id,
                  detail=f"{exc} — if the column is missing, run `regin migrate`")
        return None
