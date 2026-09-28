"""The join between a task row and a run — `agent_task_runs`.

`lib/agent_sdk` needs no new engine: `supervisor.launch_run` launches,
`registry.stop_run` stops, `registry.interrupt_run` interrupts. Everything here
is bookkeeping around those calls, and every control path delegates rather than
reimplementing — there is no second stop, no second liveness probe, no second
queue.

Three rules govern the bookkeeping, and they are the same three
`lib/orm/models/agent_runs.py` states for its own rows:

  * the terminal write happens on the run's own completion path, in a
    `finally`, and is idempotent — a `running` row outlives the process that
    wrote it and misleads every later reader;
  * a reader merges the row's claim with liveness from `agent_runs` rather than
    trusting `status == 'running'`;
  * a row is never condemned on pid inequality alone — the recorded pid is
    probed (`sdk_store.run_is_live`), or a foreign process reaps a live peer's
    runs.

Firing is event-driven for the same reason: the completion that could change
which dependents are runnable is the thing that recomputes them, so there is no
scheduler and no interval.

Two events can be that completion, and they answer different questions. The run
ending (`handle_completion`) answers "the session is over"; the agent's own
`task finish` signal (`finish_run`) answers "the work is done". Only the second
one arrives while a steerable session is still open, and a session regin did
not launch `one_shot` never ends on its own — so the signal is the path that
carries a dependency chain, and the callback is its fallback. Both settle
through `complete_run`, which settles once, so neither can double-fire.
"""

from __future__ import annotations

import json
import shlex
import sys
from datetime import datetime, timezone
from threading import Thread
from typing import Optional

from sqlalchemy import update
from sqlalchemy.exc import IntegrityError
from sqlmodel import select

from lib.activity_log import get_activity_logger
from lib.agent_sdk import store as sdk_store, supervisor, worktree
from lib.comments import store as comments
from lib.orm import SessionLocal
from lib.orm.models.comments import (
    AUTHOR_AGENT, COMMENT_BODY_MAX, RESOLUTION_OPEN, RESOLUTION_RESOLVED,
    SUBJECT_TASK)
from lib.orm.models.agent_runs import AgentRun
from lib.orm.models.tasks import (
    AgentTask, AgentTaskRun, RUN_STATUS_ARMED, RUN_STATUS_DONE,
    RUN_STATUS_FAILED, RUN_STATUS_RUNNING, RUN_STATUS_STARTING,
    TASK_STATUS_ARMED, TASK_STATUS_DONE, TASK_STATUS_FAILED,
    TASK_KIND_SPEC, TASK_STATUS_QUEUED,
    TASK_STATUS_RUNNING,
)
from lib.settings import settings
from lib.tasks import deps, images, store, verify
from lib.tasks.integrations import registry

log = get_activity_logger("tasks")

# A card grouping key, not a navigable session: a `task.ready` event belongs to
# no trace, so it is filed under a sentinel registered in
# `events.NON_SESSION_TRACE_IDS` and its card renders no session link.
TASK_READY_TRACE = "agent-tasks"

PROMPT_MAX = 8000
# A comment is already capped at `COMMENT_BODY_MAX`, but the number of threads
# is not, so an unresolved conversation could otherwise crowd out the task.
FOLLOWUP_MAX = 8000
_REASON_MAX = 2000

# The run's own vocabulary for how it ended (`RunOutcome.status`, read back
# from the durable `agent_runs` row) mapped onto the task's.
_TASK_STATUS_FOR = {RUN_STATUS_DONE: TASK_STATUS_DONE,
                    RUN_STATUS_FAILED: TASK_STATUS_FAILED}

_SETTLED = (TASK_STATUS_DONE, TASK_STATUS_FAILED)

#: Who wrote an attempt's terminal state. `SETTLED_BY_SESSION` is an inference
#: — the session ended, therefore the work did — and it is wrong for exactly
#: one shape: a session aborted mid-run and later resumed by hand.
#: `SETTLED_BY_AGENT` is the agent's own report and outranks it.
SETTLED_BY_AGENT, SETTLED_BY_SESSION = "agent", "session"
_LAUNCH_DEFAULTS: dict = {"cwd": None, "model": "", "effort": "",
                          "permission_mode": "", "one_shot": False, "env": {}}


class RunRefused(RuntimeError):
    """A run this task cannot start right now — a 409, never a crash.

    `blocking` carries the dependency ids at fault so the caller can name them
    instead of making the operator go find them.
    """

    def __init__(self, message: str, blocking=None) -> None:
        super().__init__(message)
        self.blocking = list(blocking or [])


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _already_live(task_id: int) -> RunRefused:
    """The one refusal for "this task is already going".

    Built in one place because two paths raise it — the liveness check and the
    unique-constraint loser — and a caller must not be able to tell which one
    answered: they are the same fact observed a few microseconds apart.
    """
    return RunRefused(f"task {task_id} already has a live run")


def serialize_attempt(row: AgentTaskRun) -> dict:
    """One attempt as every surface renders it.

    Public because the task feed carries the pending one per row: the arm
    sheet seeds its draft from what the task was last armed with, and that
    now lives here rather than on the task.
    """
    return _serialize(row)


def _serialize(row: AgentTaskRun) -> dict:
    return {
        "id": row.id,
        "task_id": row.task_id,
        "trace_id": row.trace_id,
        "attempt": row.attempt,
        "status": row.status,
        "started_at": row.started_at,
        "ended_at": row.ended_at,
        "exit_reason": row.exit_reason,
        "verify_exit_code": row.verify_exit_code,
        # The parameters THIS attempt spends. On the attempt rather than on
        # the task, so a rerun under different ones is a second row here and
        # not a second task — which is what retired "a sub-task is a rerun".
        "params": store._decode_params(row.params_json),
        "verify_cmd": row.verify_cmd,
        "autorun": bool(row.autorun),
    }


def arm(task_id: int, *, params: dict, verify_cmd: Optional[str],
        autorun: int) -> dict:
    """Write the parameters the task's next attempt will spend.

    An `armed` run row IS the pending attempt: arming a task used to set three
    columns on it and leave `status='armed'` standing for state nothing else
    could see, and the row makes that state a thing you can read, list and
    replace.

    Re-arming replaces the pending row rather than adding a second — there is
    one next attempt, and two would make "which parameters does Run spend"
    a question with two answers.
    """
    with SessionLocal() as session:
        row = session.exec(
            select(AgentTaskRun)
            .where(AgentTaskRun.task_id == task_id,
                   AgentTaskRun.status == RUN_STATUS_ARMED)).first()
        if row is None:
            row = AgentTaskRun(task_id=task_id,
                               attempt=_next_attempt(session, task_id),
                               status=RUN_STATUS_ARMED)
        row.params_json = json.dumps(params)
        row.verify_cmd = verify_cmd
        row.autorun = autorun
        row.started_at = _now()
        session.add(row)
        session.commit()
        session.refresh(row)
        return _serialize(row)


