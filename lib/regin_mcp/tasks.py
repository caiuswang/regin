"""Task tools: capture, read, list, edit, wire dependencies, comment, and
report a run finished.

Edits go through `capture.parse_update` + `store.update_task` and the
dependency write through `deps.parse_deps` + `deps.set_deps` — the functions
`PATCH /api/tasks/<id>` and `PUT …/deps` call — so the two surfaces cannot
validate one edit two ways.
"""

from __future__ import annotations

from typing import Literal, Optional

from lib.regin_mcp import _common, images

EditStatus = Literal["idea", "archived"]
FinishStatus = Literal["done", "failed"]


def _query_args(status: str, feature_id: int, project: str,
                query: str) -> dict:
    args = {"status": status, "q": query,
            "include_archived": "" if status else "1"}
    if feature_id:
        _common.feature(feature_id)
        args["feature_id"] = str(feature_id)
    pid = _common.project_id(project)
    if pid is not None:
        args["project_id"] = str(pid)
    return args


def task_list(status: str = "idea", feature_id: int = 0, project: str = "",
              query: str = "", limit: int = 50) -> dict:
    """Tasks newest first, under the same filters the web task feed applies.

    Args:
        status: idea, armed, running, done, failed, archived …; empty for
            every status.
        feature_id: Only this feature's tasks; 0 for any.
        project: Registered project name; empty for any.
        query: Free-text match on title and body.
        limit: Max rows.
    """
    from sqlmodel import select
    from lib.orm import SessionLocal
    from lib.orm.models.tasks import AgentTask
    from lib.tasks import query as task_query, store
    try:
        filters = task_query.parse_filters(
            _query_args(status, feature_id, project, query))
    except task_query.FilterRejected as exc:
        raise _common.refused(exc) from None
    stmt = task_query.ordered(task_query.apply_filters(select(AgentTask),
                                                       filters),
                              filters["sort"]).limit(max(1, limit))
    with SessionLocal() as session:
        rows = [store.serialize(row) for row in session.exec(stmt).all()]
    return {"tasks": rows}


def task_get(task_id: int) -> dict:
    """One task in full, with its image metadata (bytes via `image_get`), the
    tasks it waits on, and its comment threads.

    Args:
        task_id: The task's id.
    """
    from lib.comments import store as comments
    from lib.tasks import deps
    from lib.tasks import images as task_images
    task = _common.task(task_id)
    return {**task, "images": task_images.list_for(task_id),
            "deps": [{"id": e.id, "kind": e.kind}
                     for e in deps.dep_edges(task_id)],
            "comments": comments.thread_with_comments(
                comments.TASK_SUBJECT, str(task_id))}


def task_create(title: str, body: str = "", project: str = "",
                feature_id: int = 0, priority: str = "none",
                kind: str = "task",
                image_paths: Optional[list[str]] = None) -> dict:
    """Capture a task, optionally as a step of a feature.

    Args:
        title: One-line title (≤200 chars).
        body: Longer description.
        project: Registered project name; with `feature_id` set it defaults to
            the feature's project.
        feature_id: The feature this task is a step of; 0 for none.
        priority: none / low / medium / high / urgent.
        kind: What the step is — task, spec, build or fix.
        image_paths: Local image files to attach; each one's `![](image:N)`
            is appended to the body.
    """
    from lib.orm.models.tasks import TASK_KINDS, TASK_PRIORITIES
    from lib.tasks import store
    if not title.strip():
        raise ValueError("a task needs a title")
    if priority not in TASK_PRIORITIES:
        raise ValueError(f"unknown priority {priority!r}. One of: {', '.join(TASK_PRIORITIES)}")
    if kind not in TASK_KINDS:
        raise ValueError(f"unknown kind {kind!r}. One of: {', '.join(TASK_KINDS)}")
    extra = {}
    if feature_id:
        _common.feature(feature_id)
        extra["feature_id"] = feature_id
    if project:
        extra["project_id"] = _common.project_id(project)
    blobs = images.read_paths("task", image_paths)
    task, _ = store.capture_task(title=title[:200], body=body or None,
                                 source="mcp", priority=priority, kind=kind,
                                 **extra)
    if blobs:
        try:
            attached = images.attach_all("task", task["id"], blobs)
            task = {**_common.task(task["id"]), "images": attached}
        except Exception:
            store.delete_task(task["id"])
            raise
    return task


def _update_payload(title, body, status, priority, kind, feature_id,
                    project) -> dict:
    given = {"title": title, "body": body, "status": status,
             "priority": priority, "kind": kind}
    payload = {k: v for k, v in given.items() if v is not None}
    if feature_id is not None:
        payload["feature_id"] = feature_id or None
    if project is not None:
        payload["project_id"] = _common.project_id(project)
    return payload


