"""Task capture — HTTP surface (anywhere → an `agent_tasks` idea row).

Two principals, two surfaces, one store.

`api_capture_task`'s auth is the `require_scope('task:capture')` decorator
ALONE: that endpoint joins the app's PUBLIC_API_ENDPOINTS allowlist (the JWT
gate returns without auth for it), so the scope check — resolving an API key,
a principal entirely separate from the web-UI JWT — is the sole guard. It
fails closed: a disabled capture surface 404s, and any missing / unknown /
revoked key 401s, while a real key without the scope 403s.

The browser routes below (`api_create_task`, `api_list_tasks`,
`api_update_task`) are the deliberate inverse: NOT in PUBLIC_API_ENDPOINTS, so
their auth is the app-wide JWT gate plus `require_auth`/`require_editor`. They
call the same in-process store, which is what keeps the capture key — a
machine credential — out of the page. An `rgn_…` bearer reaches none of them:
nothing on the `require_scope` path produces a user, so it 401s here exactly
as it does on every other user-gated route.

No *capture* route can start an agent. None accepts `cwd`, `model` or
`permission_mode` — an unknown field is a 400 naming it — and creation stamps
`status='idea'` unconditionally, so the worst a leaked capture key can do is
add backlog rows.

Images are the one capture field that is not part of the JSON body. They are
uploaded one at a time against a task that already exists (`POST
/api/tasks/<id>/images`), so the composer creates the row first and then
flushes what was pasted into it: nothing has to be staged server-side for a
task that may never be saved, and there is no orphan to sweep. Writes are
`require_editor`; the bytes are readable by any authenticated user, like every
other read here.

Arming is the privileged step. `POST /api/tasks/<id>/arm` is the only route
that may write an execution parameter, and it — like run / stop / interrupt /
deps / graph — is `require_editor` and absent from PUBLIC_API_ENDPOINTS. The
execution routes themselves are a thin join: they delegate to
`supervisor.launch_run` and `registry.stop_run` / `interrupt_run` rather than
carrying a second lifecycle of their own.
"""

from __future__ import annotations

import base64
import binascii

from flask import Blueprint, Response, g, jsonify, request
from sqlmodel import func, select

from lib.activity_log import get_activity_logger
from lib.comments import store as comments
from lib.auth import get_current_user, require_auth, require_editor, require_scope
from lib.orm import SessionLocal
from lib.orm.models.api_keys import SCOPE_TASK_CAPTURE
from lib.agent_sdk import worktree
from lib.orm.models.tasks import (
    AgentTask, AgentTaskRun, RUN_STATUS_ARMED,
)
from lib.settings import settings
from lib.tasks import (arm, capture, deps, images, query,
                       ratelimit, runs, store)
from lib.tasks import integrations
from lib.utils.pagination import clamp_size, keyset_page_stmt

tasks_bp = Blueprint('tasks', __name__)

log = get_activity_logger("tasks")

WEB_SOURCE = "web"

#: The one `subject_kind` regin writes today. The tables are polymorphic; the
#: surface is widened exactly as far as tasks need.
COMMENT_SUBJECT = "task"


@tasks_bp.route('/api/tasks/capture', methods=['POST'])
@require_scope(SCOPE_TASK_CAPTURE)
def api_capture_task():
    """Record an idea. 201 when created, 200 when the replay resolved."""
    if not settings.tasks.capture_enabled:
        return jsonify({"error": "not found"}), 404
    key = g.api_key
    if not ratelimit.capture_allowed(key["id"]):
        log.warn("capture_rate_limited", api_key_id=key["id"])
        return jsonify({"error": "rate limit exceeded"}), 429
    try:
        fields = capture.parse_capture(request.get_json(silent=True))
    except capture.CaptureRejected as exc:
        log.error("capture_rejected", api_key_id=key["id"], field=exc.field,
                  detail=str(exc))
        return jsonify({"error": str(exc), "field": exc.field}), 400
    try:
        task, created = store.capture_task(**fields)
    except store.UnknownProject as exc:
        return jsonify({"error": str(exc), "field": "project_id"}), 400
    return jsonify({"id": task["id"], "created": created}), 201 if created else 200