def armed_attempt(task_id: int) -> Optional[dict]:
    """The pending attempt, or None for a task nobody has armed."""
    with SessionLocal() as session:
        row = session.exec(
            select(AgentTaskRun)
            .where(AgentTaskRun.task_id == task_id,
                   AgentTaskRun.status == RUN_STATUS_ARMED)).first()
        return _serialize(row) if row is not None else None


def open_attempt(task_id: int, *, params: Optional[dict] = None,
                 verify_cmd: Optional[str] = None,
                 autorun: int = 0) -> dict:
    """An attempt row, written *before* the launch: a crash between the two
    leaves this visible `starting` row rather than an untracked child.

    `UNIQUE(task_id, attempt)` closes the window between the liveness check and
    this insert. Two requests that both read "not live" compute the same next
    attempt, so the constraint picks the winner and the loser is refused with
    the liveness check's own words — identity resolving a race, exactly as it
    resolves a redelivered capture, and never a lock or a retry.
    """
    with SessionLocal() as session:
        row = session.exec(
            select(AgentTaskRun)
            .where(AgentTaskRun.task_id == task_id,
                   AgentTaskRun.status == RUN_STATUS_ARMED)).first()
        if row is None:
            # A rerun with no new parameters spends the previous attempt's:
            # "Run again" is the same attempt made twice, and re-arming is how
            # an operator asks for different ones.
            previous = session.exec(
                select(AgentTaskRun).where(AgentTaskRun.task_id == task_id)
                .order_by(AgentTaskRun.attempt.desc())).first()
            row = AgentTaskRun(
                task_id=task_id, attempt=_next_attempt(session, task_id),
                params_json=(json.dumps(params) if params is not None
                             else getattr(previous, "params_json", None)),
                verify_cmd=(verify_cmd if params is not None
                            else getattr(previous, "verify_cmd", None)),
                autorun=(autorun if params is not None
                         else int(getattr(previous, "autorun", 0) or 0)))
        elif params is not None:
            row.params_json = json.dumps(params)
            row.verify_cmd = verify_cmd
            row.autorun = autorun
        row.status = RUN_STATUS_STARTING
        row.started_at = _now()
        session.add(row)
        try:
            session.commit()
        except IntegrityError:
            session.rollback()
            log.warn("task_run_race_lost", task_id=task_id)
            raise _already_live(task_id) from None
        session.refresh(row)
        return _serialize(row)


# ── liveness ─────────────────────────────────────────────────

def _trace_is_live(trace_id: str) -> bool:
    """Whether the run behind `trace_id` is still reachable.

    The registry answers first because it is the strongest evidence there is —
    a runner in this process — and the durable row answers for a run another
    process owns, by probing its pid.
    """
    from lib import agent_sdk

    if agent_sdk.is_sdk_owned(trace_id) or agent_sdk.is_starting(trace_id):
        return True
    return sdk_store.run_is_live(trace_id)


def is_live(view: dict) -> bool:
    """Whether the agent behind an attempt is still reachable.

    A probe of the run, never a reading of the row. `finish_run` settles an
    attempt while its session deliberately keeps going, so `ended_at` and a
    terminal `status` stopped implying the agent is gone — and asking the row
    would make the run history report `live: false` for the very run a second
    launch is refused for.

    An attempt with no `trace_id` is not live: it was written before the launch
    that would have minted one, so there is no process to probe — which is
    exactly the visible `starting` row a crash between the two is meant to
    leave, rather than a task blocked forever by a run that never existed.
    """
    return bool(view["trace_id"]) and _trace_is_live(view["trace_id"])


# ── reads ────────────────────────────────────────────────────

def latest_attempt(task_id: int) -> Optional[dict]:
    """The newest attempt that has actually been launched.

    The pending `armed` row is excluded, and that exclusion is load-bearing:
    `live_run` reads this, so an armed row answering here would report "not
    live" for a task whose previous run is still going and let a second launch
    into the same worktree. `armed_attempt` is how the pending one is read.
    """
    with SessionLocal() as session:
        row = session.exec(
            select(AgentTaskRun)
            .where(AgentTaskRun.task_id == task_id,
                   AgentTaskRun.status != RUN_STATUS_ARMED)
            .order_by(AgentTaskRun.attempt.desc())).first()
        return _serialize(row) if row is not None else None


def live_run(task_id: int) -> Optional[dict]:
    """The task's attempt whose agent is still reachable, or None. Never
    `status` alone.

    The LATEST attempt, settled or not. `finish_run` settles an attempt while
    deliberately leaving its session running, so `ended_at` stopped implying
    the agent is gone — and skipping settled rows would admit a second launch
    into the same worktree while the first agent is still working, with only
    the newer one reachable by Stop.

    The tail of that trade: a task run is launched in THIS process, and
    `sdk_store.run_is_live` spares its own pid without probing it. A runner
    killed hard enough to skip its teardown therefore leaves a row that answers
    "live" until the server restarts, and the task cannot be re-run until then.
    Stop clears it, and so does the boot reaper.
    """
    view = latest_attempt(task_id)
    return view if view is not None and is_live(view) else None


def _agent_runs(session, trace_ids: list[str]) -> dict[str, dict]:
    if not trace_ids:
        return {}
    rows = session.exec(
        select(AgentRun).where(AgentRun.trace_id.in_(trace_ids))).all()
    return {r.trace_id: {"status": r.status, "pid": r.pid, "cwd": r.cwd,
                         "model": r.model, "detail": r.detail,
                         "cli_session_id": r.cli_session_id} for r in rows}


def list_runs(task_id: int) -> list[dict]:
    """Attempt history, newest first, joined to `agent_runs`.

    The join is what makes `live` answerable: the attempt row records intent,
    and the `agent_runs` row plus its pid records reachability.

    History, so the pending `armed` row is not in it: nothing has happened to
    that attempt yet. The detail envelope carries it separately.
    """
    with SessionLocal() as session:
        rows = session.exec(
            select(AgentTaskRun)
            .where(AgentTaskRun.task_id == task_id,
                   AgentTaskRun.status != RUN_STATUS_ARMED)
            .order_by(AgentTaskRun.attempt.desc())).all()
        views = [_serialize(row) for row in rows]
        agents = _agent_runs(session, [v["trace_id"] for v in views
                                       if v["trace_id"]])
    for view in views:
        view["agent_run"] = agents.get(view["trace_id"])
        view["live"] = is_live(view)
    log.read("task_runs_read", task_id=task_id, count=len(views))
    return views


# ── launching ────────────────────────────────────────────────

def _finish_command(run_id: int) -> str:
    """The exact shell command the agent runs to report its work finished.

    Built from the server's own interpreter and CLI path rather than a bare
    `regin`, so it works in a worktree, under any cwd, and whether or not the
    agent's PATH has regin on it — the same reason
    `topics.proposal_external._finish_command` builds its own.
    """
    cli = settings.project_root / "cli" / "regin.py"
    parts = [sys.executable, str(cli), "task", "finish", str(run_id)]
    return " ".join(shlex.quote(part) for part in parts)


