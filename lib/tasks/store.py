"""The writer of `agent_tasks` — capture, edits, and arming.

Keeping every write behind this module means the `status='idea'` invariant,
the JSON encoding of tags/links and identifier-based replay resolution happen
in one place regardless of caller — the HTTP capture route and the local CLI
both land here. The execution lifecycle's own writes (a run starting, a run
settling) live in `lib/tasks/runs.py`, which settles the attempt row and the
task row in one transaction.

Replay is resolved by the `(source, external_id)` unique constraint, never by
a time window: every webhook provider redelivers, and a dedup interval would
be a clock heuristic that still gets the answer wrong under retry storms.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy.exc import IntegrityError
from sqlmodel import delete, select

from lib.activity_log import get_activity_logger
from lib.defects import rounds
from lib.orm import SessionLocal
from lib.orm.models.sync import Repo
from lib.orm.models.tasks import (
    AgentTask, AgentTaskDep, AgentTaskImage, AgentTaskLink, AgentTaskRun,
    PRIORITY_ORDER, TASK_PRIORITY_NONE, TASK_STATUS_ARCHIVED,
    TASK_STATUS_ARMED, TASK_STATUS_DONE, TASK_STATUS_FAILED, TASK_STATUS_IDEA,
    TASK_STATUS_RUNNING,
)

log = get_activity_logger("tasks")

# What the *edit* surface may set. Everything past `armed` is reached by the
# execution lifecycle instead (`arm_task`, `set_status`), so a PATCH asking for
# one is refused rather than parking the row in a status it did not earn.
# Archiving is exempt and handled in `_apply_status`: it is a filing decision
# about work the operator is finished with, not an execution move.
TASK_TRANSITIONS: dict[str, tuple[str, ...]] = {
    TASK_STATUS_ARCHIVED: (TASK_STATUS_IDEA,),
    # The done-claim: an operator saying this feature shipped. The native
    # analogue of moving a Linear issue to Done, and the boundary every later
    # defect is blamed against — which is why it cannot be derived from a run
    # settling, and why it is an edit-surface transition rather than one the
    # run lifecycle reports.
    TASK_STATUS_IDEA: (TASK_STATUS_DONE,),
    TASK_STATUS_RUNNING: (TASK_STATUS_DONE,),
    TASK_STATUS_FAILED: (TASK_STATUS_DONE,),
    # The un-claim. It costs no round: nothing was built between the claim and
    # withdrawing it, so nothing was walked back. Not reachable from
    # `archived` — restore to `idea` first.
    TASK_STATUS_DONE: (TASK_STATUS_IDEA,),
}

# Arming is also re-arming: a finished or failed attempt is armed again with
# fresh parameters, which is the retry path. A task mid-run is not, because its
# parameters are already in flight.
ARMABLE_FROM: tuple[str, ...] = (TASK_STATUS_IDEA, TASK_STATUS_ARMED,
                                 TASK_STATUS_DONE, TASK_STATUS_FAILED)


class DeleteRefused(ValueError):
    """A task that cannot be removed — a 409 carrying the reason."""

    def __init__(self, task_id: int, detail: str) -> None:
        super().__init__(f"cannot delete task {task_id}: {detail}")
        self.task_id = task_id


class TransitionRejected(ValueError):
    """A status change the lifecycle does not allow — a 400 naming the target."""

    def __init__(self, current: str, target: str, detail: str = "") -> None:
        super().__init__(
            f"cannot set status to {target!r} from {current!r}: "
            + (detail or "the edit surface only restores an archived task"))
        self.current = current
        self.target = target


def _now() -> str:
    """UTC, in the same format as the columns' `datetime('now')` default."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _encode(value) -> Optional[str]:
    return json.dumps(value) if value else None


def _decode(raw: Optional[str]):
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return []
    return parsed if isinstance(parsed, list) else []


def _decode_params(raw: Optional[str]) -> dict:
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def serialize(task: AgentTask) -> dict:
    return {
        "id": task.id,
        "title": task.title,
        "body": task.body,
        "status": task.status,
        "source": task.source,
        "external_id": task.external_id,
        "tags": _decode(task.tags_json),
        "links": _decode(task.links_json),
        "created_by": task.created_by,
        "created_at": task.created_at,
        "updated_at": task.updated_at,
        "params": _decode_params(task.params_json),
        "verify_cmd": task.verify_cmd,
        "autorun": bool(task.autorun),
        "project_id": task.project_id,
        "priority": task.priority or TASK_PRIORITY_NONE,
        # Derived from `priority`, but serialized rather than hidden: it is a
        # keyset sort column, and `keyset_page_stmt` builds the next cursor by
        # reading each order column's value back off this dict.
        "priority_rank": task.priority_rank,
        "parent_id": task.parent_id,
        "assignee": task.assignee,
        # Resolved beside the id, never in place of it: `assignee` carries no
        # foreign key, so an id pointing at a deleted user is a real state and
        # the client renders the gap rather than a name regin invented.
        "assignee_name": _assignee_name(task.assignee),
        # `1 + completions_walked_back`, derived on every read so the count and
        # the reported number cannot drift. The column itself is never served.
        "rounds": rounds.from_task(task),
        # The claim is its own axis: `status == 'done'` is what a run settling
        # leaves behind, and only this says the operator called it shipped.
        "claimed_at": task.claimed_at,
    }


