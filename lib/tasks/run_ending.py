"""Ending a `one_shot` task run's session once the agent has reported.

A task armed "end after the run" means: the conversation is over when the
agent's own `task finish` report is accepted — not when the first turn returns
a result. A turn routinely ends with work still pending (a background shell it
will be woken for, a question for the operator), and ending the session there
filed an unreported attempt as `done`. The report is the one event that says
the work is over, so it is the trigger, and the session closes at the end of
the turn that made it:

  * SDK — the run is launched steerable and the report closes its queue
    (`supervisor.end_after_turn`), so the pump stops at that turn's result.
  * tmux — the session is not this process's, so the report is stamped on the
    attempt and the pane's own `Stop` hook ends it (`end_reported_tmux`).

An agent that never reports leaves its session open, exactly as a steerable
run does: an open session is the honest state for unreported work.
"""

from __future__ import annotations

import json
from typing import Optional

from sqlmodel import select

from lib.activity_log import get_activity_logger
from lib.orm import SessionLocal
from lib.orm.models.tasks import AgentTaskRun
from lib.tasks import store, tmux_runs

log = get_activity_logger("tasks")

END_REQUESTED = "end_requested"


def request_end(run_id: int) -> bool:
    """Mark a one-shot attempt's session to end after this turn, and end an
    SDK one now that it has. Returns whether the attempt was one-shot."""
    with SessionLocal() as session:
        row = session.get(AgentTaskRun, run_id)
        if row is None:
            return False
        params = store._decode_params(row.params_json)
        if not params.get("one_shot"):
            return False
        if not params.get(END_REQUESTED):
            row.params_json = json.dumps({**params, END_REQUESTED: True})
            session.add(row)
            session.commit()
        trace_id = row.trace_id
    if trace_id and not tmux_runs.is_tmux(params):
        from lib.agent_sdk import supervisor  # noqa: PLC0415

        ended, detail = supervisor.end_after_turn(trace_id)
        log.write("task_run_end_requested", run_id=run_id, launch="sdk",
                  trace_id=trace_id, ended=ended, detail=detail)
    else:
        log.write("task_run_end_requested", run_id=run_id, launch="tmux",
                  trace_id=trace_id)
    return True


def _reported_tmux_attempt(trace_id: str) -> Optional[dict]:
    with SessionLocal() as session:
        rows = session.exec(select(AgentTaskRun)
                            .where(AgentTaskRun.trace_id == trace_id)).all()
        views = [{"id": r.id, "task_id": r.task_id, "attempt": r.attempt,
                  "trace_id": r.trace_id,
                  "params": store._decode_params(r.params_json)}
                 for r in rows]
    for view in views:
        if tmux_runs.is_tmux(view["params"]) and view["params"].get(
                END_REQUESTED):
            return view
    return None


def end_reported_tmux(trace_id: str) -> bool:
    """At a turn's end, close the tmux session of a one-shot attempt whose
    agent has reported. Called from the pane's own `Stop` hook, so it hangs
    up the caller as well. Returns whether it ended one."""
    if not trace_id:
        return False
    view = _reported_tmux_attempt(trace_id)
    if view is None:
        return False
    log.write("task_tmux_run_ended_after_report", run_id=view["id"],
              task_id=view["task_id"], trace_id=trace_id)
    return tmux_runs.end_own(view)