def _cli_command(*parts: str) -> str:
    """A `regin <…>` command as the prompt must write it — the server's own
    interpreter and CLI path, for the same reason `_finish_command` does it:
    the agent's cwd is a worktree and its PATH may not carry regin at all."""
    cli = settings.project_root / "cli" / "regin.py"
    argv = [sys.executable, str(cli), *parts]
    return " ".join(shlex.quote(part) for part in argv)


def _open_fix_defects(task_id: int) -> list[int]:
    """Open defects that name this task as the work that closes them."""
    from lib.defects import store as defect_store  # noqa: PLC0415
    from lib.orm.models.defects import STATUS_OPEN  # noqa: PLC0415

    return sorted(row.id for row in defect_store.by_fix_item(task_id)
                  if row.status == STATUS_OPEN)


def _close_defect_command(defect_id: int) -> str:
    return (_cli_command("defects", "close", str(defect_id), "--check")
            + " '<the check that would have caught it>'")


def _defects_named(ids: list[int]) -> str:
    refs = ", ".join(f"#{i}" for i in ids)
    return f"defect {refs}" if len(ids) == 1 else f"defects {refs}"


def _defects_order(task_id: Optional[int]) -> str:
    """The close step a fix task's run must take before it finishes.

    Closing is the agent's act, not a side effect of the finish: `close`
    demands the check that would have caught the bug, which no status
    transition can supply. So the run is handed the exact command, and
    `finish_run` refuses a done report while the defect is still open.
    """
    from lib.prompts import render_surface  # noqa: PLC0415
    from lib.prompts.surfaces.tasks import (  # noqa: PLC0415
        FINISH_ORDER_DEFECTS_SURFACE_ID)

    ids = _open_fix_defects(task_id) if task_id is not None else []
    if not ids:
        return ""
    return "\n" + render_surface(FINISH_ORDER_DEFECTS_SURFACE_ID, {
        "defects": _defects_named(ids),
        "close_cmds": "\n".join(f"   {_close_defect_command(i)}"
                                for i in ids)})


def _finish_block(run_id: int, linked: bool = False,
                  base: str = "", task_id: Optional[int] = None) -> str:
    """The `<finish>` instruction appended to a run's launch prompt.

    The command is written out literally, not left to an env var, so a session
    resumed later — with none of the launch's environment — can still report.

    A linked run is additionally told to update its issue *first*. The signal
    is what makes regin re-read the tracker, so an agent that reports before
    it records leaves regin holding the state from before its own work. An
    unlinked run is told nothing about a tracker: there is no issue to update,
    and the instruction would be noise it has to resolve.

    A native run is told to merge its slice back first instead. Its work only
    reaches the rest of the feature through that merge, so a run reporting
    finished on an unmerged branch releases its dependents onto a base that
    does not carry it. The base branch is named when the run was armed with
    one; without it the instruction still stands, unnamed.
    """
    from lib.prompts import render_surface  # noqa: PLC0415
    from lib.prompts.surfaces.tasks import (  # noqa: PLC0415
        FINISH_BASE_UNNAMED_SURFACE_ID, FINISH_ORDER_LINKED_SURFACE_ID,
        FINISH_ORDER_NATIVE_SURFACE_ID, FINISH_SURFACE_ID)

    if linked:
        order = render_surface(FINISH_ORDER_LINKED_SURFACE_ID, {})
    else:
        target = (f"`{base}`" if base
                  else render_surface(FINISH_BASE_UNNAMED_SURFACE_ID, {}))
        order = render_surface(FINISH_ORDER_NATIVE_SURFACE_ID,
                               {"target": target})
    order += _defects_order(task_id)
    return "\n\n" + render_surface(
        FINISH_SURFACE_ID,
        {"order": order, "finish_cmd": _finish_command(run_id)})


def _tracker_block(feature) -> str:
    """The `<tracker>` instruction for a run of a remotely-tracked feature.

    Identity, not content: the issue's description is deliberately not
    repeated, because the feature already carries it as its spec and a second
    copy is either duplication or a stale contradiction of one. The agent that
    needs the body reads it where it is current.

    It hangs off the FEATURE rather than off the task, which is what the
    redesign moved: a link used to live on a task, so which issue a run
    belonged to depended on which of a feature's tasks launched it.

    Empty — not an empty block — for a native feature, so its run's prompt is
    exactly what it was.
    """
    if feature is None or not feature.external_ref:
        return ""
    from lib.prompts import render_surface  # noqa: PLC0415
    from lib.prompts.surfaces.tasks import TRACKER_SURFACE_ID  # noqa: PLC0415

    return "\n\n" + render_surface(TRACKER_SURFACE_ID, {
        "tracker_name": registry.display_name(feature.origin),
        "external_ref": feature.external_ref,
        "external_url": feature.external_url or ""})


def _followup_rows(task_id: int) -> list[tuple[int, dict]]:
    """Every comment on this task's unresolved threads, oldest first.

    Only this task's own threads. A parent's comments are addressed to the
    feature, and a child's run is one concrete piece of work — threading the
    feature's whole discussion through every round is how a prompt rots.

    Chronological across threads and within them, because an appended
    instruction corrects the ones before it and no other order preserves that.
    """
    threads = [row for row
               in comments.thread_with_comments(SUBJECT_TASK, str(task_id))
               if row["resolution_state"] == RESOLUTION_OPEN]
    threads.sort(key=lambda row: (row["created_at"] or "", row["id"]))
    return [(thread["id"], comment)
            for thread in threads
            for comment in sorted(thread["comments"],
                                  key=lambda row: (row["created_at"] or "",
                                                   row["id"]))]


def _followup_line(thread_id: int, comment: dict, previous) -> str:
    """One comment, labelled by who wrote it, with a blank line where one
    conversation ends and the next begins.

    The thread id is on every line, not just the first of a run: it is the
    handle the reply command takes, and a run whose oldest comments were
    dropped to fit could otherwise be shown a continuation line with no id
    anywhere above it.
    """
    gap = "\n" if previous not in (None, thread_id) else ""
    return (f"{gap}  [thread {thread_id} · {comment['author_kind']}] "
            f"{comment['body'].strip()}\n")


def _followup_lines(rows: list[tuple[int, dict]]) -> tuple[list[str], int]:
    """The rendered comments that fit in `FOLLOWUP_MAX`, and how many were
    dropped to make them fit.

    The oldest go first — a later comment corrects an earlier one, so losing
    the correction is the one loss that changes the instruction. The newest is
    kept unconditionally even when it alone overruns: a bound that can deliver
    nothing is worse than one that overshoots by one comment.
    """
    lines: list[str] = []
    previous = None
    for thread_id, comment in rows:
        lines.append(_followup_line(thread_id, comment, previous))
        previous = thread_id
    dropped = 0
    while len(lines) > 1 and sum(len(line) for line in lines) > FOLLOWUP_MAX:
        lines.pop(0)
        dropped += 1
    return lines, dropped


