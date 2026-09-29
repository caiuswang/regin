"""The agent's own report on an attempt: posting it once as a comment
thread, and reopening an attempt the session settled before the agent spoke.
"""

from __future__ import annotations

from sqlalchemy import update
from sqlmodel import select

from lib.activity_log import get_activity_logger
from lib.comments import store as comments
from lib.orm import SessionLocal
from lib.orm.models.comments import (
    AUTHOR_AGENT, COMMENT_BODY_MAX, RESOLUTION_RESOLVED, SUBJECT_TASK)
from lib.orm.models.tasks import (
    AgentTask, AgentTaskRun, RUN_STATUS_DONE, RUN_STATUS_RUNNING,
    TASK_STATUS_FAILED, TASK_STATUS_RUNNING,
)
from lib.pipeline import live
from lib.tasks import store

log = get_activity_logger("tasks")

#: Who wrote an attempt's terminal state. `SETTLED_BY_SESSION` is an inference
#: — the session ended, therefore the work did — and it is wrong for exactly
#: one shape: a session aborted mid-run and later resumed by hand.
#: `SETTLED_BY_AGENT` is the agent's own report and outranks it.
SETTLED_BY_AGENT, SETTLED_BY_SESSION = "agent", "session"


def _reopen_refusal(session, run_id: int) -> str:
    """Why this settled attempt may not be reopened, or `""` when it may.

    Named rather than inlined because each clause is a different thing being
    protected: the agent's own word is final, a task a human has moved on from
    is not this run's to re-settle, and a superseded attempt speaks for a run
    nobody is watching.
    """
    row = session.get(AgentTaskRun, run_id)
    if row is None or row.ended_at is None:
        return "not settled"
    if row.settled_by == SETTLED_BY_AGENT:
        return "already reported by the agent"
    task = session.get(AgentTask, row.task_id)
    if task is None or task.status != TASK_STATUS_FAILED:
        return "the task is no longer holding this attempt's failure"
    latest = session.exec(
        select(AgentTaskRun.id)
        .where(AgentTaskRun.task_id == row.task_id)
        .order_by(AgentTaskRun.attempt.desc())).first()
    if latest != run_id:
        return "a later attempt supersedes it"
    return ""


def reopen_for_agent_report(run_id: int) -> bool:
    """Undo an *inferred* settle so the agent's own report can decide it.

    The SDK callback settling a run says only that the session ended. For a
    session aborted mid-work and later resumed by hand that inference is wrong,
    and `ended_at` made it permanent: the resumed agent's `regin task finish`
    hit the idempotence guard and was dropped, leaving the task `failed` over
    finished work. So an agent report reaching a session-settled attempt
    reopens it and then travels the ordinary settle path.

    The attempt is cleared back to open — `ended_at`, `exit_reason` and
    `verify_exit_code` all — so a `verify_cmd` is re-claimed and re-run rather
    than inherited from the aborted attempt: the report is a claim about the
    work, and the gate is what grades it.

    The `UPDATE` carries its own guard so two concurrent reports cannot both
    reopen; the loser falls through to `_claim_verify` and `ended_at`, which
    already arbitrate a live attempt.
    """
    with SessionLocal() as session:
        refusal = _reopen_refusal(session, run_id)
        if refusal:
            return False
        task_id = session.get(AgentTaskRun, run_id).task_id
        reopened = session.execute(
            update(AgentTaskRun)
            .where(AgentTaskRun.id == run_id,
                   AgentTaskRun.ended_at.is_not(None),
                   AgentTaskRun.settled_by.is_distinct_from(SETTLED_BY_AGENT))
            .values(status=RUN_STATUS_RUNNING, ended_at=None, exit_reason=None,
                    verify_exit_code=None, settled_by=None)).rowcount
        if reopened:
            live.mark(session, "task", task_id)
        session.commit()
    if not reopened:
        return False
    store.set_status(task_id, TASK_STATUS_RUNNING)
    log.write("task_run_reopened", run_id=run_id, task_id=task_id)
    return True


# Stamped into the report's first line so a second `task finish` for the same
# attempt recognises its own earlier report instead of opening a duplicate
# thread. The identity is the attempt, which is stable; nothing here is timed.
_REPORT_MARKER = "[attempt {run_id} · {status}]"


def _already_reported(task_id: int, run_id: int) -> bool:
    marker = _REPORT_MARKER.split(" ·")[0].format(run_id=run_id)
    return any(
        (comment["body"] or "").startswith(marker)
        for thread in comments.thread_with_comments(SUBJECT_TASK, str(task_id))
        for comment in thread["comments"])


def _post_agent_report(task_id: int, run_id: int, status: str,
                       reason: str) -> None:
    """The run's own account of what it did, filed where a human reads it.

    `exit_reason` already holds this text, but on the *attempt* — a line in the
    run history that the next attempt pushes out of view. A report is about the
    task, so it belongs in the task's conversation, beside whatever a person
    said there.

    The thread's resolution mirrors the outcome, and that is what carries a
    failure forward: `done` is a statement and is settled on arrival, while
    `failed` stays open, so `_followup_block` delivers it to the next attempt
    unchanged. The next agent reads why the last one could not finish without
    regin growing a second delivery path to tell it.
    """
    if _already_reported(task_id, run_id):
        return
    thread = comments.open_thread(SUBJECT_TASK, str(task_id))
    body = f"{_REPORT_MARKER.format(run_id=run_id, status=status)} {reason}"
    comments.add_comment(thread["id"], body=body[:COMMENT_BODY_MAX],
                         author_kind=AUTHOR_AGENT)
    if status == RUN_STATUS_DONE:
        comments.resolve(thread["id"], RESOLUTION_RESOLVED)
    log.write("task_run_reported", task_id=task_id, run_id=run_id,
              status=status, thread_id=thread["id"])
