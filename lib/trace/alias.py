"""Which `trace_id`s belong to one session.

A regin-launched SDK run is traced twice — once by the runner, from the SDK
message stream (`sdk-<hex>`), and once by the child `claude`'s own hooks, under
the session id the child reports. Neither is redundant: only the SDK stream is
live (it needs no hook to fire, so an assistant's reasoning lands the moment the
model emits it rather than when the next hook re-reads the transcript), and only
the hook trace carries `rule.check`, `instructions.loaded`, `cwd.changed`,
`turn` and `skill.invoke`.

So the store keeps both and the read path unions them, in the same spirit as
`lib/trace/merge.py`: reconcile at serve time, leave the append-only rows alone.
Nothing here writes.

A run drives more than one child over its life — `/clear` retires a
conversation and opens another under the same runner — so the SDK stream is
split per child (`sdk_span_trace_id`) and each child unions with the half it
owns. Without that split the run's first child absorbed every later one.

The canonical id is the **child's** — it is the id the transcript uses, the id
`regin session-id` returns inside the run, and the id every hook-side subsystem
already keys on. The `sdk-<hex>` id stays a valid entry point (an operator may
have `/live` open on it) and resolves to the same group.
"""

from __future__ import annotations

# Marks which of the two writers a row came from, attached at fetch time and
# never persisted. Cross-source dedup can't use the `source` column: the runner
# emits `source="sdk"` as a span *attribute*, so its rows land under the column
# default `'hook'` and are indistinguishable from the child's there.
ORIGIN_KEY = '_alias_origin'

# regin's own name for a run it launched (`agent_sdk.supervisor.launch_run`).
# An id carrying it is the only kind that can have an alias at all, which lets
# a hot reader skip the group lookup for every ordinary session.
SDK_RUN_PREFIX = 'sdk-'

# Joins a run id to the child session whose SDK-stream spans a segment holds.
# `/clear` retires a child and opens another under the same runner, so one run
# drives several conversations over its life. Without a per-child id every one
# of them lands on the run's single `sdk-<hex>` trace, and since the run unions
# with `agent_runs.cli_session_id` — pinned to the FIRST child forever — the
# first conversation absorbs all the later ones while each heir loses its live
# stream entirely.
SEGMENT_SEP = ':'


def sdk_span_trace_id(run_trace_id: str, child_session_id: str) -> str:
    """The trace a run's SDK-stream spans land on while it drives `child`.

    Names the segment only. The caller owns the "is this the run's first child"
    test, because the first child keeps the run's bare id — an unchanged
    single-conversation run must keep exactly the trace it has always had.
    """
    return f"{run_trace_id}{SEGMENT_SEP}{child_session_id}"


def split_segment(trace_id: str) -> tuple[str, str]:
    """`(run_id, child_session_id)` for a segment id, `('', '')` for anything
    else."""
    if not trace_id.startswith(SDK_RUN_PREFIX):
        return '', ''
    run, sep, child = trace_id.partition(SEGMENT_SEP)
    return (run, child) if sep and child else ('', '')


def _known_segment(conn, run_id: str, child: str) -> bool:
    """Whether this run really drove this child. Segment ids arrive off a URL,
    so an unbacked one must read as its own empty session rather than union a
    stranger's spans into it."""
    try:
        return conn.execute("""
            SELECT 1 FROM agent_run_sessions
            WHERE trace_id = ? AND cli_session_id = ? LIMIT 1
        """, (run_id, child)).fetchone() is not None
    except Exception:  # noqa: BLE001 — pre-0016 DB has no such table
        return False


def _resolve_run(conn, trace_id: str) -> tuple[str, str] | None:
    """`(run_id, first_child)` for the run `trace_id` belongs to, or None.

    `agent_runs` is asked first and alone answers for the run's own id and its
    first child, so a pre-0016 DB keeps working and the common SDK reads stay
    one indexed lookup. Only a miss falls through to the alias-group table,
    which is the sole place a LATER child is named — that costs an ordinary
    hook-only session a second query, on a table holding one row per SDK child
    ever launched.
    """
    row = conn.execute("""
        SELECT trace_id, cli_session_id FROM agent_runs
        WHERE (trace_id = ? OR cli_session_id = ?)
          AND cli_session_id IS NOT NULL
        ORDER BY trace_id
        LIMIT 1
    """, (trace_id, trace_id)).fetchone()
    if row is not None:
        return row["trace_id"], row["cli_session_id"]
    try:
        row = conn.execute("""
            SELECT r.trace_id AS run_id, r.cli_session_id AS first_child
            FROM agent_run_sessions s
            JOIN agent_runs r ON r.trace_id = s.trace_id
            WHERE s.cli_session_id = ? AND r.cli_session_id IS NOT NULL
            ORDER BY r.trace_id
            LIMIT 1
        """, (trace_id,)).fetchone()
    except Exception:  # noqa: BLE001 — pre-0016 DB has no such table
        return None
    return (row["run_id"], row["first_child"]) if row is not None else None


