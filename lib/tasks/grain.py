"""The feature grain — which tasks are features, and which are runs.

A task with `parent_id IS NULL` that something names as parent **is** a
feature. Its children are the runs. The two grains stay distinct without a
second entity, and they stay *disjoint* because three symmetric guards refuse
at the act rather than at read:

  * `arm_task` refuses on a task that has children — a parent holds no runs;
  * attaching a child refuses on a task that has runs — a run is never a
    parent;
  * a dep edge between a parent and its own child is refused — "waits for" and
    "is a run of" are two axes, and one pair cannot carry both.

**Depth is exactly two**, enforced here at write time: a task may take a parent
only if that parent's own `parent_id IS NULL`. That makes acyclicity free — a
cycle needs a child that is also a parent, and the rule forbids one — so no
reader below carries a visited-guard, the same posture `lib/tasks/deps.py`
takes for its own graph.

**The parent's status is written by child events and recomputed from the
children, never bumped.** The recompute reads the parent's status *first*,
because two statuses are claims no child may overwrite: `archived` is a filing
decision and is terminal against every trigger, and a `done` is an operator's
ship claim, which exactly one edge walks back.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from sqlmodel import select

from lib.activity_log import get_activity_logger
from lib.orm import SessionLocal
from lib.orm.models.tasks import (
    AgentTask, AgentTaskRun, RUN_OPEN_STATUSES, RUN_STATUS_DONE,
    TASK_STATUS_ARCHIVED, TASK_STATUS_DONE, TASK_STATUS_FAILED,
    TASK_STATUS_IDEA, TASK_STATUS_RUNNING,
)
from lib.tasks.query import like_pattern

log = get_activity_logger("tasks")

#: A child run started. The one event that may walk a ship claim back.
EVENT_RUN_STARTED = "run_started"
#: A child run settled. Says *this session finished*, never *this shipped*.
EVENT_RUN_SETTLED = "run_settled"
#: A child gained or lost its parent.
EVENT_CHILD_ATTACHED = "child_attached"
EVENT_CHILD_DETACHED = "child_detached"


class GrainRejected(ValueError):
    """A parentage the two grains do not admit — a 400 naming the pair."""

    def __init__(self, message: str, task_id: int,
                 parent_id: Optional[int] = None) -> None:
        super().__init__(message)
        self.task_id = task_id
        self.parent_id = parent_id


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


# ── the guards ───────────────────────────────────────────────

def _has_children(session, task_id: int) -> bool:
    return session.exec(
        select(AgentTask.id).where(AgentTask.parent_id == task_id)).first() \
        is not None


def _has_runs(session, task_id: int) -> bool:
    return session.exec(
        select(AgentTaskRun.id).where(AgentTaskRun.task_id == task_id)
    ).first() is not None


def _holds_a_link(session, task_id: int) -> bool:
    """Whether a task already points at an issue in another tracker.

    A task holding one is already a feature in that tracker's terms, so making
    it a parent would give one task two feature keys — the thing `feature_key`
    being identity forbids. Refused at the act, like every other grain rule.
    """
    from lib.orm.models.tasks import AgentTaskLink  # noqa: PLC0415

    return session.exec(
        select(AgentTaskLink.id).where(AgentTaskLink.task_id == task_id)
    ).first() is not None


def refuse_bad_parentage(session, task_id: int,
                         parent_id: Optional[int]) -> None:
    """Why `task_id` cannot take `parent_id`, or nothing.

    Every refusal is about the *pair*, so the caller can name both rows. The
    order is the order an operator needs to hear it: a self-edge first, then
    the row that does not exist, then the two grain rules.
    """
    if parent_id is None:
        return
    if parent_id == task_id:
        raise GrainRejected(
            f"a task cannot be its own run ({task_id} → {parent_id})",
            task_id, parent_id)
    parent = session.get(AgentTask, parent_id)
    if parent is None:
        raise GrainRejected(f"unknown task {parent_id}", task_id, parent_id)
    if parent.parent_id is not None:
        raise GrainRejected(
            f"task {parent_id} is already a run of {parent.parent_id} — the "
            "grain is exactly two deep, so a run cannot hold runs",
            task_id, parent_id)
    if _has_runs(session, parent_id):
        raise GrainRejected(
            f"task {parent_id} holds runs of its own, so it is a run and not "
            "a feature — a feature holds no runs, ever",
            task_id, parent_id)
    if _holds_a_link(session, parent_id):
        raise GrainRejected(
            f"task {parent_id} already links an issue, so it is already a "
            "feature in that tracker — one task cannot hold two feature keys",
            task_id, parent_id)
    if _has_children(session, task_id):
        raise GrainRejected(
            f"task {task_id} is a feature with runs of its own, so it cannot "
            "also be a run",
            task_id, parent_id)


#: How many recent rows one candidate read considers. A picker is a way in to
#: a long backlog, and that narrowing is deliberately NOT a legality rule —
#: everything the caller is offered still passed `refuse_bad_parentage`.
CANDIDATE_SCAN = 300
CANDIDATE_LIMIT = 50


def _accepts(session, task_id: int, parent_id: int) -> bool:
    try:
        refuse_bad_parentage(session, task_id, parent_id)
    except GrainRejected:
        return False
    return True


def _scan(session, q: str):
    stmt = select(AgentTask).where(AgentTask.status != TASK_STATUS_ARCHIVED)
    if q:
        stmt = stmt.where(AgentTask.title.like(like_pattern(q), escape="\\"))
    return session.exec(
        stmt.order_by(AgentTask.updated_at.desc(), AgentTask.id.desc())
        .limit(CANDIDATE_SCAN)).all()


def candidates(*, child_id: Optional[int] = None,
               parent_id: Optional[int] = None,
               q: str = "", limit: int = CANDIDATE_LIMIT) -> dict:
    """The rows the grain would accept on the other side of one parentage.

    Exactly one side is named. `child_id` asks who may be *this task's* parent
    — `0` for a task the composer has not captured yet, the same id
    `capture_task` checks under. `parent_id` asks who may become a run OF this
    task, which is the detail page's Runs card standing on the other side.

    Every row is decided by `refuse_bad_parentage`, never by a rule spelled
    again here: a list computed by a second copy of the rule is a list that
    drifts from the one refusal that is authority.
    """
    asking_for_parents = child_id is not None
    items = []
    with SessionLocal() as session:
        rows = _scan(session, q)
        for row in rows:
            pair = ((child_id, row.id) if asking_for_parents
                    else (row.id, parent_id))
            if not _accepts(session, *pair):
                continue
            items.append({"id": row.id, "title": row.title,
                          "status": row.status, "parent_id": row.parent_id})
            if len(items) >= limit:
                break
    return {"items": items, "scanned": len(rows), "scan_limit": CANDIDATE_SCAN}


def refuse_arming_a_parent(session, task_id: int) -> Optional[str]:
    """Why this task cannot be armed, or None. A parent holds no runs."""
    if _has_children(session, task_id):
        return ("the task is a feature with runs of its own — arm one of its "
                "runs instead")
    return None


# ── writing parentage ────────────────────────────────────────

def set_parent(task_id: int, parent_id: Optional[int]) -> Optional[dict]:
    """Attach or detach a run, recomputing both parents in the transaction.

    Both, because a move has two sides: the parent losing the child and the
    one gaining it each have a different set of children afterwards, and
    recomputing only the new one would leave the old reading a status its
    children no longer support.
    """
    with SessionLocal() as session:
        row = session.get(AgentTask, task_id)
        if row is None:
            return None
        previous = row.parent_id
        if previous == parent_id:
            return _view(row)
        refuse_bad_parentage(session, task_id, parent_id)
        row.parent_id = parent_id
        row.updated_at = _now()
        session.add(row)
        # Flushed before the recompute reads back: both parents' statuses are
        # computed from a `parent_id` query, and an unflushed change would let
        # each of them see the parentage as it was a moment ago.
        session.flush()
        for affected, event in ((previous, EVENT_CHILD_DETACHED),
                                (parent_id, EVENT_CHILD_ATTACHED)):
            if affected is not None:
                _recompute(session, affected, event)
        session.commit()
        session.refresh(row)
        view = _view(row)
    log.write("task_parent_set", task_id=task_id, parent_id=parent_id,
              previous=previous)
    _remember_affected([previous, parent_id])
    return view


def _view(row: AgentTask) -> dict:
    from lib.tasks import store  # noqa: PLC0415  (circular at import time)

    return store.serialize(row)


# ── the parent's status ──────────────────────────────────────

def recompute_parent(child_id: int, *, event: str = EVENT_RUN_SETTLED) -> None:
    """Rewrite the parent of `child_id` from its children. A no-op with none.

    Called by the run lifecycle rather than derived at read: a status is a
    projection of current state and can be recomputed, but the walked-back
    completion this may mint is an *event* and is not recoverable from current
    state — so it has to be written when it happens.
    """
    with SessionLocal() as session:
        child = session.get(AgentTask, child_id)
        if child is None or child.parent_id is None:
            return
        parent_id = child.parent_id
        _recompute(session, parent_id, event)
        session.commit()
    _remember_affected([parent_id])


def walk_back_own_claim(task_id: int) -> None:
    """A top-level task's own run starting over its ship claim.

    The same edge `_walk_back_if_restarted` counts for a child run, applied
    to a feature whose runs are its own attempts: work beginning again is the
    statement that it is no longer shipped, and the round it opens has to be
    written when it happens. A no-op on an unclaimed row and on a run of
    something (its parent is recomputed by the caller instead).
    """
    with SessionLocal() as session:
        row = session.get(AgentTask, task_id)
        if row is None or row.parent_id is not None or not row.claimed_at:
            return
        _walk_back_if_restarted(session, row, EVENT_RUN_STARTED)
        session.commit()
    _remember_affected([task_id])


def _recompute(session, parent_id: int, event: str) -> None:
    """The parent's status, from its status first and its children second."""
    parent = session.get(AgentTask, parent_id)
    if parent is None or parent.status == TASK_STATUS_ARCHIVED:
        return
    # A CLAIMED feature is the thing no child event may recompute away — not
    # a task that merely reads `done`, which is what its own run settling
    # leaves behind and says nothing about the feature.
    if parent.claimed_at:
        _walk_back_if_restarted(session, parent, event)
        return
    _write_status(session, parent, _status_from_children(session, parent_id))