def _rejected(exc: capture.CaptureRejected):
    return jsonify({"error": str(exc), "field": exc.field}), 400


def _with_images(tasks: list[dict]) -> list[dict]:
    """Hang each task's image metadata off its payload.

    One query for the whole page rather than one per row: the table holds
    BLOBs, and an N+1 over it is the difference between a list read and a
    download. The bytes themselves are never inlined — a thumbnail is its own
    GET.
    """
    by_task = images.summaries_for(task["id"] for task in tasks)
    for task in tasks:
        task["images"] = by_task.get(task["id"], [])
    return tasks


def _one_with_images(task: dict) -> dict:
    return _with_images([task])[0]


def _parse_web_capture(payload):
    """Capture validation, minus the fields the server owns.

    `source` is stamped, not accepted — a browser task is a `web` task by
    definition — and a request that tries to say otherwise is told so rather
    than having its value silently overwritten.
    """
    if isinstance(payload, dict):
        for field in ("source", "external_id"):
            if field in payload:
                raise capture.CaptureRejected(
                    f"{field} is set by the server", field)
        payload = {**payload, "source": WEB_SOURCE}
    return capture.parse_capture(payload)


@tasks_bp.route('/api/tasks', methods=['POST'])
@require_editor
def api_create_task():
    """Capture an idea from the dashboard. Always lands as `status='idea'`."""
    try:
        fields = _parse_web_capture(request.get_json(silent=True))
    except capture.CaptureRejected as exc:
        log.error("task_create_rejected", field=exc.field, detail=str(exc))
        return _rejected(exc)
    user = get_current_user()
    try:
        task, _ = store.capture_task(created_by=user["id"], **fields)
    except store.UnknownProject as exc:
        return jsonify({"error": str(exc), "field": "project_id"}), 400
    return jsonify({"task": _one_with_images(task)}), 201


def _rejected_filter(exc: query.FilterRejected):
    return jsonify({"error": str(exc), "field": exc.field}), 400


@tasks_bp.route('/api/tasks', methods=['GET'])
@require_auth
def api_list_tasks():
    """One keyset page of the task feed under the current filters.

    Every filter is a WHERE clause here rather than an `Array.filter` in the
    page: a client-side filter over one 50-row page answers a different
    question than the control claims to ask, and the counts beside it would
    then describe the page instead of the backlog. `counts` rides the first
    page so the status chips are true totals without a second round trip.
    """
    try:
        filters = query.parse_filters(request.args)
    except query.FilterRejected as exc:
        return _rejected_filter(exc)
    size = clamp_size(request.args.get("size"))
    cursor = request.args.get("cursor")
    with SessionLocal() as session:
        stmt = query.apply_filters(select(AgentTask), filters)
        page = keyset_page_stmt(
            session, stmt,
            order_cols=query.ORDER_COLS[filters["sort"]],
            cursor_token=cursor, size=size,
            row_to_dict=store.serialize,
        )
        # Only on the first page: `loadMore` appends rows, and the totals
        # describe the whole filtered set, not the slice just fetched.
        totals = None if cursor else query.counts(session, filters)
    envelope = page.to_envelope()
    _with_images(envelope["items"])
    _with_run_counts(envelope["items"])
    envelope["status_filter"] = filters["status"]
    if totals is not None:
        envelope["counts"] = totals
    return jsonify(envelope)


@tasks_bp.route('/api/tasks/counts', methods=['GET'])
@require_auth
def api_task_counts():
    """Per-status totals under every filter except status.

    Deliberately status-blind: each chip must report how many tasks it *would*
    show, so computing it inside the status filter would make every chip read
    the size of its own slice.
    """
    try:
        filters = query.parse_filters(request.args)
    except query.FilterRejected as exc:
        return _rejected_filter(exc)
    with SessionLocal() as session:
        return jsonify(query.counts(session, filters))