def _followup_block(task_id: int) -> str:
    """The `<followup>` instruction: what was said on the task after it ran.

    This is the only thing `resolution_state` has ever done. A thread stays
    here on every run until somebody resolves it — delivery does not consume
    it, because an instruction the agent has not acted on yet is not an
    instruction it has dealt with, and regin cannot tell the two apart.

    Empty — not an empty block — when nothing is open, so a run of a task
    nobody commented on carries the prompt it always did.
    """
    lines, dropped = _followup_lines(_followup_rows(task_id))
    if not lines:
        return ""
    from lib.prompts import render_surface  # noqa: PLC0415
    from lib.prompts.surfaces.tasks import (  # noqa: PLC0415
        FOLLOWUP_OMITTED_SURFACE_ID, FOLLOWUP_SURFACE_ID)

    omitted = "\n" + render_surface(
        FOLLOWUP_OMITTED_SURFACE_ID,
        {"dropped": dropped, "plural": "" if dropped == 1 else "s"}
    ) + "\n" if dropped else ""
    return "\n\n" + render_surface(FOLLOWUP_SURFACE_ID, {
        "comments": "".join(lines).lstrip("\n"),
        "omitted": omitted,
        "comment_cmd": _cli_command("task", "comment", "<thread id>",
                                    "--body", "<what you did about it>",
                                    "--resolve")})


def _feature_block(task: dict) -> str:
    """The `<feature>` instruction: which feature this run's session counts
    under.

    The attribution itself needs no act from the agent — the lane reads it off
    the run row through the task. What only the agent can do is blame a defect,
    so the block is mostly the command for that, with the id filled in.
    """
    feature_id = task.get("feature_id")
    if feature_id is None:
        return ""
    from lib.features import store as feature_store  # noqa: PLC0415
    from lib.prompts import render_surface  # noqa: PLC0415
    from lib.prompts.surfaces.tasks import FEATURE_SURFACE_ID  # noqa: PLC0415

    feature = feature_store.get(feature_id)
    named = f"#{feature_id}" + (f" ({feature.title})" if feature else "")
    return "\n\n" + render_surface(FEATURE_SURFACE_ID, {
        "feature_named": named, "feature_id": feature_id,
        "spec_cmd": _cli_command("feature", "spec", str(feature_id),
                                 "--file", "-")})


def _spec_block(task: dict) -> str:
    """The `<spec>` instruction, for a feature's spec step only.

    Keyed on the declared `kind`, never on the title: "write the spec and do
    not build" used to be typed into titles by hand.
    """
    feature_id = task.get("feature_id")
    if task.get("kind") != TASK_KIND_SPEC or feature_id is None:
        return ""
    from lib.features import store as feature_store  # noqa: PLC0415
    from lib.prompts import render_surface  # noqa: PLC0415
    from lib.prompts.surfaces.tasks import SPEC_SURFACE_ID  # noqa: PLC0415

    feature = feature_store.get(feature_id)
    named = f"#{feature_id}" + (f" ({feature.title})" if feature else "")
    return "\n\n" + render_surface(SPEC_SURFACE_ID, {
        "feature_named": named,
        "spec_cmd": _cli_command("feature", "spec", str(feature_id),
                                 "--file", "-")})


def _prompt(task: dict, run_id: int, params: Optional[dict] = None) -> str:
    """The sentence a run is launched with, plus how to report it finished.

    A task's images are NOT named here. They travel beside this text as content
    blocks (`supervisor.launch_run(images=…)`), so the prompt stays the idea as
    it was captured — and an attempt re-reads whatever the task carries *now*,
    which is what makes editing an image after the first failed run mean
    something.

    The title is a shelf label written for the board, not an instruction, so it
    is left out whenever a body exists; only a bodyless task falls back to it
    for want of anything else to say.

    An armed skill goes in front as a slash-command line (`_invocation`), so
    the run opens by following that procedure with the idea as its subject.

    Every block after the body is appended *after* the `PROMPT_MAX` cut, never
    inside it: truncation is a property of the captured idea, and a body long
    enough to reach the limit would otherwise silently eat the instructions —
    including the one that lets the task ever complete.

    `<followup>` sits after `<tracker>` so a linked task reads the external
    issue as the source of truth first, and the local unresolved comments as
    what was additionally said here.

    None of the wording is here: every block is a prompt surface under the
    `task-run` area (`lib/prompts/surfaces/tasks.py`), so an operator changes
    what a run is told without changing this file. The order is the envelope's
    to decide, which is why even the assembly renders through a template.
    """
    body = (task.get("body") or "").strip()
    text = body or task["title"]
    from lib.features import store as feature_store  # noqa: PLC0415
    feature = (feature_store.get(task["feature_id"])
               if task.get("feature_id") is not None else None)
    remote = bool(feature is not None and feature.external_ref)
    from lib.prompts import render_surface  # noqa: PLC0415
    from lib.prompts.surfaces.tasks import LAUNCH_SURFACE_ID  # noqa: PLC0415

    finish = _finish_block(run_id, linked=remote, base=_armed_base(params),
                           task_id=task["id"])
    prompt = render_surface(LAUNCH_SURFACE_ID, {
        "invocation": _invocation(params or {}),
        "body": text[:PROMPT_MAX],
        "tracker": _tracker_block(feature),
        "followup": _followup_block(task["id"]),
        "feature": _feature_block(task),
        "spec": _spec_block(task),
        "finish": finish})
    # An envelope edited to drop `{{finish}}` renders fine — a *missing*
    # placeholder is not a render error, so `render_surface` never falls back —
    # and produces a task that can never complete. Every other block is the
    # operator's to remove; this one is the control instruction regin needs
    # back, so it is re-appended rather than silently lost.
    return prompt if finish in prompt else prompt + finish


def _armed_base(params: Optional[dict]) -> str:
    """The branch this run's worktree was cut from, as the prompt should name.

    Read only when a branch was armed too: `_launch_kwargs` builds a worktree
    only for an armed branch, so a lone `base` belongs to no tree this run is
    in — naming it would tell the agent to merge into a branch it was never
    based on.
    """
    spec = (params or {}).get("worktree")
    if not isinstance(spec, dict) or not spec.get("branch"):
        return ""
    base = spec.get("base")
    return base if isinstance(base, str) else ""


def _invocation(params: dict) -> str:
    """The `/<skill> <args>` line an armed skill puts in front of the idea.

    A slash command is what the CLI resolves from the *start* of a prompt, so
    the invocation is the first line and the body follows as the rest of the
    skill's arguments — a skill whose procedure takes the task as its subject
    reads it from there, and one with its own arguments gets them on the line.
    """
    skill = (params or {}).get("skill") or {}
    name = skill.get("name")
    if not name:
        return ""
    line = f"/{name}"
    if skill.get("args"):
        line += f" {skill['args']}"
    return line + "\n\n"


