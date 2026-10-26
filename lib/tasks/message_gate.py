"""The gate between operator messages and a task run's `task finish`.

A message sent to a task run's session has exactly three endings: handed to
the model before the report is accepted, refused at send with the draft kept,
or dropped with a `dropped:` record. Two moves keep a fourth — the report
accepted while the message was still on its way — from existing:

  * the report is refused while `pending_operator_messages` is non-empty;
  * a `one_shot` report seals the attempt in the same step that found the
    queue empty, so a send that loses the race is refused instead of queued
    behind a session that is about to close.

Seal and check are one atomic step per runner, never a clock:

  * SDK — `AgentRunner.seal_if_idle` / `admit` share the runner's queue lock;
  * ACP — `AcpRunHandle.seal_if_idle` / `enqueue` share the handle's lock;
  * tmux — the session is not this process's, so both sides are SQLite write
    transactions (`BEGIN IMMEDIATE`): the seal computes pending and writes
    `end_requested`, the send inserts its row and reads `end_requested`.

Sealed is `params.end_requested` on the attempt — the field `run_ending`
already reads to end the session after the reporting turn.
"""

from __future__ import annotations

import json
from typing import Callable, NamedTuple, Optional

from lib.activity_log import get_activity_logger
from lib.agent_bridge import store as bridge_store
from lib.agent_bridge.pending import PREVIEW_CHARS, pending_operator_messages
from lib.agent_sdk.registry import SEALED_DETAIL
from lib.orm.engine import get_connection
from lib.tasks import store, tmux_runs
from lib.tasks.run_ending import END_REQUESTED

log = get_activity_logger("tasks")

_ATTEMPT_SQL = """
SELECT id, task_id, params_json FROM agent_task_runs
WHERE trace_id = ? ORDER BY attempt DESC LIMIT 1
"""


class Gate(NamedTuple):
    """What the finish guard found: messages still owed to the model, and how
    to take the seal back if the report fails after it."""

    pending: list[dict]
    undo: Callable[[], None]


def _noop() -> None:
    return None


def check(run: dict) -> Gate:
    """Pending messages for `run` (`{id, trace_id, params}`), sealing it when
    it is `one_shot` and nothing is pending."""
    trace_id = run.get("trace_id")
    params = run.get("params") or {}
    if not trace_id:
        return Gate([], _noop)
    if not params.get("one_shot"):
        return Gate(pending_operator_messages(trace_id), _noop)
    if tmux_runs.is_tmux(params):
        return _seal_tmux(run["id"], trace_id)
    return _seal_runner(trace_id)


def refusal_text(pending: list[dict]) -> str:
    lines = "\n".join(f"  - {p['preview']}" for p in pending)
    return (f"有 {len(pending)} 条 operator 消息尚未送达。请结束本轮，先不要 "
            "finish：它们会作为下一轮到达。处理完再 finish。\n" + lines)


def _seal_runner(trace_id: str) -> Gate:
    from lib.agent_sdk import registry  # noqa: PLC0415

    # A repeated finish on a run an accepted report already sealed must not
    # be able to unseal it when its own settle fails.
    already = registry.is_sealed(trace_id)
    queued = registry.seal_if_idle(trace_id)
    if not queued:
        return Gate([], _noop if already
                    else (lambda: registry.unseal(trace_id)))
    source = ("acp_queue" if registry._acp_handle(trace_id) is not None
              else "sdk_queue")
    return Gate([{"source": source, "id": q["id"],
                  "preview": q["text"][:PREVIEW_CHARS]} for q in queued],
                _noop)


def _seal_tmux(run_id: int, trace_id: str) -> Gate:
    conn = get_connection()
    try:
        conn.execute("BEGIN IMMEDIATE")
        pending = pending_operator_messages(trace_id, conn=conn)
        already = False
        if not pending:
            already = _write_end_requested(conn, run_id, True)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    if pending:
        return Gate(pending, _noop)
    if already:
        return Gate([], _noop)
    log.write("task_tmux_run_sealed", run_id=run_id, trace_id=trace_id)
    return Gate([], lambda: _unseal_tmux(run_id))


def _unseal_tmux(run_id: int) -> None:
    conn = get_connection()
    try:
        _write_end_requested(conn, run_id, False)
        conn.commit()
    finally:
        conn.close()
    log.write("task_tmux_run_unsealed", run_id=run_id)