def _bulk_ids(payload) -> list[int]:
    """The `ids` of a bulk body. Raises `CaptureRejected` naming the field."""
    if not isinstance(payload, dict):
        raise capture.CaptureRejected("body must be a JSON object")
    ids = payload.get("ids")
    if not isinstance(ids, list) or not all(
            isinstance(i, int) and not isinstance(i, bool) for i in ids):
        raise capture.CaptureRejected("ids must be a list of task ids", "ids")
    return ids


@tasks_bp.route('/api/tasks/bulk', methods=['POST'])
@require_editor
def api_bulk_update_tasks():
    """Apply one edit to many tasks, reporting per-row what happened.

    A row the lifecycle refuses is named in `rejected` rather than failing the
    whole request: a selection of forty ideas and two running tasks should
    archive the forty.
    """
    payload = request.get_json(silent=True)
    try:
        ids = _bulk_ids(payload)
        fields = capture.parse_update({k: v for k, v in payload.items()
                                       if k != "ids"})
    except capture.CaptureRejected as exc:
        return _rejected(exc)
    try:
        result = store.bulk_update(ids, fields)
    except store.BulkFieldRejected as exc:
        return jsonify({"error": str(exc), "field": exc.field}), 400
    return jsonify(result)


def _with_run_counts(items: list[dict]) -> None:
    """`run_count` and the pending attempt per row, two grouped queries.

    On the page rather than in `serialize`: a read per row is the N+1 the
    keyset cursor exists to avoid, and a query over the page's ids is not.

    `armed` rides along because the arm sheet seeds its draft from whatever the
    task was last armed with, and those parameters live on the attempt now —
    so a row that could not carry them would open an empty sheet over a task
    that is armed.

    `child_count` went with the two-level grain. A task has attempts and a
    feature, and neither of those is a child task.
    """
    if not items:
        return
    from lib.tasks import runs  # noqa: PLC0415

    ids = [row["id"] for row in items]
    with SessionLocal() as session:
        attempts = dict(session.exec(
            select(AgentTaskRun.task_id, func.count(AgentTaskRun.id))
            .where(AgentTaskRun.task_id.in_(ids),
                   AgentTaskRun.status != RUN_STATUS_ARMED)
            .group_by(AgentTaskRun.task_id)).all())
        pending = {row.task_id: row for row in session.exec(
            select(AgentTaskRun)
            .where(AgentTaskRun.task_id.in_(ids),
                   AgentTaskRun.status == RUN_STATUS_ARMED)).all()}
    for row in items:
        row["run_count"] = attempts.get(row["id"], 0)
        armed = pending.get(row["id"])
        row["armed"] = runs.serialize_attempt(armed) if armed else None


def _detail(task: dict, task_id: int) -> dict:
    """The detail envelope: the row, its pending attempt, and its feature.

    The feature rides back resolved rather than as a bare id, because the page
    renders its title and stage — and `features.title` is the authority for
    that name, never this task's own title.
    """
    from lib.features import events as feature_events  # noqa: PLC0415
    from lib.features import store as feature_store  # noqa: PLC0415
    from lib.tasks import runs  # noqa: PLC0415

    feature = (feature_store.get(task["feature_id"])
               if task.get("feature_id") is not None else None)
    return {"task": _one_with_images(task),
            "armed": runs.armed_attempt(task_id),
            "feature": None if feature is None else {
                "id": feature.id, "title": feature.title,
                "origin": feature.origin,
                "external_ref": feature.external_ref,
                "stage": feature_events.stage(feature.id)}}


@tasks_bp.route('/api/tasks/<int:task_id>', methods=['GET'])
@require_auth
def api_get_task(task_id: int):
    """One task by id — the read behind the `/tasks/<id>` detail route.

    A page that has an address must be able to resolve it directly. Answering
    it by filtering the list would silently stop working at whatever page size
    the client happened to ask for.
    """
    task = store.get_task(task_id)
    if task is None:
        return _unknown_task()
    return jsonify(_detail(task, task_id))