def _launch_kwargs(params: dict) -> dict:
    """The armed parameters, in `supervisor.launch_run`'s own vocabulary.

    A worktree is resolved here rather than at arm time because the tree has to
    exist for *this* attempt: the branch a task was armed with may have been
    removed between arming and running, and `ensure_worktree` is the launch
    route's own answer to that.
    """
    kwargs = {name: params.get(name) or default
              for name, default in _LAUNCH_DEFAULTS.items()}
    kwargs["one_shot"] = bool(kwargs["one_shot"])
    kwargs["env"] = dict(kwargs["env"])
    spec = params.get("worktree") or {}
    if spec.get("branch"):
        anchor = kwargs["cwd"] or str(settings.project_root)
        created = worktree.ensure_worktree(anchor, spec["branch"],
                                           spec.get("base") or "")
        kwargs["cwd"] = created["path"]
    return kwargs


def _refuse_unrunnable(task: dict) -> None:
    """Why this task cannot start, in the order the operator needs to hear it.

    The live-run check comes first because it explains the task's own status: a
    task that is `running` is not "not armed", it is already going.
    """
    task_id = task["id"]
    if live_run(task_id) is not None:
        raise _already_live(task_id)
    blocked = deps.blocking(task_id)
    if blocked:
        raise RunRefused(
            "blocked by " + ", ".join(str(dep) for dep in blocked),
            blocking=blocked)
    # `running` passes: the check above already proved nothing backs that
    # claim. A runner killed with its server leaves the status behind, and
    # refusing on the row alone would strand the task forever.
    if task["status"] in (TASK_STATUS_ARMED, TASK_STATUS_RUNNING):
        return
    # A settled task keeps the parameters it was armed with, and Run spends
    # them again: a feature that failed once, or shipped and needs another
    # round, is not "not armed". A task never armed still is. The parameters
    # live on the attempts now, so "was it armed" is "is there one to reuse".
    if task["status"] in _SETTLED and latest_attempt(task_id) is not None:
        return
    raise RunRefused(
        f"task {task_id} is {task['status']!r}, not 'armed' — arm it first")


def _next_attempt(session, task_id: int) -> int:
    last = session.exec(
        select(AgentTaskRun.attempt)
        .where(AgentTaskRun.task_id == task_id)
        .order_by(AgentTaskRun.attempt.desc())).first() or 0
    return last + 1


def _auto_tag_feature(trace_id: str, feature_id: Optional[int]) -> None:
    """Stamp a task-launched session with its `feature-<id>` tag.

    Written at launch rather than waiting on ingest: `session_tags` carries
    no FK to `sessions`, so the row lands regardless of ingestion order, and
    the manual rename-and-tag pass this used to require becomes unnecessary.
    """
    if feature_id is None:
        return
    from lib.orm.models.trace import SessionTag  # noqa: PLC0415

    tag = f"feature-{feature_id}"
    with SessionLocal() as session:
        if session.get(SessionTag, (trace_id, tag)) is None:
            session.add(SessionTag(trace_id=trace_id, tag=tag, source="auto"))
            session.commit()


def _attach_trace(run_id: int, trace_id: str) -> dict:
    with SessionLocal() as session:
        row = session.get(AgentTaskRun, run_id)
        row.trace_id = trace_id
        row.status = RUN_STATUS_RUNNING
        session.add(row)
        session.commit()
        session.refresh(row)
        return _serialize(row)


def _resolve_stuck(task_id: int) -> None:
    """Retire the card a previous refusal left.

    `_announce_stuck` emits `once`, so a live card suppresses the next one: a
    task that stuck for a full queue and later sticks on a broken worktree
    would report the first reason forever.
    """
    from lib.agent_messages import events

    events.resolve(TASK_READY_TRACE, f"task-ready:{task_id}")
    # The chain-stopped card is keyed on the slice that broke, and emitted
    # `once` for the same reason: re-arming and running that slice is the act
    # that unbreaks the chain, so its card must retire here or a second break
    # of the same slice would never be announced.
    events.resolve(TASK_READY_TRACE, f"chain-stopped:{task_id}")


def _launch_images(task: dict) -> list[dict]:
    """The feature's images, then the task's own.

    Resolved here rather than copied onto the slice when the spec was
    transcribed: the mockup is the part of a spec most likely to be replaced
    after the slices exist, and reading it through `feature_id` means
    correcting it corrects the next attempt of every slice already cut. The
    feature's go first — they are the shared backdrop the slice-specific shot
    is read against.
    """
    own = images.payload_for(task["id"])
    feature_id = task.get("feature_id")
    if feature_id is None:
        return own
    from lib.features import images as feature_images  # noqa: PLC0415

    return feature_images.payload_for(feature_id) + own


def start_run(task_id: int) -> Optional[dict]:
    """Launch one attempt at a task. None when the task is unknown.

    Raises `RunRefused` for a task that is blocked, unarmed or already running,
    and `worktree.WorktreeRefused` for a tree that cannot be made.
    """
    task = store.get_task(task_id)
    if task is None:
        return None
    _refuse_unrunnable(task)
    row = open_attempt(task_id)
    kwargs = _launch_kwargs(row["params"])
    try:
        handle = supervisor.launch_run(_prompt(task, row["id"],
                                               row["params"]),
                                       images=_launch_images(task),
                                       **kwargs)
    except (supervisor.LaunchRefused, ImportError) as exc:
        # A refusal is not a crash: the attempt never started, so it is settled
        # here and the task stays armed rather than stranded as `running`. The
        # attempt still carries the `verify_cmd` it was armed with, so it
        # settles with the code for a verify that did not run — a NULL there
        # would read as "this attempt had no gate".
        complete_run(row["id"], status=RUN_STATUS_FAILED, exit_reason=str(exc),
                     verify_exit_code=verify.NOT_RUN if row["verify_cmd"]
                     else None)
        raise RunRefused(f"launch refused: {exc}") from exc
    view = _attach_trace(row["id"], handle.trace_id)
    store.set_status(task_id, TASK_STATUS_RUNNING)
    # Work beginning again over a claimed-done feature is exactly what a round
    # is. The decision reads the feature's last event alone, so a second
    # attempt inside one round appends nothing and the same run reported twice
    # costs nothing either. A spec step writes the spec, not code, so its run
    # is not a round and leaves the feature's log untouched.
    if (task.get("feature_id") is not None
            and task.get("kind") != TASK_KIND_SPEC):
        from lib.features import events as feature_events  # noqa: PLC0415
        feature_events.on_run_start(task["feature_id"], row["id"],
                                    trace_id=handle.trace_id)
    _auto_tag_feature(handle.trace_id, task.get("feature_id"))
    _resolve_stuck(task_id)
    # Registered last: a run that ends before this point fires the callback the
    # moment it is added, and it must not race the writes above.
    handle.add_done_callback(
        lambda outcome: handle_completion(task_id, row["id"], outcome))
    log.write("task_run_started", task_id=task_id, run_id=row["id"],
              trace_id=handle.trace_id, attempt=view["attempt"])
    return view


# ── completion ───────────────────────────────────────────────