def trace_group(conn, trace_id: str) -> list[str]:
    """Every trace id whose spans belong to `trace_id`'s session.

    Canonical id first. A plain session returns `[trace_id]`, so callers can
    use this unconditionally and non-SDK reads keep their exact current
    behaviour.

    A subagent's logical id (`sa-…`, `lib/trace/subagent_trace.py`) is a slice
    of ONE parent trace, so it is returned as-is: widening it into an alias
    group would pull the parent's whole conversation into the child.

    Resolves in every direction because each is a legitimate entry point: the
    run's own id (what `/live` was opened on), a child's (what the hooks
    recorded), or a segment's (`sdk_span_trace_id`).

    A run pairs with ONE child, never with all of them: `/clear` opens a
    conversation a person deliberately separated, and each gets the half of the
    run's stream it actually owns.

    `ORDER BY` because nothing constrains `cli_session_id` to be unique; if two
    runs ever claimed one child, an unordered `LIMIT 1` would resolve the same
    id to a different group between calls, and a trace that changes shape when
    you refresh is worse than one that is consistently wrong.
    """
    # A falsy id still yields a one-member group: callers index `[0]`, and a
    # blank id must read as an empty session (what it did before) rather than
    # raise.
    if not trace_id:
        return [trace_id]
    from lib.trace.subagent_trace import is_subagent_trace_id
    if is_subagent_trace_id(trace_id):
        return [trace_id]
    run_id, child = split_segment(trace_id)
    if run_id:
        return [child, trace_id] if _known_segment(conn, run_id, child) \
            else [trace_id]
    try:
        resolved = _resolve_run(conn, trace_id)
    except Exception:  # noqa: BLE001 — a pre-0012 DB has no such column, and a
        # trace read must degrade to the single-trace view rather than 500.
        return [trace_id]
    if resolved is None:
        return [trace_id]
    run_id, first_child = resolved
    child = first_child if trace_id == run_id else trace_id
    if child == first_child:
        return [child, run_id]
    return [child, sdk_span_trace_id(run_id, child)]


def canonical_trace_id(trace_id: str) -> str:
    """`trace_id` resolved to the id its session is really keyed on.

    The child's id for a regin-launched run, `trace_id` itself for anything
    else (and for a run whose child has not named itself yet). Opens its own
    connection so a caller outside the SQLModel readers — the live transcript
    rescan, say — can ask without threading one through.

    This is what a transcript lookup has to ask first: the file on disk is
    named for the CLI session, so a `sdk-…` id finds nothing and the whole
    rescan silently no-ops for a run an operator is watching.
    """
    from lib.orm.engine import get_connection

    if not trace_id:
        return trace_id
    conn = None
    try:
        conn = get_connection()
        return trace_group(conn, trace_id)[0]
    except Exception:  # noqa: BLE001 — a rescan must degrade, never raise
        return trace_id
    finally:
        if conn is not None:
            conn.close()


def strip_origin(spans: list[dict]) -> list[dict]:
    """Drop the internal writer marker before the spans leave the read path.

    `ORIGIN_KEY` exists only so `merge.py` can reconcile a mixed window; it is
    not part of the span contract and must not reach the client, where it would
    appear on every session's payload including unaliased ones.
    """
    return [{k: v for k, v in s.items() if k != ORIGIN_KEY} for s in spans]


def aliased_run_ids(session) -> list[str]:
    """The `sdk-*` ids that are the non-canonical half of a merged session.

    Small (one row per launched run that reported a child, plus one per child
    a `/clear` opened after the first) and used to hide those rows from the
    session list. Returns `[]` on a DB that predates
    migration 0012: booting the server does not migrate, so that state is
    reachable, and a list that 500s there is worse than one that shows the
    duplicate.

    Only ids whose child actually HAS a session row qualify. The runner records
    the alias off the SDK message stream whether or not regin's hooks are wired
    for that cwd, so a child that wrote no trace leaves a link pointing at
    nothing — hiding the run against it would remove its only visible row and
    drop the run out of the session list entirely, reachable afterwards only by
    someone who already knew the `sdk-<hex>` id.
    """
    from sqlalchemy.exc import OperationalError
    from sqlmodel import select

    from lib.orm.models.agent_runs import AgentRun, AgentRunSession
    from lib.orm.models.trace import Session as SessionModel
    try:
        ids = list(session.exec(
            select(AgentRun.trace_id)
            .where(AgentRun.cli_session_id.is_not(None))
            .where(select(SessionModel.trace_id)
                   .where(SessionModel.trace_id == AgentRun.cli_session_id)
                   .exists())).all())
    except OperationalError:
        return []
    try:
        heirs = session.exec(
            select(AgentRunSession.trace_id, AgentRunSession.cli_session_id)
            .join(AgentRun, AgentRun.trace_id == AgentRunSession.trace_id)
            .where(AgentRunSession.cli_session_id != AgentRun.cli_session_id)
            .where(select(SessionModel.trace_id)
                   .where(SessionModel.trace_id == AgentRunSession.cli_session_id)
                   .exists())).all()
    except OperationalError:
        return ids
    ids.extend(sdk_span_trace_id(run, child) for run, child in heirs)
    return ids


def sql_in(trace_ids) -> tuple[str, list[str]]:
    """`('(?,?)', params)` for an `IN` over a trace group.

    Every windowed reader builds the same clause; centralising it keeps a new
    call site from silently going back to a single-trace `= ?`. A bare string
    is accepted as a one-member group so callers that legitimately hold a
    single id don't have to wrap it.
    """
    ids = [trace_ids] if isinstance(trace_ids, str) else list(trace_ids)
    return "(" + ",".join("?" for _ in ids) + ")", ids
