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


def trace_group(conn, trace_id: str) -> list[str]:
    """Every trace id whose spans belong to `trace_id`'s session.

    Canonical id first. A plain session returns `[trace_id]`, so callers can
    use this unconditionally and non-SDK reads keep their exact current
    behaviour.

    Resolves in both directions because either id is a legitimate entry point:
    the run's own id (what `/live` was opened on) or the child's (what the
    hooks recorded).

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
    try:
        row = conn.execute("""
            SELECT trace_id, cli_session_id FROM agent_runs
            WHERE (trace_id = ? OR cli_session_id = ?)
              AND cli_session_id IS NOT NULL
            ORDER BY trace_id
            LIMIT 1
        """, (trace_id, trace_id)).fetchone()
    except Exception:  # noqa: BLE001 — a pre-0012 DB has no such column, and a
        # trace read must degrade to the single-trace view rather than 500.
        return [trace_id]
    if row is None:
        return [trace_id]
    return [row["cli_session_id"], row["trace_id"]]


def strip_origin(spans: list[dict]) -> list[dict]:
    """Drop the internal writer marker before the spans leave the read path.

    `ORIGIN_KEY` exists only so `merge.py` can reconcile a mixed window; it is
    not part of the span contract and must not reach the client, where it would
    appear on every session's payload including unaliased ones.
    """
    return [{k: v for k, v in s.items() if k != ORIGIN_KEY} for s in spans]


def aliased_run_ids(session) -> list[str]:
    """The `sdk-*` ids that are the non-canonical half of a merged session.

    Small (one row per launched run that reported a child) and used to hide
    those rows from the session list. Returns `[]` on a DB that predates
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

    from lib.orm.models.agent_runs import AgentRun
    from lib.orm.models.trace import Session as SessionModel
    try:
        return list(session.exec(
            select(AgentRun.trace_id)
            .where(AgentRun.cli_session_id.is_not(None))
            .where(select(SessionModel.trace_id)
                   .where(SessionModel.trace_id == AgentRun.cli_session_id)
                   .exists())).all())
    except OperationalError:
        return []


def sql_in(trace_ids) -> tuple[str, list[str]]:
    """`('(?,?)', params)` for an `IN` over a trace group.

    Every windowed reader builds the same clause; centralising it keeps a new
    call site from silently going back to a single-trace `= ?`. A bare string
    is accepted as a one-member group so callers that legitimately hold a
    single id don't have to wrap it.
    """
    ids = [trace_ids] if isinstance(trace_ids, str) else list(trace_ids)
    return "(" + ",".join("?" for _ in ids) + ")", ids