def complete_run(run_id: int, *, status: str, exit_reason: str = "",
                 task_status: Optional[str] = None,
                 verify_exit_code: Optional[int] = None,
                 settled_by: str = SETTLED_BY_SESSION) -> bool:
    """Settle one attempt, and the task with it. Returns whether this call was
    the one that did it.

    Idempotent by `ended_at`: a second completion cannot rewrite how a run
    ended, and — because the return value gates it — cannot re-fire its
    dependents either.

    The task only follows the run while it is still `running`: a task re-armed
    by a human while the previous attempt was tearing down has moved on, and
    stamping it `done` would undo that.

    `settled_by` records which authority wrote the terminal state, so
    `reopen_for_agent_report` can later tell an inference apart from a report.
    """
    with SessionLocal() as session:
        row = session.get(AgentTaskRun, run_id)
        if row is None or row.ended_at is not None:
            return False
        task_id_of = row.task_id
        row.status = status
        row.ended_at = _now()
        row.exit_reason = (exit_reason or "")[:_REASON_MAX] or None
        row.verify_exit_code = verify_exit_code
        row.settled_by = settled_by
        session.add(row)
        task = session.get(AgentTask, row.task_id) if task_status else None
        if task is not None and task.status == TASK_STATUS_RUNNING:
            task.status = task_status
            task.updated_at = _now()
            session.add(task)
        session.commit()
    # A run settling says *this session finished*, never *this feature
    # shipped*, so it writes no feature event at all. The two used to share a
    # column, and a settling run therefore shipped features nobody claimed.
    log.write("task_run_settled", run_id=run_id, status=status,
              task_status=task_status)
    return True


def _settled(outcome) -> tuple[str, str]:
    """`(run status, reason)` from a `RunOutcome`.

    `exited` is the one clean end: the outcome is read back from the durable
    `agent_runs` row, so anything else — `failed`, or a row still claiming to
    run — is a run that did not finish on its own terms.
    """
    status = getattr(outcome, "status", "") or ""
    detail = getattr(outcome, "detail", "") or status
    return (RUN_STATUS_DONE if status == "exited" else RUN_STATUS_FAILED,
            detail)


#: The three ways a settle path can stand with respect to the verify command:
#: there is none, this caller holds the right to run it, or another caller
#: already does and this one must not settle at all.
NO_VERIFY, VERIFY_MINE, VERIFY_THEIRS = "none", "mine", "theirs"


def _claim_verify(run_id: int) -> tuple[str, str, dict]:
    """`(claim, command, params)` — stake the right to run this attempt's
    `verify_cmd` before spending it.

    Both settle paths can reach one attempt while it is still open: the agent
    reports `task finish`, its verify starts, and the session then ends — so
    `handle_completion` arrives minutes before `ended_at` is written. Keying
    off `ended_at` would let it start the operator's suite a *second* time, in
    the same tree, and throw one of the two verdicts away.

    So the claim is the row's own `verify_exit_code`, stamped `NOT_RUN` by one
    conditional `UPDATE` before the subprocess starts. The loser reads a
    non-NULL code and stands down rather than settling, because a settle
    without the verdict would strip the attempt of the gate it was armed with.
    A process killed mid-verify leaves that stamp behind, which is exactly what
    is true of it: the verify produced no verdict.
    """
    try:
        with SessionLocal() as session:
            row = session.get(AgentTaskRun, run_id)
            command = (row.verify_cmd or "").strip() if row is not None else ""
            params = store._decode_params(row.params_json) if row else {}
    except Exception:  # noqa: BLE001 — a gate we cannot read is one we must not skip
        # Standing down, not settling. Invariant 4 is written over the row, not
        # over what this reader managed to load: an attempt whose `verify_cmd`
        # column we failed to read still has one, and settling on the agent's
        # own word would report a pass for a verify nobody ran.
        log.error("task_verify_unreadable", run_id=run_id, exc_info=True)
        return VERIFY_THEIRS, "", {}
    if not command:
        return NO_VERIFY, "", {}
    try:
        with SessionLocal() as session:
            claimed = session.execute(
                update(AgentTaskRun)
                .where(AgentTaskRun.id == run_id,
                       AgentTaskRun.verify_exit_code.is_(None))
                .values(verify_exit_code=verify.NOT_RUN)).rowcount
            session.commit()
    except Exception:  # noqa: BLE001 — a gate we cannot stake is one we must not skip
        # Standing down is the only safe answer once a command has been read:
        # settling here would report a pass for a verify nobody ran, and
        # running it unclaimed could spend it twice.
        log.error("task_verify_claim_failed", run_id=run_id, exc_info=True)
        return VERIFY_THEIRS, "", {}
    if not claimed:
        log.write("task_verify_already_claimed", run_id=run_id)
        return VERIFY_THEIRS, "", {}
    return VERIFY_MINE, command, params


def _is_open(run_id: int) -> bool:
    """Whether this attempt is still unsettled — the difference between a
    verify someone else is running and a run that settled minutes ago."""
    try:
        with SessionLocal() as session:
            row = session.get(AgentTaskRun, run_id)
            return row is not None and row.ended_at is None
    except Exception:  # noqa: BLE001 — a report line, never a decision
        log.error("task_run_openness_unreadable", run_id=run_id, exc_info=True)
        return True


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
        session.commit()
    if not reopened:
        return False
    store.set_status(task_id, TASK_STATUS_RUNNING)
    log.write("task_run_reopened", run_id=run_id, task_id=task_id)
    return True


def _verify_cwd(params: dict) -> Optional[str]:
    """The tree the attempt ran in — looked up, never created.

    Raises when the branch it was armed with no longer has a tree: verifying a
    checkout materialised at settle time would grade the branch's committed
    state, which is exactly the work the attempt may have failed to produce.

    An attempt armed with no tree at all verifies in the project root, never in
    whatever directory the server happens to have been started in.
    """
    spec = (params or {}).get("worktree") or {}
    anchor = params.get("cwd") or str(settings.project_root)
    if not spec.get("branch"):
        return anchor
    found = worktree.existing_worktree(anchor, spec["branch"])
    if found is None:
        raise RunRefused(f"no worktree for {spec['branch']!r}")
    return found["path"]


def _apply_verdict(command: str, params: dict, status: str,
                   reason: str) -> tuple[str, str, Optional[int]]:
    """`(status, reason, verify_exit_code)` once the verify has had its say.

    With no command the run's own result stands and the code is NULL — that
    NULL is what keeps "no gate" distinguishable from "the gate passed". With
    one, the command is the arbiter and always leaves a code behind: the run
    answering for itself is the thing a `verify_cmd` was armed to override.
    """
    if not command:
        return status, reason, None
    try:
        cwd = _verify_cwd(params)
    except Exception as exc:  # noqa: BLE001 — a tree we cannot reach is a failed verify
        verdict = verify.Verdict(verify.NOT_RUN, f"verify did not run: {exc}")
    else:
        verdict = verify.run_verify(command, cwd=cwd)
    return (RUN_STATUS_DONE if verdict.passed else RUN_STATUS_FAILED,
            verdict.reason, verdict.exit_code)