def _assignee_name(user_id: Optional[int]) -> Optional[str]:
    """The claimer's display name, or None when the id resolves to nobody."""
    if not user_id:
        return None
    from lib.orm import AuthSessionLocal
    from lib.orm.models.users import User

    with AuthSessionLocal() as session:
        user = session.get(User, user_id)
        return user.display_name if user else None


def _set_priority(row: AgentTask, priority: str) -> None:
    """Write the word and its sort key together — never one without the other.

    Two columns for one axis is a denormalization with a reason: ordering by a
    CASE over the words cannot use an index, and its value cannot be read back
    off a serialized row, so the keyset cursor for page two cannot be built.
    """
    row.priority = priority
    row.priority_rank = PRIORITY_ORDER[priority]


def _resolved_project(session, project_id: Optional[int]) -> Optional[int]:
    """The repo id, once it is known to be registered. `None` unfiles the task.

    A task's project IS a repo — the same thing `project_id` means in
    `pattern_deployments` and in `web/blueprints/skills.py`. Checked rather
    than trusted because there is no foreign key to do it: a task pointing at
    a repo that was dropped between the two requests would render as an
    unresolvable chip forever.
    """
    if project_id is None:
        return None
    if session.get(Repo, project_id) is None:
        raise UnknownProject(project_id)
    return project_id


class BulkFieldRejected(ValueError):
    """A field a bulk edit does not own — a 400 naming that field.

    Distinct from `TransitionRejected` so the 400 can carry the offending
    field: a client that highlights `field` would otherwise point at `status`
    while complaining about `title`.
    """

    def __init__(self, field: str) -> None:
        super().__init__(f"a bulk edit cannot set {field!r}")
        self.field = field


class UnknownProject(ValueError):
    """A `project_id` no registered repo answers to — a 400 naming the field."""

    def __init__(self, project_id: int) -> None:
        super().__init__(f"unknown project: {project_id}")
        self.project_id = project_id


def _find_existing(session, source: str, external_id: Optional[str]):
    """The row a replay would duplicate, or None.

    A NULL `external_id` never matches: the unique constraint does not collide
    on NULLs either, so a hand-typed capture is a new row every time."""
    if not external_id:
        return None
    return session.exec(
        select(AgentTask)
        .where(AgentTask.source == source)
        .where(AgentTask.external_id == external_id)
    ).first()


def _touch(session, row: AgentTask) -> dict:
    row.updated_at = _now()
    session.add(row)
    session.commit()
    session.refresh(row)
    return serialize(row)


def capture_task(*, title: str, source: str, body: Optional[str] = None,
                 external_id: Optional[str] = None, tags=None, links=None,
                 created_by: Optional[int] = None,
                 project_id: Optional[int] = None,
                 parent_id: Optional[int] = None,
                 priority: str = TASK_PRIORITY_NONE) -> tuple[dict, bool]:
    """Insert an idea, or resolve a replay. Returns `(task, created)`.

    `status` is `idea` unconditionally — no caller can set it.
    """
    with SessionLocal() as session:
        existing = _find_existing(session, source, external_id)
        if existing is not None:
            view = _touch(session, existing)
            log.write("task_capture_replayed", task_id=view["id"],
                      source=source, external_id=external_id)
            return view, False

        from lib.tasks import grain
        # Guarded at capture exactly as at edit: the grain rules are about the
        # pair of rows, not about which surface asked, and a capture that
        # parked a run under a run would be a shape no later edit could
        # explain.
        grain.refuse_bad_parentage(session, 0, parent_id)
        row = AgentTask(
            title=title, body=body, status=TASK_STATUS_IDEA, source=source,
            external_id=external_id, tags_json=_encode(tags),
            links_json=_encode(links), created_by=created_by,
            project_id=_resolved_project(session, project_id),
            parent_id=parent_id,
            priority=priority, priority_rank=PRIORITY_ORDER[priority],
        )
        session.add(row)
        try:
            session.commit()
        except IntegrityError:
            # Two concurrent deliveries of the same update: the constraint is
            # the arbiter, and the loser resolves to the winner's row.
            session.rollback()
            existing = _find_existing(session, source, external_id)
            if existing is None:
                raise
            return _touch(session, existing), False
        session.refresh(row)
        view = serialize(row)
    if parent_id is not None:
        # The parent's status follows its children, so a capture that lands
        # under one is a child event like any other.
        from lib.tasks import grain
        grain.recompute_parent(view["id"], event=grain.EVENT_CHILD_ATTACHED)
    log.write("task_captured", task_id=view["id"], source=source,
              external_id=external_id, status=view["status"])
    return view, True