def _walk_back_if_restarted(session, parent: AgentTask, event: str) -> None:
    """A claimed `done` moves on exactly one edge — a child run starting.

    Which is precisely what work restarting after a ship claim *is*. A run
    settling, a child attaching and a child detaching all no-op: none of them
    is work beginning again, and letting any of them move the row would make
    the count mean something a reader could not name.
    """
    if event != EVENT_RUN_STARTED:
        return
    parent.status = TASK_STATUS_RUNNING
    # The claim is withdrawn by the restart itself: work beginning again IS
    # the statement that the feature is no longer shipped, and leaving the
    # record would let a feature read `completed` while it is being rebuilt.
    parent.claimed_at = None
    parent.completions_walked_back += 1
    parent.updated_at = _now()
    session.add(parent)
    log.write("task_completion_walked_back", task_id=parent.id,
              completions_walked_back=parent.completions_walked_back)


def _write_status(session, parent: AgentTask, status: str) -> None:
    if parent.status == status:
        return
    parent.status = status
    parent.updated_at = _now()
    session.add(parent)


def _status_from_children(session, parent_id: int) -> str:
    """What the children say the parent is.

    A child that has never run is **not settled**, which is what keeps a parent
    with unstarted work out of `running`-by-completion and stops it ever
    reading as shipped.

    All settled with at least one `done` writes `running` — built, awaiting the
    claim — rather than no-opping, because attaching an already-run child to a
    fresh parent is the common adoption path and a no-op would strand such a
    parent at `idea` with finished work hanging off it. `running` on a parent
    is not a claim about a live process: a parent is never armed and holds no
    runs.
    """
    runs = session.exec(
        select(AgentTaskRun.task_id, AgentTaskRun.status)
        .join(AgentTask, AgentTask.id == AgentTaskRun.task_id)
        .where(AgentTask.parent_id == parent_id)).all()
    if not runs:
        return TASK_STATUS_IDEA
    if any(status in RUN_OPEN_STATUSES for _, status in runs):
        return TASK_STATUS_RUNNING
    if _has_unstarted_child(session, parent_id, {task for task, _ in runs}):
        return TASK_STATUS_RUNNING if _any_done(runs) else TASK_STATUS_IDEA
    return TASK_STATUS_RUNNING if _any_done(runs) else TASK_STATUS_FAILED