def _settle_and_fire(task_id: int, run_id: int, status: str, reason: str,
                     command: str, params: dict,
                     *, resync: bool = False,
                     settled_by: str = SETTLED_BY_SESSION
                     ) -> tuple[bool, list[int]]:
    """Verify, settle, fire. The terminal write is in a `finally` because a
    settle that does not happen outlives this process, and one that happens
    wrong does not — and `code` starts at the did-not-run value so even a
    settle written out of that `finally` leaves the gate accounted for."""
    code = verify.NOT_RUN if command else None
    try:
        status, reason, code = _apply_verdict(command, params, status, reason)
    finally:
        ended = complete_run(run_id, status=status, exit_reason=reason,
                             task_status=_TASK_STATUS_FOR[status],
                             verify_exit_code=code, settled_by=settled_by)
    if not ended:
        return False, []
    if resync:
        _resync_feature(task_id)
    return True, fire_ready_dependents(task_id)


#: Verifies still in flight, by run id. A settle that happens on a worker
#: thread is still a settle a caller may need to see, and a registry makes that
#: wait a join on the exact run rather than a poll for its effect.
_verifying: dict[int, Thread] = {}


def _spawn_verify(args: tuple, *, resync: bool,
                  settled_by: str = SETTLED_BY_SESSION) -> Thread:
    """Hand one claimed verify to a worker thread and register it.

    Registered before it starts, so a caller that asks the instant after this
    returns cannot be told there is nothing to wait for.
    """
    run_id = args[1]

    def _work() -> None:
        try:
            _settle_and_fire(*args, resync=resync, settled_by=settled_by)
        finally:
            _verifying.pop(run_id, None)

    worker = Thread(target=_work, daemon=True, name=f"verify-run-{run_id}")
    _verifying[run_id] = worker
    worker.start()
    return worker


def wait_for_verify(run_id: int, timeout: Optional[float] = None) -> None:
    """Block until the verify settling `run_id` has finished, if one is running.

    Absent from the registry means the settle already landed, so this is a join
    on a named thread and never a poll for a condition.
    """
    worker = _verifying.get(run_id)
    if worker is not None:
        worker.join(timeout)


def handle_completion(task_id: int, run_id: int, outcome) -> Optional[Thread]:
    """The run's own completion path: settle the attempt, then fire whatever it
    unblocked.

    Runs on the SDK loop's thread (`RunHandle.add_done_callback`), which every
    other live run shares, so it must not block — and a `verify_cmd` is a test
    suite, measured in minutes. So an attempt with one hands the verify and the
    settle to a worker thread and returns at once; an attempt without one still
    settles inline, where the caller can see it happen. The thread comes back
    so a caller that needs the verdict can wait for it.

    Arriving second is the common case, not the exception: the agent's own
    `task finish` settles the work, and the session it settled from ends here
    afterwards. A verify already claimed elsewhere is therefore left alone.
    """
    status, reason = RUN_STATUS_FAILED, "run ended"
    try:
        status, reason = _settled(outcome)
    finally:
        claim, command, params = _claim_verify(run_id)
        args = (task_id, run_id, status, reason, command, params)
        worker = None
        if claim == VERIFY_MINE:
            worker = _spawn_verify(args, resync=False)
        elif claim == NO_VERIFY:
            _settle_and_fire(*args)
    return worker


# ── firing ───────────────────────────────────────────────────

def _announce_stuck(task: dict, why: str) -> None:
    """Tell the operator about a task that came due and could not start.

    The only outcome of a release that a human has to act on: the task is
    runnable, the launch was refused, and it stays `armed` — so the card
    carries the refusal rather than leaving the task silently parked.
    """
    from lib.agent_messages import events

    events.emit(
        "task.ready", trace_id=TASK_READY_TRACE,
        title=f"Task ready: {task['title'][:80]}",
        body=(f"Every dependency of task {task['id']} — **{task['title']}** — "
              f"is done, but it could not start: {why}. It is still armed."),
        key=f"task-ready:{task['id']}",
        links=[{"label": "Open Tasks", "href": "/tasks"}], once=True)


#: The statuses a dependent can hold while a failed slice is what stands
#: between it and running. Invariant 6 is about the cascade, not about `armed`.
_PARKED = (TASK_STATUS_ARMED, TASK_STATUS_QUEUED)


def _announce_chain_stopped(blocker: dict, parked: list[int]) -> None:
    """Tell the operator which slice stopped a cascade.

    `blocking` treats "not done" as one answer, so a failed dependency parks
    its dependents in `armed` looking exactly like a normal wait. It is not
    one: nothing is coming, and only a human re-arming that slice will start
    the chain again. The card names the slice rather than the dependents
    because that is the row to act on.
    """
    from lib.agent_messages import events

    waiting = ", ".join(str(task_id) for task_id in parked)
    events.emit(
        "task.chain_stopped", trace_id=TASK_READY_TRACE,
        title=f"Chain stopped at: {blocker['title'][:80]}",
        body=(f"Task {blocker['id']} — **{blocker['title']}** — failed, and "
              f"{len(parked)} task(s) still wait on it: {waiting}. They stay "
              f"armed, but nothing will release them until "
              f"{blocker['id']} runs again."),
        key=f"chain-stopped:{blocker['id']}",
        links=[{"label": "Open Tasks", "href": "/tasks"}], once=True)


def _announce_broken_chains(task_id: int, fired: list[int]) -> None:
    """Report every failed slice that is holding a dependent of `task_id`.

    Read after the firing, not instead of it: a dependent that launched is not
    blocked by anything, and a dependent still waiting on an *unfinished*
    slice is waiting normally. Only a `failed` blocker is the broken chain.

    The failed slice is not necessarily the one that just completed — a
    dependent can wait on two slices, one landing now and one that failed
    earlier — so the blocker is looked up per dependent.

    `failed_blockers` reads only `requires` edges, so a dependent queued behind
    unrelated work by an `after` edge raises no card: it was already released
    by the failure.
    """
    by_blocker: dict[int, tuple[dict, list[int]]] = {}
    for dependent in deps.dependents(task_id):
        if dependent in fired:
            continue
        task = store.get_task(dependent)
        # Parked, not merely unstarted: `idea` is a backlog row nobody asked
        # to run, and anything settled or live is not waiting on this edge.
        if task is None or task["status"] not in _PARKED:
            continue
        for blocker in deps.failed_blockers(dependent):
            by_blocker.setdefault(blocker["id"], (blocker, []))[1].append(
                dependent)
    for blocker, parked in by_blocker.values():
        _announce_chain_stopped(blocker, parked)
        log.write("task_chain_stopped", task_id=blocker["id"], parked=parked)


def _fire(task_id: int) -> bool:
    """Launch one task that just became runnable. Returns whether it started.

    A dependency edge IS the grant: arming a task behind another one is the
    operator saying "run this when that lands", so the release needs no second
    opt-in. A refusal is reported and leaves the task armed — the operator can
    run it once the reason is gone.
    """
    task = store.get_task(task_id)
    if task is None or task["status"] != TASK_STATUS_ARMED:
        return False
    if deps.blocking(task_id):
        return False
    try:
        start_run(task_id)
    except (RunRefused, worktree.WorktreeRefused) as exc:
        _announce_stuck(task, str(exc))
        return False
    return True