def _archive_refusal(row: AgentTask) -> Optional[TransitionRejected]:
    """Why this task cannot be archived, or None.

    A probe of the run, never a reading of the row — the same rule
    `runs._refuse_unrunnable` applies to launching. `status == 'running'` is a
    claim a runner that died with its server leaves behind, and refusing on
    that claim alone strands a finished task in the list forever with no way
    out. The only thing that blocks filing a task away is an agent still
    reachable, which is stopped first so its work is not orphaned.
    """
    from lib.tasks import runs

    if runs.live_run(row.id) is None:
        return None
    return TransitionRejected(
        row.status, TASK_STATUS_ARCHIVED,
        "the task has a live run — stop it before archiving")


def _claim_refusal(session, row: AgentTask) -> Optional[TransitionRejected]:
    """Why this task cannot be shipped, or None.

    The claim is a statement about a *feature*, and every top-level task is
    one: a task with a parent is a run of something and is refused; a task
    holding runs of its own is the feature those runs built. A childless,
    never-run task IS claimable — that is how a spec-only feature ships.
    """
    if row.parent_id is not None:
        return TransitionRejected(
            row.status, TASK_STATUS_DONE,
            f"task {row.id} is a run of {row.parent_id} — ship the feature it "
            "is a run of, not the run")
    return None


def _moves_anything(row: AgentTask, target: str) -> bool:
    """Whether this target changes the status, the claim, or both.

    `done` on a row already reading `done` is NOT a no-op when the claim is
    absent: that is precisely a task whose own run settled and stamped the
    word, which an operator may now want to actually claim. Returning early on
    the word alone is what made the claim unreachable for such a task.
    """
    if target != row.status:
        return True
    return target == TASK_STATUS_DONE and not row.claimed_at


def _refusal_for(session, row: AgentTask, target: str):
    """Why this move is refused, or None."""
    if target == TASK_STATUS_ARCHIVED:
        return _archive_refusal(row)
    if target == TASK_STATUS_DONE:
        if target != row.status and target not in TASK_TRANSITIONS.get(
                row.status, ()):
            return TransitionRejected(row.status, target)
        return _claim_refusal(session, row)
    if target not in TASK_TRANSITIONS.get(row.status, ()):
        return TransitionRejected(row.status, target)
    return None


def _apply_status(session, row: AgentTask, fields: dict) -> None:
    target = fields.get("status")
    if target is None or not _moves_anything(row, target):
        return
    refusal = _refusal_for(session, row, target)
    if refusal is not None:
        raise refusal
    _apply_claim(row, target)
    row.status = target


def update_task(task_id: int, fields: dict) -> Optional[dict]:
    """Edit an existing task. Returns the updated view, or None if unknown.

    Only the keys present in `fields` are touched, so a caller can archive
    without resending the body. A `status` the lifecycle cannot reach raises
    `TransitionRejected`; nothing else about the row changes.
    """
    with SessionLocal() as session:
        row = session.get(AgentTask, task_id)
        if row is None:
            return None
        _apply_status(session, row, fields)
        if "title" in fields:
            row.title = fields["title"]
        if "body" in fields:
            row.body = fields["body"]
        if "tags" in fields:
            row.tags_json = _encode(fields["tags"])
        if "priority" in fields:
            _set_priority(row, fields["priority"])
        if "project_id" in fields:
            row.project_id = _resolved_project(session, fields["project_id"])
        if "assignee" in fields:
            row.assignee = fields["assignee"]
        view = _touch(session, row)
    # Parentage is a structural claim with its own guards and its own status
    # recompute, so it is written by `grain.set_parent` rather than inline —
    # one writer, whichever surface asked.
    if "parent_id" in fields:
        from lib.tasks import grain
        view = grain.set_parent(task_id, fields["parent_id"]) or view
    _remember_if_a_feature(view)
    log.write("task_updated", task_id=task_id, status=view["status"],
              changed=sorted(fields))
    return view