def task_update(task_id: int, title: Optional[str] = None,
                body: Optional[str] = None,
                status: Optional[EditStatus] = None,
                priority: Optional[str] = None, kind: Optional[str] = None,
                feature_id: Optional[int] = None,
                project: Optional[str] = None) -> dict:
    """Edit a task's text, priority, kind, feature or project, or move it
    between idea and archived. An omitted field is left alone.

    Args:
        task_id: The task's id.
        title: New title.
        body: New body (replaces the old one).
        status: idea or archived.
        priority: none / low / medium / high / urgent.
        kind: task, spec, build or fix.
        feature_id: Move it under this feature; 0 detaches it.
        project: Registered project name; "" unfiles it.
    """
    from lib.features.store import FeatureError
    from lib.tasks import capture, store
    _common.task(task_id)
    try:
        fields = capture.parse_update(_update_payload(
            title, body, status, priority, kind, feature_id, project))
        store.update_task(task_id, fields)
    except capture.CaptureRejected as exc:
        raise _common.refused(exc) from None
    except store.TransitionRejected as exc:
        raise _common.refused(exc, "status") from None
    except store.UnknownProject as exc:
        raise _common.refused(exc, "project") from None
    except FeatureError as exc:
        raise _common.refused(exc, "feature_id") from None
    return task_get(task_id)


def _release(task_id: int) -> None:
    """Fire a task the dependency write may have released.

    `in_server=False`: an SDK child launched from this stdio process would die
    with it, so such a task is marked deferred for the server's next fire —
    the rule the CLI's no-server path already follows.
    """
    from lib.activity_log import get_activity_logger
    from lib.tasks import runs
    try:
        runs.fire_if_ready(task_id, in_server=False)
    except Exception:  # noqa: BLE001 — a launch fault is not a failed write
        get_activity_logger("tasks").error("task_fire_failed",
                                           task_id=task_id, exc_info=True)


def task_set_deps(task_id: int, blockers: list[int]) -> dict:
    """Replace the set of tasks this one waits on. A self-edge or a cycle is
    refused, naming the pair.

    Args:
        task_id: The waiting task.
        blockers: Ids of the tasks it waits on; [] clears them.
    """
    from lib.tasks import capture, deps
    _common.task(task_id)
    try:
        stored = deps.set_deps(task_id,
                               deps.parse_deps({"depends_on": blockers}))
    except capture.CaptureRejected as exc:
        raise _common.refused(exc, "blockers") from None
    except deps.DepRejected as exc:
        raise ValueError(f"blockers: {exc} (pair: {list(exc.pair)})") from None
    if stored is None:
        raise ValueError(f"no task #{task_id}")
    _release(task_id)
    edges = deps.dep_edges(task_id)
    return {"task_id": task_id, "depends_on": [e.id for e in edges],
            "deps": [{"id": e.id, "kind": e.kind} for e in edges]}


def _resolve_thread(thread_id: int, resolve: bool) -> dict:
    from lib.comments import store as comments
    from lib.orm.models.comments import RESOLUTION_OPEN, RESOLUTION_RESOLVED
    return comments.resolve(thread_id, RESOLUTION_RESOLVED if resolve
                            else RESOLUTION_OPEN)


def task_comment(task_id: int, body: str = "", thread_id: Optional[int] = None,
                 resolve: Optional[bool] = None) -> dict:
    """Comment on a task, as an agent. Opens a new thread unless `thread_id`
    names one of this task's threads.

    Args:
        task_id: The task's id.
        body: The comment. May be empty only when resolving a named thread.
        thread_id: Reply in this thread instead of opening one.
        resolve: True marks the thread resolved, False reopens it; omit to
            leave it as is.
    """
    from lib.comments import store as comments
    from lib.orm.models.comments import AUTHOR_AGENT
    _common.task(task_id)
    if not body.strip() and (resolve is None or thread_id is None):
        raise ValueError("body: a comment needs a body")
    thread = comments.thread_for(comments.TASK_SUBJECT, str(task_id),
                                 thread_id)
    if thread is None:
        raise ValueError(f"thread_id: task #{task_id} has no thread {thread_id}")
    try:
        comment = (comments.add_comment(thread["id"], body=body,
                                        author_kind=AUTHOR_AGENT)
                   if body.strip() else None)
        if resolve is not None:
            _resolve_thread(thread["id"], resolve)
    except comments.CommentRejected as exc:
        raise _common.refused(exc) from None
    return {"thread": {**comments.get_thread(thread["id"]),
                       "comments": comments.comments_for(thread["id"])},
            "comment": comment}


def task_finish(run_id: int, status: FinishStatus, reason: str,
                task_id: Optional[int] = None) -> dict:
    """Report that a task run's work is finished — the same report, over the
    same path, as `regin task finish <task> --run <run>`: to the running
    regin server, or settled here when none is up. Settles the attempt, posts
    the reason on the task, and releases its dependents.

    A done report on a fix task whose defects are still open is refused,
    naming them.

    Args:
        run_id: The run (attempt) id.
        status: done or failed.
        reason: What the run did and why — posted on the task as its report.
        task_id: The task you believe the run belongs to; a mismatch is
            refused rather than settled.
    """
    from lib.tasks import finish_report, runs
    wanted = {"done": runs.RUN_STATUS_DONE,
              "failed": runs.RUN_STATUS_FAILED}[status]
    try:
        result = finish_report.report_finish(
            task_id, run_id, wanted, reason,
            opener=finish_report.open_loopback)
    except finish_report.ReportRefused as exc:
        blocking = (f" (open_defects: {exc.open_defects})"
                    if exc.open_defects else "")
        raise ValueError(f"{exc}{blocking}") from None
    if result.get("task_id") is None:
        raise ValueError(f"no run #{run_id}")
    return result


TOOLS = (task_list, task_get, task_create, task_update, task_set_deps,
         task_comment, task_finish)
