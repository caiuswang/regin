"""What a session's compaction must keep: its feature, task and finish command.

`regin session context` prints this for the regin-compact mod, which appends
it to `session.compact`'s instructions. A summary that drops the finish
command leaves a task run with no way to report itself done, so the command
is the one `<finish>` block of the launch prompt carries, from the same
function.
"""

from __future__ import annotations

from typing import Optional

from sqlmodel import select

from lib.agent_sdk.store import sdk_trace_for_cli_session
from lib.features.attribution import canonical_trace, features_for_session
from lib.orm import SessionLocal
from lib.orm.models.features import Feature
from lib.orm.models.tasks import AgentTask, AgentTaskRun
from lib.tasks.run_prompt import _finish_command

HEADER = "Keep verbatim in the summary:"


def _task_run(trace_ids: set[str]) -> Optional[tuple[AgentTaskRun, AgentTask]]:
    with SessionLocal() as session:
        run = session.exec(
            select(AgentTaskRun)
            .where(AgentTaskRun.trace_id.in_(trace_ids))
            .order_by(AgentTaskRun.id.desc())).first()
        task = session.get(AgentTask, run.task_id) if run else None
        return (run, task) if task else None


def _feature(trace_id: str, task: Optional[AgentTask]) -> Optional[Feature]:
    declared = [r for r in features_for_session(trace_id) if r.is_primary]
    feature_id = (declared[0].feature_id if declared
                  else task.feature_id if task else None)
    if feature_id is None:
        return None
    with SessionLocal() as session:
        return session.get(Feature, feature_id)


def session_context(trace_id: str) -> str:
    """The block to keep, or '' when the session has neither a feature nor a
    task run."""
    # A launched run records regin's own run trace; the mod knows only the
    # Claude Code session, which the SDK alias maps back onto it.
    traces = {trace_id, canonical_trace(trace_id),
              sdk_trace_for_cli_session(trace_id)} - {None}
    found = _task_run(traces)
    run, task = found if found else (None, None)
    feature = _feature(trace_id, task)
    lines = []
    if feature is not None:
        lines.append(f"- This session works on feature F{feature.id}: "
                     f"{feature.title}")
    if task is not None:
        lines.append(f"- It is task {task.id} ({task.title}), run {run.id}. "
                     "Report it finished with:")
        lines.append(f"  {_finish_command(task.id, run.id)} "
                     "--reason '<what you did, and why>'")
    return "\n".join([HEADER, *lines]) if lines else ""
