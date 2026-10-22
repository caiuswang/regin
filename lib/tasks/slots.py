"""Who holds the `max_concurrent_runs` slots, named by the task they serve.

The registry knows the live runs by trace id only; the task behind one is the
`agent_task_runs` attempt carrying that trace id. A run with no attempt (a
`/live` session) is still a holder, just with no task to name.
"""

from __future__ import annotations

from sqlmodel import select

from lib.agent_sdk import registry
from lib.orm import SessionLocal
from lib.orm.models.tasks import AgentTask, AgentTaskRun
from lib.settings import settings


def _tasks_by_trace(trace_ids: list[str]) -> dict[str, AgentTask]:
    if not trace_ids:
        return {}
    with SessionLocal() as session:
        rows = session.exec(
            select(AgentTaskRun.trace_id, AgentTask)
            .join(AgentTask, AgentTask.id == AgentTaskRun.task_id)
            .where(AgentTaskRun.trace_id.in_(trace_ids))).all()
    return {trace_id: task for trace_id, task in rows}


def run_slots() -> dict:
    """`RunSlots`: the limit, how many are used, and every holder."""
    holders = registry.slot_holders()
    tasks = _tasks_by_trace([h["trace_id"] for h in holders])
    for holder in holders:
        task = tasks.get(holder["trace_id"])
        holder["task_id"] = task.id if task else None
        holder["task_title"] = task.title if task else None
        holder["task_status"] = task.status if task else None
    return {"limit": settings.agent_sdk.max_concurrent_runs,
            "used": len(holders), "holders": holders}