def fire_if_ready(task_id: int) -> bool:
    """Resolve one task against a write that could have made it runnable.

    Completion is not the only such write: arming a task whose dependencies
    already finished, or dropping the edge that was blocking it, changes the
    same answer and would otherwise leave the task armed with nothing left to
    fire it.

    A task with no dependencies is not resolved here: nothing could ever come
    due for it, and arming one is a request to write parameters, not to run.

    The retirement runs AFTER the fire, never before: an `after` edge written
    behind a task that already settled is born satisfied, and sweeping it
    first would leave this task with no dependencies and send the guard above
    home without launching the very thing the edge was drawn to order.
    """
    if not deps.depends_on(task_id):
        return False
    try:
        return _fire(task_id)
    finally:
        deps.retire_satisfied_after(task_id)


def fire_ready_dependents(task_id: int) -> list[int]:
    """Launch every dependent this completion just unblocked.

    There is no scheduler and no interval behind this: the completion that
    could change the answer is the thing that recomputes it, so a task whose
    last dependency just landed is resolved exactly once, here.

    The same completion retires the `after` edges it just honoured. Last,
    because the dependent list is read from those edges and the notice below
    reads it again — a sweep in the middle would shorten the walk it is part
    of.
    """
    dependents = deps.dependents(task_id)
    fired = []
    for dependent in dependents:
        try:
            if _fire(dependent):
                fired.append(dependent)
        except Exception:  # noqa: BLE001 — one dependent cannot sink the rest
            log.error("task_fire_failed", task_id=dependent, exc_info=True)
    if fired:
        log.write("task_dependents_fired", task_id=task_id, fired=fired)
    try:
        _announce_broken_chains(task_id, fired)
    except Exception:  # noqa: BLE001 — a notice cannot sink a completion
        log.error("task_chain_notice_failed", task_id=task_id, exc_info=True)
    for dependent in dependents:
        try:
            deps.retire_satisfied_after(dependent)
        except Exception:  # noqa: BLE001 — a tidy-up cannot sink a completion
            log.error("task_after_retire_failed", task_id=dependent,
                      exc_info=True)
    return fired


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


def _refuse_with_open_defects(task_id: int, run_id: int) -> None:
    """A fix task reported done over a defect still open is refused.

    Otherwise the board says fixed and the ledger says open, and nothing
    reconciles them: regin never writes one side from the other. Refused
    before the report is posted, so a retry after closing is the only report.
    """
    ids = _open_fix_defects(task_id)
    if not ids:
        return
    log.warn("task_run_finish_refused", run_id=run_id, task_id=task_id,
             open_defects=ids)
    cmds = "\n".join(f"  {_close_defect_command(i)}" for i in ids)
    raise RunRefused(
        f"task #{task_id} is the fix for {_defects_named(ids)}, still open. "
        f"Close each once the fix is verified, then finish again:\n{cmds}\n"
        f"If it is not fixed, finish with --status failed instead.",
        blocking=ids)


def finish_run(run_id: int, *, status: str = RUN_STATUS_DONE,
               reason: str = "") -> dict:
    """The agent's own report, from inside its run, that the work is done.

    This is the completion a dependency chain actually waits on. A task run is
    launched steerable (`one_shot` off), so its session does not end when the
    agent stops working — it parks on an empty prompt queue until someone stops
    it, and `handle_completion` with it. Nothing downstream could ever fire.

    So the run reports for itself, and the session it reports from stays open:
    settling the attempt is a statement about the work, not about the
    conversation. Stop still reaches the run afterwards — `_deliver` addresses
    the latest attempt, not a live one.

    Idempotent, and safe against the callback that may still arrive: both paths
    settle through `complete_run`, whose `ended_at` guard picks one winner and
    whose return value is what gates the firing. An attempt with a `verify_cmd`
    is decided a step earlier, by `_claim_verify` — `ended_at` is not written
    until the verify returns, so it cannot arbitrate the window the verify runs
    in.
    """
    if status not in _TASK_STATUS_FOR:
        raise ValueError(f"unknown finish status {status!r}")
    with SessionLocal() as session:
        row = session.get(AgentTaskRun, run_id)
        task_id = row.task_id if row is not None else None
    if task_id is None:
        return {"run_id": run_id, "task_id": None, "settled": False,
                "verifying": False, "fired": []}
    if status == RUN_STATUS_DONE:
        _refuse_with_open_defects(task_id, run_id)
    # Before the default is substituted: only what the AGENT actually wrote is
    # a report. "agent signalled finish" is regin talking to itself.
    if reason.strip():
        _post_agent_report(task_id, run_id, status, reason.strip())
    reason = reason or "agent signalled finish"
    reopen_for_agent_report(run_id)
    claim, command, params = _claim_verify(run_id)
    args = (task_id, run_id, status, reason, command, params)
    if claim != NO_VERIFY:
        # The verify is what the settle must carry, and it is a test suite: the
        # caller is the agent's own `regin task finish`, on a short HTTP
        # timeout, so waiting for it here would report a failure to an agent
        # whose work is fine. `VERIFY_THEIRS` stands down for the same reason
        # in reverse — someone else's verify owns this settle, unless that
        # verify already finished, in which case this is simply a second report.
        if claim == VERIFY_MINE:
            _spawn_verify(args, resync=True, settled_by=SETTLED_BY_AGENT)
        verifying = claim == VERIFY_MINE or _is_open(run_id)
        log.write("task_run_finish_verifying", run_id=run_id, task_id=task_id,
                  claim=claim, verifying=verifying)
        return {"run_id": run_id, "task_id": task_id, "settled": False,
                "verifying": verifying, "fired": []}
    # Only this path refreshes the remote: it is the one that runs on a request
    # thread. `handle_completion` runs on the SDK loop's thread and cannot
    # spend a round trip against a tracker.
    settled, fired = _settle_and_fire(*args, resync=True,
                                      settled_by=SETTLED_BY_AGENT)
    log.write("task_run_finish_signalled", run_id=run_id, task_id=task_id,
              status=status, settled=settled, fired=fired)
    return {"run_id": run_id, "task_id": task_id, "settled": settled,
            "verifying": False, "fired": fired}


def _resync_feature(task_id: int) -> None:
    """Re-read the remote issue a finished run was told to move. Never raises.

    The event is an agent reporting its own work finished — a linked run is
    launched told to move the issue's state to match what it did, so that
    report is the moment regin's copy is known stale.

    A failure is swallowed rather than propagated: the report settles the
    attempt and releases whatever waited on it, and a tracker being unreachable
    cannot be allowed to walk that back. The reason lands on the feature
    (`sync_error`), which is what its page states.
    """
    from lib.features import store as feature_store  # noqa: PLC0415
    from lib.features import sync  # noqa: PLC0415

    feature = None
    try:
        feature = feature_store.feature_of_task(task_id)
        if feature is None or not sync.is_remote(feature):
            return
        sync.refresh(feature.id)
    except Exception:  # noqa: BLE001 — a remote cannot sink the finish
        log.error("feature_resync_failed", task_id=task_id,
                  feature_id=getattr(feature, "id", None), exc_info=True)
