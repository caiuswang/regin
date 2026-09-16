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

import shlex
import sys
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy.exc import IntegrityError
from sqlmodel import select

from lib.activity_log import get_activity_logger
from lib.agent_sdk import store as sdk_store, supervisor, worktree
from lib.comments import store as comments
from lib.orm import SessionLocal
from lib.orm.models.comments import RESOLUTION_OPEN, SUBJECT_TASK
from lib.orm.models.agent_runs import AgentRun
from lib.orm.models.tasks import (
    AgentTask, AgentTaskRun, RUN_STATUS_DONE,
    RUN_STATUS_FAILED, RUN_STATUS_RUNNING, RUN_STATUS_STARTING,
    TASK_STATUS_ARMED, TASK_STATUS_DONE, TASK_STATUS_FAILED,
    TASK_STATUS_RUNNING,
)
from lib.settings import settings
from lib.tasks import deps, grain, images, links, store
from lib.tasks.integrations import registry
from lib.tasks.integrations.regin import key_for_task

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
    }


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
    with SessionLocal() as session:
        row = session.exec(
            select(AgentTaskRun)
            .where(AgentTaskRun.task_id == task_id)
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
    """
    with SessionLocal() as session:
        rows = session.exec(
            select(AgentTaskRun)
            .where(AgentTaskRun.task_id == task_id)
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


def _finish_block(run_id: int, linked: bool = False) -> str:
    """The `<finish>` instruction appended to a run's launch prompt.

    The command is written out literally, not left to an env var, so a session
    resumed later — with none of the launch's environment — can still report.

    A linked run is additionally told to update its issue *first*. The signal
    is what makes regin re-read the tracker, so an agent that reports before
    it records leaves regin holding the state from before its own work. An
    unlinked run is told nothing about a tracker: there is no issue to update,
    and the instruction would be noise it has to resolve.
    """
    order = (
        "Before you run it: record the work on the linked issue and move its "
        "state — regin re-reads the issue when this command lands, and a "
        "state you change afterwards will not reach the task.\n"
    ) if linked else ""
    return (
        "\n\n<finish>\n"
        "This work was launched from a regin task. Running the command below "
        "is the ONLY thing that marks the task complete and releases whatever "
        "depends on it; your session stays open either way, so run it as soon "
        "as the work is done rather than waiting to be stopped.\n"
        + order
        + f"   {_finish_command(run_id)}\n"
        "If you could not finish, run the same command with "
        "`--status failed --reason '<why>'`.\n"
        "</finish>"
    )


def _tracker_line(link: dict) -> str:
    """One linked issue, named the way an operator would name it.

    The provider is named by asking the registry, never by knowing what Linear
    is called here — the same rule that keeps a second tracker additive.
    """
    issue = link.get("issue") or {}
    name = registry.display_name(link.get("provider", ""))
    head = f"  - {name} {link.get('external_key', '')}"
    if issue.get("state"):
        head += f" [{issue['state']}]"
    title = issue.get("title") or "(not fetched — regin holds no snapshot)"
    return f"{head} — {title}\n    {link.get('url') or ''}".rstrip()


def _tracker_block(rows: list[dict]) -> str:
    """The `<tracker>` instruction for a task that links to another tracker.

    Identity, not content: the issue's description is deliberately not
    repeated, because the composer already offered it as the task's body at
    capture time and a second copy is either duplication or a stale
    contradiction of one. The agent that needs the body reads it where it is
    current.

    Empty — not an empty block — for a task with no link, so an unlinked run's
    prompt is exactly what it was.
    """
    if not rows:
        return ""
    return (
        "\n\n<tracker>\n"
        "This task is linked to the issue(s) below. The issue — and its "
        "comments, which can revise or reverse the description — is the "
        "source of truth for what is being asked; regin's copy of the title "
        "and details is a snapshot taken when the task was captured and may "
        "already be stale.\n"
        + "\n".join(_tracker_line(row) for row in rows)
        + "\nRead the issue and its comments before you start. When the work "
        "lands, record it there: say what changed, and move the issue's state "
        "to match. Do that before you run the finish command below — regin "
        "re-reads the issue at that moment, so a state you change afterwards "
        "is not the one the task will show.\n"
        "</tracker>"
    )


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
    conversation ends and the next begins."""
    gap = "\n" if previous not in (None, thread_id) else ""
    return f"{gap}  [{comment['author_kind']}] {comment['body'].strip()}\n"


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
    plural = "" if dropped == 1 else "s"
    omitted = (f"\n({dropped} earlier comment{plural} omitted to fit; the "
               "most recent are the ones kept.)\n") if dropped else ""
    return (
        "\n\n<followup>\n"
        "Comments left on this task that nobody has resolved. They were "
        "written on the task page, possibly after an earlier attempt, and "
        "they are still here because the conversation is still open — read "
        "them as part of what is being asked, not as history already dealt "
        "with. Later ones correct earlier ones. They keep arriving on every "
        "run until someone resolves the thread.\n"
        + "".join(lines).lstrip("\n")
        + omitted
        + "</followup>"
    )


def _feature_key(task: dict, linked: bool) -> Optional[str]:
    """The native feature this run builds, or None for a run of an issue in
    another tracker — the `<tracker>` block already names that one."""
    if linked:
        return None
    return key_for_task(task["parent_id"] or task["id"])


def _feature_block(task: dict, linked: bool) -> str:
    """The `<feature>` instruction: which key this run's session counts under.

    The attribution itself needs no act from the agent — the lane reads it off
    the run row. What only the agent can do is blame a defect, so the block is
    mostly the command for that, with the key filled in.
    """
    key = _feature_key(task, linked)
    if key is None:
        return ""
    return (
        "\n\n<feature>\n"
        f"This run is a session of feature {key}; regin attributes it "
        "automatically. If you find something the feature got wrong, declare "
        f"it against that key: `regin defects open \"<what>\" --feature {key} "
        "--session <session id>`. Shipping is the operator's act on the "
        "feature's task page, not yours.\n"
        "</feature>"
    )


def _prompt(task: dict, run_id: int) -> str:
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
    """
    body = (task.get("body") or "").strip()
    text = body or task["title"]
    rows = links.list_links(task["id"])
    return (_invocation(task) + text[:PROMPT_MAX]
            + _tracker_block(rows) + _followup_block(task["id"])
            + _feature_block(task, bool(rows))
            + _finish_block(run_id, linked=bool(rows)))


def _invocation(task: dict) -> str:
    """The `/<skill> <args>` line an armed skill puts in front of the idea.

    A slash command is what the CLI resolves from the *start* of a prompt, so
    the invocation is the first line and the body follows as the rest of the
    skill's arguments — a skill whose procedure takes the task as its subject
    reads it from there, and one with its own arguments gets them on the line.
    """
    skill = (task.get("params") or {}).get("skill") or {}
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
    # round, is not "not armed". A task never armed still is.
    if task["status"] in _SETTLED and task["params"]:
        return
    raise RunRefused(
        f"task {task_id} is {task['status']!r}, not 'armed' — arm it first")


def _next_attempt(session, task_id: int) -> int:
    last = session.exec(
        select(AgentTaskRun.attempt)
        .where(AgentTaskRun.task_id == task_id)
        .order_by(AgentTaskRun.attempt.desc())).first() or 0
    return last + 1


def _open_run_row(task_id: int) -> dict:
    """The attempt row, written *before* the launch: a crash between the two
    leaves this visible `starting` row rather than an untracked child.

    `UNIQUE(task_id, attempt)` closes the window between the liveness check and
    this insert. Two requests that both read "not live" compute the same next
    attempt, so the constraint picks the winner and the loser is refused with
    the liveness check's own words — identity resolving a race, exactly as it
    resolves a redelivered capture, and never a lock or a retry.
    """
    with SessionLocal() as session:
        row = AgentTaskRun(task_id=task_id,
                           attempt=_next_attempt(session, task_id),
                           status=RUN_STATUS_STARTING)
        session.add(row)
        try:
            session.commit()
        except IntegrityError:
            session.rollback()
            log.warn("task_run_race_lost", task_id=task_id)
            raise _already_live(task_id) from None
        session.refresh(row)
        return _serialize(row)


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


def start_run(task_id: int) -> Optional[dict]:
    """Launch one attempt at a task. None when the task is unknown.

    Raises `RunRefused` for a task that is blocked, unarmed or already running,
    and `worktree.WorktreeRefused` for a tree that cannot be made.
    """
    task = store.get_task(task_id)
    if task is None:
        return None
    _refuse_unrunnable(task)
    kwargs = _launch_kwargs(task["params"])
    row = _open_run_row(task_id)
    try:
        handle = supervisor.launch_run(_prompt(task, row["id"]),
                                       images=images.payload_for(task_id),
                                       **kwargs)
    except (supervisor.LaunchRefused, ImportError) as exc:
        # A refusal is not a crash: the attempt never started, so it is settled
        # here and the task stays armed rather than stranded as `running`.
        complete_run(row["id"], status=RUN_STATUS_FAILED, exit_reason=str(exc))
        raise RunRefused(f"launch refused: {exc}") from exc
    view = _attach_trace(row["id"], handle.trace_id)
    store.set_status(task_id, TASK_STATUS_RUNNING)
    # The one event that may walk a ship claim back: work restarting after a
    # feature was claimed done is exactly what a round is — on the parent when
    # this task is a run of one, on the task itself when its runs are its own.
    grain.recompute_parent(task_id, event=grain.EVENT_RUN_STARTED)
    grain.walk_back_own_claim(task_id)
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
                 verify_exit_code: Optional[int] = None) -> bool:
    """Settle one attempt, and the task with it. Returns whether this call was
    the one that did it.

    Idempotent by `ended_at`: a second completion cannot rewrite how a run
    ended, and — because the return value gates it — cannot re-fire its
    dependents either.

    The task only follows the run while it is still `running`: a task re-armed
    by a human while the previous attempt was tearing down has moved on, and
    stamping it `done` would undo that.
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
        session.add(row)
        task = session.get(AgentTask, row.task_id) if task_status else None
        if task is not None and task.status == TASK_STATUS_RUNNING:
            task.status = task_status
            task.updated_at = _now()
            session.add(task)
        session.commit()
    # A run settling says *this session finished*, never *this feature
    # shipped* — so the parent is recomputed from its children and the
    # done-claim guard leaves a claimed `done` alone.
    grain.recompute_parent(task_id_of, event=grain.EVENT_RUN_SETTLED)
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


def handle_completion(task_id: int, run_id: int, outcome) -> None:
    """The run's own completion path: settle the attempt, then fire whatever it
    unblocked.

    Runs on the SDK loop's thread (`RunHandle.add_done_callback`), so it must
    not block. The terminal write is in a `finally` because reading the outcome
    can fail and a row left claiming `running` outlives this process — settling
    it wrong is recoverable, not settling it at all is not.
    """
    status, reason = RUN_STATUS_FAILED, "run ended"
    try:
        status, reason = _settled(outcome)
    finally:
        ended = complete_run(run_id, status=status, exit_reason=reason,
                             task_status=_TASK_STATUS_FOR[status])
    if ended:
        fire_ready_dependents(task_id)


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
    """
    if not deps.depends_on(task_id):
        return False
    return _fire(task_id)


def fire_ready_dependents(task_id: int) -> list[int]:
    """Launch every dependent this completion just unblocked.

    There is no scheduler and no interval behind this: the completion that
    could change the answer is the thing that recomputes it, so a task whose
    last dependency just landed is resolved exactly once, here.
    """
    fired = []
    for dependent in deps.dependents(task_id):
        try:
            if _fire(dependent):
                fired.append(dependent)
        except Exception:  # noqa: BLE001 — one dependent cannot sink the rest
            log.error("task_fire_failed", task_id=dependent, exc_info=True)
    if fired:
        log.write("task_dependents_fired", task_id=task_id, fired=fired)
    return fired


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
    whose return value is what gates the firing.
    """
    if status not in _TASK_STATUS_FOR:
        raise ValueError(f"unknown finish status {status!r}")
    with SessionLocal() as session:
        row = session.get(AgentTaskRun, run_id)
        task_id = row.task_id if row is not None else None
    if task_id is None:
        return {"run_id": run_id, "task_id": None, "settled": False,
                "fired": []}
    settled = complete_run(run_id, status=status,
                           exit_reason=reason or "agent signalled finish",
                           task_status=_TASK_STATUS_FOR[status])
    if settled:
        # Only this path refreshes the links: it is the one that runs on a
        # request thread. `handle_completion` runs on the SDK loop's thread
        # and cannot spend a round trip against a tracker.
        links.resync_task(task_id)
    fired = fire_ready_dependents(task_id) if settled else []
    log.write("task_run_finish_signalled", run_id=run_id, task_id=task_id,
              status=status, settled=settled, fired=fired)
    return {"run_id": run_id, "task_id": task_id, "settled": settled,
            "fired": fired}