def _remember_if_a_feature(view: dict) -> None:
    """Warm `issue_cache` after an edit to a top-level task.

    The fifth cache trigger, and the one a child event cannot cover: renaming
    a feature changes what its header says with no run involved. It lives on
    the *write* rather than on a route because the other eight `remember` call
    sites are fetch paths and `regin task ship` bypasses HTTP entirely — a
    route-side trigger would simply not fire for it.

    Best-effort, inside `grain.remember_task`: the edit is the fact.
    """
    if view.get("parent_id") is not None:
        return
    from lib.tasks import grain

    grain.remember_task(view["id"])


def arm_task(task_id: int, *, params: dict, verify_cmd: Optional[str],
             autorun: int) -> Optional[dict]:
    """Write a task's execution parameters and mark it `armed`.

    The only path by which `params_json`, `verify_cmd` or `autorun` are ever
    set — capture and the edit surface carry none of them, which is what keeps
    a captured task inert until an authenticated human arms it.
    """
    with SessionLocal() as session:
        row = session.get(AgentTask, task_id)
        if row is None:
            return None
        from lib.tasks import grain
        refusal = grain.refuse_arming_a_parent(session, task_id)
        if refusal is not None:
            raise TransitionRejected(row.status, TASK_STATUS_ARMED, refusal)
        if row.status not in ARMABLE_FROM:
            raise TransitionRejected(
                row.status, TASK_STATUS_ARMED,
                "a task is armed from 'idea', 'armed', 'done' or 'failed'")
        row.params_json = json.dumps(params)
        row.verify_cmd = verify_cmd
        row.autorun = autorun
        row.status = TASK_STATUS_ARMED
        view = _touch(session, row)
    log.write("task_armed", task_id=task_id, autorun=autorun,
              has_verify=bool(verify_cmd))
    return view


def ship_task(task_id: int) -> Optional[dict]:
    """The done-claim: an operator saying this feature shipped.

    Its own verb rather than a `status` in a PATCH body, because the reach
    matters: `regin task ship` writes the database directly, like `add` and
    `delete` and unlike `finish`. A done-claim releases no dependents, so it
    launches nothing and must keep working with the server stopped — which is
    exactly when someone finishing a feature wants to record it.

    The cache write follows the status write and cannot fail it. The status is
    the fact; the snapshot is a rendering of it, and a rendering that did not
    land is a stale surface rather than an unrecorded ship.
    """
    return update_task(task_id, {"status": TASK_STATUS_DONE})


def _apply_claim(row: AgentTask, target: str) -> None:
    """Record or withdraw the done-claim alongside the status move.

    Written here and nowhere else. `status` is the task lifecycle and the run
    lifecycle writes it freely; the claim is an operator's statement that the
    feature shipped, and the two stopped sharing a column because a run
    settling `done` was reading as a ship nobody made.
    """
    if target == TASK_STATUS_DONE:
        row.claimed_at = _now()
    elif row.claimed_at:
        # Any declared exit from `done` withdraws the claim. It costs no round
        # — nothing was built between claiming and withdrawing — which is what
        # keeps `completions_walked_back` counting restarts and not edits.
        row.claimed_at = None


def set_status(task_id: int, status: str) -> Optional[dict]:
    """Advance a task's *execution* status (`running`, `done`, `failed`).

    Deliberately outside `TASK_TRANSITIONS`: that table governs what a request
    may ask for, while these moves are reported by the run lifecycle, which is
    the only thing that knows them to be true.
    """
    with SessionLocal() as session:
        row = session.get(AgentTask, task_id)
        if row is None:
            return None
        row.status = status
        view = _touch(session, row)
    log.write("task_status_set", task_id=task_id, status=status)
    return view


def _refuse_live_delete(task_id: int) -> None:
    """The same probe `_archive_refusal` makes, for the same reason: a
    `running` row is a claim left behind by a runner that may be gone, so only
    an agent still reachable blocks removal."""
    from lib.tasks import runs

    if runs.live_run(task_id) is not None:
        raise DeleteRefused(
            task_id, "the task has a live run — stop it before deleting")