def _write_end_requested(conn, run_id: int, value: bool) -> bool:
    """Set or clear the seal; returns whether it was already set."""
    row = conn.execute("SELECT params_json FROM agent_task_runs WHERE id = ?",
                       (run_id,)).fetchone()
    if row is None:
        return False
    params = store._decode_params(row["params_json"])
    was = bool(params.get(END_REQUESTED))
    if value:
        params[END_REQUESTED] = True
    else:
        params.pop(END_REQUESTED, None)
    conn.execute("UPDATE agent_task_runs SET params_json = ? WHERE id = ?",
                 (json.dumps(params), run_id))
    return was


def release_for_resume(trace_ids) -> list[int]:
    """Lift the seal off the task attempts whose session an operator resumes.

    The seal guards the session the attempt launched, and only while it is
    closing; the resume route refuses a session that is still live, so by here
    that session is over. Left in place, the seal would refuse every send to
    the continuation and a tmux `Stop` would end it after its first turn. The
    run's settled status is untouched.
    """
    ids = sorted({t for t in trace_ids if t})
    if not ids:
        return []
    marks = ",".join("?" for _ in ids)
    conn = get_connection()
    released = []
    try:
        conn.execute("BEGIN IMMEDIATE")
        rows = conn.execute(
            f"SELECT id FROM agent_task_runs WHERE trace_id IN ({marks})",
            ids).fetchall()
        released = [r["id"] for r in rows
                    if _write_end_requested(conn, r["id"], False)]
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    for run_id in released:
        log.write("task_run_seal_released_for_resume", run_id=run_id)
    return released


def _attempt(conn, trace_id: str) -> Optional[dict]:
    row = conn.execute(_ATTEMPT_SQL, (trace_id,)).fetchone()
    if row is None:
        return None
    params = store._decode_params(row["params_json"])
    return {"run_id": row["id"], "task_id": row["task_id"],
            "sealed": bool(params.get(END_REQUESTED))}


def _sealed_attempt(conn, trace_id: str) -> Optional[dict]:
    attempt = _attempt(conn, trace_id)
    return attempt if attempt and attempt["sealed"] else None


def task_attempt(trace_id: str) -> Optional[dict]:
    """`{run_id, task_id, sealed}` for the task run `trace_id` belongs to."""
    if not trace_id:
        return None
    conn = get_connection()
    try:
        return _attempt(conn, trace_id)
    finally:
        conn.close()


def record_tmux_send(trace_id: str, body: str,
                     sender: Optional[str]) -> tuple[int, Optional[dict]]:
    """Record a pane-bound steer, refusing it when its run is sealed.

    Insert-then-read inside one write transaction: either this row exists
    before a concurrent seal computes pending (and the seal refuses), or the
    seal's `end_requested` is visible here (and the send refuses).
    """
    conn = get_connection()
    try:
        conn.execute("BEGIN IMMEDIATE")
        row_id = bridge_store.insert_bridge_message(conn, trace_id, body,
                                                    sender, pending=True)
        sealed = _sealed_attempt(conn, trace_id)
        if sealed is not None:
            bridge_store.refuse_bridge_message(conn, row_id, SEALED_DETAIL)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    log.write("bridge_message_recorded", trace_id=trace_id, row_id=row_id,
              kind="steer", refused=sealed is not None)
    return row_id, sealed


def _resume_inputs(trace_id: str) -> dict:
    """What `POST /api/agent-runs` needs to continue `trace_id`: the CLI
    resolves a resumed id against the directory the session ran in."""
    from lib.orm import SessionLocal  # noqa: PLC0415
    from lib.orm.models.trace import Session as SessionModel  # noqa: PLC0415

    with SessionLocal() as session:
        row = session.get(SessionModel, trace_id) if trace_id else None
        return {"session_id": trace_id,
                "cwd": (row.cwd if row else None) or "",
                "model": (row.model if row else None) or ""}


def refused_send(row_id: int, sealed: dict, trace_id: str = "") -> dict:
    """The `SendRefused` body a sealed run's send answers with. `resume`
    carries what continuing this same conversation takes, beside the task
    comment that hands the text to the next run instead."""
    return {"delivered": False, "reason": "run_sealed",
            "detail": ("This task run has reported finished and its session "
                       "is closing, so the message was not sent. Resume the "
                       "session to keep the same conversation, or post it as "
                       "a task comment for the next run."),
            "task_id": sealed["task_id"], "run_id": sealed["run_id"],
            "id": row_id, "resume": _resume_inputs(trace_id)}