@tasks_bp.route('/api/tasks/<int:task_id>', methods=['PATCH'])
@require_editor
def api_update_task(task_id: int):
    """Edit a task's text, or move it between `idea` and `archived`."""
    try:
        fields = capture.parse_update(request.get_json(silent=True))
    except capture.CaptureRejected as exc:
        return _rejected(exc)
    try:
        task = store.update_task(task_id, fields)
    except store.TransitionRejected as exc:
        log.error("task_transition_rejected", task_id=task_id,
                  target=exc.target, current=exc.current)
        return jsonify({"error": str(exc), "field": "status"}), 400
    except store.UnknownProject as exc:
        return jsonify({"error": str(exc), "field": "project_id"}), 400
    except feature_store_error() as exc:
        return jsonify({"error": str(exc), "field": "feature_id"}), 400
    if task is None:
        return _unknown_task()
    return jsonify(_detail(task, task_id))


def feature_store_error():
    """`lib.features.store.FeatureError`, imported lazily.

    An edit may move the task between features, and naming one that does not
    exist is a 400 on that field — the same posture every other refusal here
    takes.
    """
    from lib.features.store import FeatureError  # noqa: PLC0415
    return FeatureError


@tasks_bp.route('/api/tasks/<int:task_id>', methods=['DELETE'])
@require_editor
def api_delete_task(task_id: int):
    """Remove a task for good, with its attempts, images and edges.

    Distinct from `PATCH {status: 'archived'}`, which keeps the row and can be
    undone — nothing here can, so the refusal is a 409 the caller shows rather
    than a silent no-op.
    """
    try:
        task = store.delete_task(task_id)
    except store.DeleteRefused as exc:
        log.error("task_delete_refused", task_id=task_id, detail=str(exc))
        return jsonify({"error": str(exc)}), 409
    if task is None:
        return _unknown_task()
    return jsonify({"deleted": task})


@tasks_bp.route('/api/tasks/bulk', methods=['DELETE'])
@require_editor
def api_bulk_delete_tasks():
    """Delete many tasks, naming the rows it could not — `bulk` for removal."""
    try:
        ids = _bulk_ids(request.get_json(silent=True))
    except capture.CaptureRejected as exc:
        return _rejected(exc)
    return jsonify(store.bulk_delete(ids))


def _unknown_task():
    return jsonify({"error": "unknown task"}), 404


# ── images ───────────────────────────────────────────────────

def _decoded_image(payload) -> bytes:
    """The bytes a composer sent, or ValueError with the reason.

    Base64 in JSON rather than multipart because that is what the sibling
    `bridge-attachments` ingest takes and what the SPA's api client can send
    without a second transport.
    """
    raw = (payload or {}).get("data_b64")
    if not isinstance(raw, str) or not raw:
        raise ValueError("data_b64 required")
    try:
        return base64.b64decode(raw, validate=True)
    except (ValueError, binascii.Error):
        raise ValueError("data_b64 is not valid base64") from None


@tasks_bp.route('/api/tasks/<int:task_id>/images', methods=['POST'])
@require_editor
def api_attach_task_image(task_id: int):
    """Attach one image to a task.

    A refusal is a 400 with the reason, not a silent drop: the operator is
    watching a thumbnail that has to either appear or explain itself.
    """
    if store.get_task(task_id) is None:
        return _unknown_task()
    try:
        data = _decoded_image(request.get_json(silent=True))
        return jsonify({"image": images.attach(task_id, data)}), 201
    except ValueError as exc:
        log.error("task_image_rejected", task_id=task_id, detail=str(exc))
        return jsonify({"error": str(exc), "field": "data_b64"}), 400


@tasks_bp.route('/api/tasks/<int:task_id>/images/<int:idx>', methods=['DELETE'])
@require_editor
def api_remove_task_image(task_id: int, idx: int):
    """Detach one image. 404 when nothing is at that index."""
    if not images.remove(task_id, idx):
        return jsonify({"error": "unknown image"}), 404
    return jsonify({"removed": True, "idx": idx})