def _any_done(runs) -> bool:
    return any(status == RUN_STATUS_DONE for _, status in runs)


def _has_unstarted_child(session, parent_id: int, ran: set) -> bool:
    children = session.exec(
        select(AgentTask.id).where(AgentTask.parent_id == parent_id)).all()
    return any(child_id not in ran for child_id in children)


def counts_for(task_id: int) -> dict:
    """`{children, runs}` for one task — which grain it is, in two numbers.

    Read on the DETAIL route only, never in `serialize`: the feed renders 50
    rows at a time, and two counts per row is the N+1 the keyset cursor exists
    to avoid. The detail page needs them because the grain decides what it
    renders — a feature holds no runs, and only a feature is claimable — and
    asking by *listing* the children instead would make a page with an address
    resolve it by scanning, which is the thing that route refuses.
    """
    with SessionLocal() as session:
        children = len(session.exec(
            select(AgentTask.id)
            .where(AgentTask.parent_id == task_id)).all())
        runs = len(session.exec(
            select(AgentTaskRun.id)
            .where(AgentTaskRun.task_id == task_id)).all())
    return {"children": children, "runs": runs}


# ── the cache write ──────────────────────────────────────────

def _remember_affected(task_ids) -> None:
    """Warm `issue_cache` for every parent a child event just moved.

    Through the generic `resolve` → `fetch_issue` path, so there is no new
    trigger and no provider branch: the same write any other tracker's fetch
    makes, from the event that made the answer change.

    Best-effort, and it cannot fail the status write that preceded it — the
    status is the fact, and the cache is a rendering of it.
    """
    for task_id in {t for t in task_ids if t}:
        remember_task(task_id)


def remember_task(task_id: int) -> None:
    """Write one task's issue snapshot to the shared cache. Never raises."""
    from lib.tasks import issue_cache  # noqa: PLC0415  (circular at import)
    from lib.tasks.integrations.regin import key_for_task  # noqa: PLC0415

    try:
        from lib.defects import tracker  # noqa: PLC0415
        provider, ref = tracker.resolve(key_for_task(task_id))
        issue_cache.remember(ref.provider, ref.key, provider.fetch_issue(ref))
    except Exception:  # noqa: BLE001 — a cache write cannot sink a status write
        log.error("task_issue_cache_write_failed", task_id=task_id,
                  exc_info=True)