def _delete_children(session, task_id: int) -> None:
    """Every row keyed to the task, in the transaction that removes it.

    The dep edges go in *both* directions. An edge left behind pointing at a
    deleted blocker makes `blocking()` return an id that no longer resolves,
    and the dependent then waits on it forever with nothing reporting why.
    """
    session.exec(delete(AgentTaskRun).where(AgentTaskRun.task_id == task_id))
    session.exec(
        delete(AgentTaskImage).where(AgentTaskImage.task_id == task_id))
    session.exec(delete(AgentTaskLink).where(AgentTaskLink.task_id == task_id))
    session.exec(delete(AgentTaskDep).where(
        (AgentTaskDep.task_id == task_id)
        | (AgentTaskDep.depends_on_id == task_id)))
    # The comment tables carry no foreign key to the subject — a subject id is
    # opaque to them — so the subject's own delete path is what removes its
    # threads. `comments.thread_id` cascades from there.
    from lib.comments import store as comments
    comments.delete_for_subject(session, "task", str(task_id))


def delete_task(task_id: int) -> Optional[dict]:
    """Remove a task and everything keyed to it. The deleted view, or None.

    Not a status, and not a substitute for archiving: `archived` keeps the row
    — its attempts, its images, its links — and is reversible from the edit
    surface, while this leaves nothing to restore. Unblocked dependents are
    deliberately not fired here; launching agents as a side effect of a
    deletion is not what the operator asked for.
    """
    with SessionLocal() as session:
        row = session.get(AgentTask, task_id)
        if row is None:
            return None
        _refuse_live_delete(task_id)
        view = serialize(row)
        _delete_children(session, task_id)
        session.delete(row)
        session.commit()
    log.write("task_deleted", task_id=task_id, status=view["status"])
    return view


def bulk_delete(task_ids: list[int]) -> dict:
    """Delete many tasks, reporting per-row what happened.

    Partial success, exactly as `bulk_update`: a selection holding one live
    task deletes the rest and names the one it could not.
    """
    deleted: list[int] = []
    rejected: list[dict] = []
    for task_id in task_ids[:BULK_MAX]:
        try:
            view = delete_task(task_id)
        except DeleteRefused as exc:
            rejected.append({"id": task_id, "reason": str(exc)})
            continue
        if view is None:
            rejected.append({"id": task_id, "reason": "unknown task"})
        else:
            deleted.append(task_id)
    log.write("task_bulk_deleted", count=len(deleted), rejected=len(rejected))
    return {"deleted": deleted, "rejected": rejected}


def get_task(task_id: int) -> Optional[dict]:
    with SessionLocal() as session:
        row = session.get(AgentTask, task_id)
        if row is None:
            return None
        view = serialize(row)
    log.read("task_read", task_id=task_id)
    return view


def list_tasks(status: Optional[str] = None, limit: int = 50) -> list[dict]:
    """Tasks newest-first, optionally filtered by status."""
    with SessionLocal() as session:
        stmt = select(AgentTask).order_by(AgentTask.id.desc())
        if status:
            stmt = stmt.where(AgentTask.status == status)
        rows = session.exec(stmt.limit(max(1, limit))).all()
        views = [serialize(row) for row in rows]
    log.read("task_list", count=len(views), status=status)
    return views


# What a bulk action may set. Deliberately a subset of the single-task edit
# surface: title and body are per-task text, and nothing about selecting fifty
# rows makes overwriting fifty titles a sensible request.
BULK_FIELDS = frozenset({"status", "priority", "project_id"})

BULK_MAX = 500


def bulk_update(task_ids: list[int], fields: dict) -> dict:
    """Apply `fields` to many tasks, reporting what each one did.

    A row the lifecycle refuses is *skipped and named*, not fatal: a selection
    of forty ideas and two running tasks should archive the forty rather than
    failing whole because two cannot move. The caller renders `rejected` so the
    operator sees which rows did not follow.
    """
    unknown_fields = sorted(set(fields) - BULK_FIELDS)
    if unknown_fields:
        raise BulkFieldRejected(unknown_fields[0])
    changed: list[int] = []
    rejected: list[dict] = []
    with SessionLocal() as session:
        for task_id in task_ids[:BULK_MAX]:
            row = session.get(AgentTask, task_id)
            if row is None:
                rejected.append({"id": task_id, "reason": "unknown task"})
                continue
            try:
                _apply_bulk_fields(session, row, fields)
            except (TransitionRejected, UnknownProject) as exc:
                rejected.append({"id": task_id, "reason": str(exc)})
                continue
            row.updated_at = _now()
            session.add(row)
            changed.append(task_id)
        session.commit()
    log.write("task_bulk_updated", count=len(changed),
              rejected=len(rejected), changed=sorted(fields))
    return {"changed": changed, "rejected": rejected}


def _apply_bulk_fields(session, row: AgentTask, fields: dict) -> None:
    _apply_status(session, row, fields)
    if "priority" in fields:
        _set_priority(row, fields["priority"])
    if "project_id" in fields:
        row.project_id = _resolved_project(session, fields["project_id"])