@tasks_bp.route('/api/tasks/<int:task_id>/images/<int:idx>', methods=['GET'])
@require_auth
def api_get_task_image(task_id: int, idx: int):
    """Serve one image as raw bytes, for the thumbnail on the card."""
    found = images.get_bytes(task_id, idx)
    if found is None:
        return jsonify({"error": "unknown image"}), 404
    data, media_type = found
    # `idx` is never reused once freed (`images.remove`), so a (task, idx) pair
    # names the same bytes forever and can be cached hard.
    return Response(data, mimetype=media_type,
                    headers={"Cache-Control": "private, max-age=86400"})


# ── execution ────────────────────────────────────────────────


def _fire_after_write(task_id: int) -> None:
    """Release a task the write above may have made runnable.

    Guarded exactly as `runs.fire_ready_dependents` guards its own fires: the
    write is already committed, so a launch that faults must be reported as a
    task that did not start, never as the write failing.
    """
    try:
        runs.fire_if_ready(task_id)
    except Exception:  # noqa: BLE001 — a launch fault is not a failed write
        log.error("task_fire_failed", task_id=task_id, exc_info=True)


@tasks_bp.route('/api/tasks/<int:task_id>/arm', methods=['POST'])
@require_editor
def api_arm_task(task_id: int):
    """Parameterise the task's next attempt and mark it `armed`.

    The only route that may set an execution parameter. The allowlist mirrors
    `launch_run`'s signature, so a field it accepts is a field the launch
    honors, and anything else is a 400 naming it rather than a silent drop.

    The parameters land on an `armed` **run row** — the pending attempt — so a
    rerun under different ones is a second row there rather than a second task.
    """
    try:
        fields = arm.parse_arm(request.get_json(silent=True))
    except capture.CaptureRejected as exc:
        log.error("task_arm_rejected", task_id=task_id, field=exc.field,
                  detail=str(exc))
        return _rejected(exc)
    try:
        task = store.arm_task(task_id, **fields)
    except store.TransitionRejected as exc:
        return jsonify({"error": str(exc), "field": "status"}), 400
    if task is None:
        return _unknown_task()
    # Arming can be the write that makes a task runnable — its dependencies may
    # have finished long before anyone armed it, and no completion is left to
    # notice.
    _fire_after_write(task_id)
    return jsonify(_detail(store.get_task(task_id), task_id))


@tasks_bp.route('/api/tasks/<int:task_id>/run', methods=['POST'])
@require_editor
def api_run_task(task_id: int):
    """Launch one attempt. 409 when the task is blocked, unarmed or live.

    The refusal names the blocking ids: an operator told only "blocked" has to
    go find them.
    """
    try:
        run = runs.start_run(task_id)
    except runs.RunRefused as exc:
        log.warn("task_run_refused", task_id=task_id, detail=str(exc))
        return jsonify({"error": str(exc), "blocking": exc.blocking}), 409
    except worktree.WorktreeRefused as exc:
        return jsonify({"error": str(exc), "field": "worktree"}), 400
    if run is None:
        return _unknown_task()
    return jsonify({"run": run}), 201


def _deliver(task_id: int, control):
    """Hand a control call to the run behind a task's latest attempt.

    `control` is the registry's own function — this route neither settles the
    attempt row nor decides that the run ended. The authority on that is the
    run's completion callback: a stop that only closed the prompt queue would
    land between turns and silently fail on a turn that never ends.
    """
    if store.get_task(task_id) is None:
        return _unknown_task()
    attempt = runs.latest_attempt(task_id)
    if attempt is None or not attempt["trace_id"]:
        return jsonify({"error": f"task {task_id} has no run to control"}), 409
    delivered, detail = control(attempt["trace_id"])
    return jsonify({"delivered": delivered, "detail": detail,
                    "trace_id": attempt["trace_id"], "run_id": attempt["id"]})


