"""Durable record of regin-launched runs (`agent_runs`)."""

from __future__ import annotations

import os

from sqlmodel import select

from lib.activity_log import get_activity_logger
from lib.orm import SessionLocal
from lib.orm.models import AgentRun

log = get_activity_logger("agent_sdk")


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
        }