@tasks_bp.route('/api/tasks/<int:task_id>/stop', methods=['POST'])
@require_editor
def api_stop_task_run(task_id: int):
    """End the run: `registry.stop_run` interrupts the turn in flight as well
    as closing the queue. Asynchronous by design — the row advances when
    teardown actually completes, not when it is asked for."""
    from lib.agent_sdk import registry

    return _deliver(task_id, registry.stop_run)


@tasks_bp.route('/api/tasks/<int:task_id>/interrupt', methods=['POST'])
@require_editor
def api_interrupt_task_run(task_id: int):
    """Cancel the turn in flight, leaving the session open for the next one."""
    from lib.agent_sdk import registry

    return _deliver(task_id, registry.interrupt_run)


_FINISH_STATUSES = {"done": runs.RUN_STATUS_DONE,
                    "failed": runs.RUN_STATUS_FAILED}


@tasks_bp.route('/api/tasks/runs/<int:run_id>/finish', methods=['POST'])
def api_finish_task_run(run_id: int):
    """The running agent's own report that its work is finished.

    Deliberately undecorated. The caller is the agent's `regin task finish`
    process, which holds no JWT, so the guard is the app's loopback-only bypass
    (`web/app.py::_task_finish_loopback_ok`) — the same shape the auto-inject
    and badge-push producers use. It admits nothing a local caller could not
    already do by writing the DB it shares, and returns no task content.
    """
    payload = request.get_json(silent=True) or {}
    unknown = sorted(set(payload) - {"status", "reason"})
    if unknown:
        return jsonify({"error": f"unknown field: {unknown[0]}",
                        "field": unknown[0]}), 400
    wanted = (payload.get("status") or "done")
    if wanted not in _FINISH_STATUSES:
        return jsonify({"error": f"status must be one of "
                                 f"{sorted(_FINISH_STATUSES)}",
                        "field": "status"}), 400
    result = runs.finish_run(run_id, status=_FINISH_STATUSES[wanted],
                             reason=str(payload.get("reason") or ""))
    if result["task_id"] is None:
        return jsonify({"error": f"no run #{run_id}"}), 404
    return jsonify(result)


@tasks_bp.route('/api/tasks/<int:task_id>/runs', methods=['GET'])
@require_editor
def api_task_runs(task_id: int):
    """Attempt history, newest first, joined to `agent_runs` for status and
    pid — `live` is the merged answer, never `status` alone."""
    if store.get_task(task_id) is None:
        return _unknown_task()
    return jsonify({"runs": runs.list_runs(task_id)})


@tasks_bp.route('/api/tasks/<int:task_id>/deps', methods=['PUT'])
@require_editor
def api_set_task_deps(task_id: int):
    """Replace a task's dependency set. A self-edge or a cycle is a 400 naming
    the offending pair — rejected here so no reader has to defend against it.

    An entry is a bare id (a `requires` edge, the strict wait) or
    `{"id", "kind"}`. `depends_on` comes back as ids for a caller that only
    wants the shape of the graph; `deps` carries the kinds."""
    try:
        wanted = deps.parse_deps(request.get_json(silent=True))
        stored = deps.set_deps(task_id, wanted)
    except capture.CaptureRejected as exc:
        return _rejected(exc)
    except deps.DepRejected as exc:
        log.error("task_deps_rejected", task_id=task_id, pair=list(exc.pair),
                  detail=str(exc))
        return jsonify({"error": str(exc), "pair": list(exc.pair),
                        "field": "depends_on"}), 400
    if stored is None:
        return _unknown_task()
    # Dropping the edge that was blocking an armed task — or re-declaring it as
    # ordering behind something that already failed — releases it exactly as
    # finishing that dependency would have.
    _fire_after_write(task_id)
    return jsonify({
        "task_id": task_id,
        "depends_on": [edge.id for edge in stored],
        "deps": [{"id": edge.id, "kind": edge.kind} for edge in stored],
    })


@tasks_bp.route('/api/tasks/graph', methods=['GET'])
@require_editor
def api_task_graph():
    """The DAG, as a neighbourhood rather than a corpus.

    `root` + `depth` walk out from one task in both directions. `cap` is what a
    caller that is *drawing* passes: past it, an unfocused graph answers with
    its size and no nodes rather than a 40 000-pixel SVG. It is opt-in because
    the dependency editor reads the same endpoint for its candidate list, and a
    cap there would silently offer no dependencies at all.
    """
    try:
        root = query.parse_int(request.args.get("root"), "root")
        depth = query.parse_int(request.args.get("depth"), "depth")
        cap = query.parse_int(request.args.get("cap"), "cap")
    except query.FilterRejected as exc:
        return _rejected_filter(exc)
    status = (request.args.get("status") or "").strip()
    return jsonify(deps.graph(root=root, depth=depth, status=status or None,
                              cap=cap))


# ── issue trackers ───────────────────────────────────────────

@tasks_bp.route('/api/task-integrations', methods=['GET'])
@require_auth
def api_task_integrations():
    """Every known tracker and whether it holds a credential.

    Unconfigured platforms are listed rather than hidden: the attach field
    needs to say *why* a paste resolves to nothing, and "Linear is here but
    off" is a different answer from "regin has never heard of Linear".
    """
    return jsonify({"providers": integrations.provider_rows()})


@tasks_bp.route('/api/tasks/<int:task_id>/comments', methods=['GET'])
@require_auth
def api_list_task_comments(task_id: int):
    """Every thread about this task, each carrying its comments.

    Read-authenticated like the rest of this blueprint's reads: the roles
    guard against write-accidents, not read-secrets.
    """
    if store.get_task(task_id) is None:
        return _unknown_task()
    return jsonify({"threads": comments.thread_with_comments(
        COMMENT_SUBJECT, str(task_id))})


@tasks_bp.route('/api/tasks/<int:task_id>/comments', methods=['POST'])
@require_editor
def api_add_task_comment(task_id: int):
    """Post a comment. Opens a thread unless the body names one.

    A bare `{"body": …}` starting a new thread is the common case — the
    alternative made an operator perform two requests to say one thing.
    """
    if store.get_task(task_id) is None:
        return _unknown_task()
    payload = request.get_json(silent=True) or {}
    thread_id = payload.get("thread_id")
    user = get_current_user()
    try:
        thread = _thread_for(task_id, thread_id, user)
        if thread is None:
            return jsonify({"error": "unknown thread"}), 404
        comment = comments.add_comment(
            thread["id"], body=payload.get("body") or "",
            author_id=user["id"] if user else None)
    except comments.CommentRejected as exc:
        return jsonify({"error": str(exc), "field": exc.field}), 400
    return jsonify({"thread": {**thread, "comments": comments.comments_for(
        thread["id"])}, "comment": comment}), 201


def _thread_for(task_id: int, thread_id, user):
    """The named thread, or a fresh one. None when the id names nothing."""
    if thread_id is None:
        return comments.open_thread(COMMENT_SUBJECT, str(task_id),
                                    created_by=user["id"] if user else None)
    existing = [t for t in comments.threads_for(COMMENT_SUBJECT, str(task_id))
                if t["id"] == thread_id]
    return existing[0] if existing else None


@tasks_bp.route('/api/tasks/<int:task_id>/comments/<int:thread_id>',
                methods=['PATCH'])
@require_editor
def api_resolve_task_comment_thread(task_id: int, thread_id: int):
    """Mark a conversation dealt with, or reopen it.

    Says nothing about the task's own status: a resolved conversation and a
    shipped feature are two axes, and this route moves only the first.
    """
    payload = request.get_json(silent=True) or {}
    if _thread_for(task_id, thread_id, None) is None:
        return jsonify({"error": "unknown thread"}), 404
    try:
        thread = comments.resolve(thread_id,
                                  payload.get("resolution_state") or "")
    except comments.CommentRejected as exc:
        return jsonify({"error": str(exc), "field": exc.field}), 400
    return jsonify({"thread": thread})


