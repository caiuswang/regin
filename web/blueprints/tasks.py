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

from flask import Blueprint, Response, g, jsonify, request
from sqlmodel import func, select

from lib.activity_log import get_activity_logger
from lib.comments import images as comment_images
from lib.comments import store as comments
from lib.auth import get_current_user, require_auth, require_editor, require_scope
from lib.orm import SessionLocal
from lib.orm.models.api_keys import SCOPE_TASK_CAPTURE
from lib.orm.models.tasks import AgentTask
from lib.settings import settings
from lib.tasks import (arm, capture, deps, feature_slices, graph_lanes,
                       images, next_step, query, ratelimit, runs, slots,
                       store)
from lib.tasks import rows as task_rows
from lib.tasks import tmux_runs
from lib.agent_bridge import delivery
from lib.tasks import integrations
from lib.utils.pagination import (
    clamp_page, clamp_size, list_page, paginate_query_stmt,
)
from lib.image_payload import decoded_image, wanted_idx
from lib.workspaces import scope as ws_scope

tasks_bp = Blueprint('tasks', __name__)

log = get_activity_logger("tasks")

WEB_SOURCE = "web"

COMMENT_SUBJECT = comments.TASK_SUBJECT


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
    return task_rows.fix_defects(
        task_rows.feature_titles(_with_images([task])))[0]


def _parse_web_capture(payload):
    """Capture validation, minus the fields the server owns, plus `kind`.

    `kind` is taken here and not by `parse_capture`: this route is reachable
    only by a signed-in editor, while the machine-key capture route stays
    unable to declare a stage.

    `source` is stamped, not accepted — a browser task is a `web` task by
    definition — and a request that tries to say otherwise is told so rather
    than having its value silently overwritten.
    """
    if isinstance(payload, dict):
        for field in ("source", "external_id"):
            if field in payload:
                raise capture.CaptureRejected(
                    f"{field} is set by the server", field)
        kind = capture.parse_web_kind(payload)
        payload = {key: value for key, value in payload.items()
                   if key != "kind"}
        return {**capture.parse_capture({**payload, "source": WEB_SOURCE}),
                "kind": kind}
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
    """One offset page of the task feed under the current filters.

    Every filter is a WHERE clause here rather than an `Array.filter` in the
    page: a client-side filter over one 50-row page answers a different
    question than the control claims to ask, and the counts beside it would
    then describe the page instead of the backlog. `extras.counts` rides every
    page so the group headers are true totals without a second round trip.
    """
    if (request.args.get("group") or "").strip() == query.GROUP_NEXT_STEP:
        return _next_step_groups()
    try:
        filters = query.parse_filters(request.args)
    except query.FilterRejected as exc:
        return _rejected_filter(exc)
    with SessionLocal() as session:
        stmt = query.ordered(query.apply_filters(select(AgentTask), filters),
                             filters["sort"])
        page = paginate_query_stmt(
            session, stmt, clamp_page(request.args.get("page")),
            clamp_size(request.args.get("size")),
            row_to_dict=store.serialize)
        totals = query.counts(session, filters)
        population = session.exec(query.population_stmt(
            select(func.count(AgentTask.id)), filters["project_id"],
            filters["unassigned"])).one()
    envelope = page.to_envelope()
    _with_images(envelope["items"])
    task_rows.next_step_facts(envelope["items"])
    return jsonify(list_page(envelope, population=population,
                             sort=filters["sort"], filters=_applied(filters),
                             extras={"counts": totals}))


def _applied(filters: dict) -> dict:
    applied = {k: v for k, v in filters.items() if k != "sort"}
    applied["project_id"] = ("none" if filters["unassigned"]
                             else ws_scope.echo(filters["project_id"]))
    applied.pop("unassigned", None)
    return applied


def _population(session, filters: dict) -> int:
    return session.exec(query.population_stmt(
        select(func.count(AgentTask.id)), filters["project_id"],
        filters["unassigned"])).one()


def _grouped_read(read, only_field: str, allowed: tuple[str, ...]):
    """Shared shape of the two grouped reads: parse, read, envelope."""
    try:
        filters = query.parse_filters(request.args)
        only = next_step.parse_only(request.args.get(only_field), allowed,
                                    only_field)
    except query.FilterRejected as exc:
        return _rejected_filter(exc)
    except next_step.UnknownGroup as exc:
        return jsonify({"error": str(exc), "field": exc.field}), 400
    with SessionLocal() as session:
        body = read(session, filters,
                    size=clamp_size(request.args.get("size")),
                    page=clamp_page(request.args.get("page")), only=only)
        body["population"] = _population(session, filters)
    body["applied"] = _applied(filters)
    return jsonify(body)


def _next_step_groups():
    """`group=next_step`: Ready / Running / Blocked / Failed / Ideas / Done,
    each with its server count and first rows."""
    return _grouped_read(next_step.groups, "only", next_step.GROUP_KEYS)


@tasks_bp.route('/api/tasks/board', methods=['GET'])
@require_auth
def api_task_board():
    """The feed as status columns, each counted and paged on its own.

    `column=<status>&page=N` pages one column; failed is pageable that way but
    is otherwise only a count.
    """
    return _grouped_read(next_step.board, "column", next_step.BOARD_PAGEABLE)


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
        body = query.counts(session, filters)
        # The grouped list renders its toolbar count from this read alone.
        body["population"] = session.exec(query.population_stmt(
            select(func.count(AgentTask.id)), filters["project_id"],
            filters["unassigned"])).one()
        return jsonify(body)


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
    except feature_store_error() as exc:
        return jsonify({"error": str(exc), "field": "feature_id"}), 400
    return jsonify(result)


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
    task_rows.next_step_facts([task])
    return {"task": _one_with_images(task),
            "armed": runs.armed_attempt(task_id),
            "feature": None if feature is None else {
                "id": feature.id, "title": feature.title,
                "origin": feature.origin,
                "project_id": feature.project_id,
                "project_name": feature_store.project_name(feature.project_id),
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


@tasks_bp.route('/api/tasks/<int:task_id>/prompt-preview', methods=['GET'])
@require_auth
def api_task_prompt_preview(task_id: int):
    """The launch prompt this task's next run would be handed, built by the
    same `_prompt` the launch calls — and with no side effect at all."""
    from lib.tasks import run_prompt  # noqa: PLC0415

    task = store.get_task(task_id)
    if task is None:
        return _unknown_task()
    return jsonify(run_prompt.preview(task))


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
        payload = request.get_json(silent=True)
        data = decoded_image(payload)
        return jsonify({"image": images.attach(
            task_id, data, wanted_idx(payload))}), 201
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
    # A cached (task, idx) pair, because the index is stable for as long as
    # the image is. It is NOT stable across a delete of the HIGHEST index —
    # that one is reissued (`ImageSet.remove`), so re-attaching after such a
    # delete can serve the previous picture for the life of the cache. Defect
    # #177.
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
        arm.check_target(fields)
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


@tasks_bp.route('/api/tasks/<int:task_id>/arm-proposal', methods=['GET'])
@require_auth
def api_task_arm_proposal(task_id: int):
    """What the task's slice row proposes to arm it with, derived now and
    never stored (#119 D7). 404 when there is no slice row behind it."""
    try:
        return jsonify(feature_slices.proposal_for_task(task_id))
    except feature_slices.NoProposal as exc:
        return jsonify({"error": str(exc)}), 404


@tasks_bp.route('/api/tasks/<int:task_id>/disarm', methods=['POST'])
@require_editor
def api_disarm_task(task_id: int):
    """Take an armed task back to `idea` and drop its pending attempt.

    409 while it runs or when it is not armed — the refusal says which.
    """
    try:
        task = store.disarm_task(task_id)
    except store.DisarmRefused as exc:
        log.warn("task_disarm_refused", task_id=task_id, detail=str(exc))
        return jsonify({"error": str(exc)}), 409
    if task is None:
        return _unknown_task()
    return jsonify(_detail(task, task_id))


@tasks_bp.route('/api/tasks/<int:task_id>/run', methods=['POST'])
@require_editor
def api_run_task(task_id: int):
    """Launch one attempt. 409 when the task is blocked, unarmed or live.

    The refusal names the blocking ids: an operator told only "blocked" has to
    go find them.
    """
    waiting = _waiting_for_gate(task_id)
    if waiting is not None:
        return waiting
    try:
        run = runs.start_run(task_id)
    except runs.RunRefused as exc:
        log.warn("task_run_refused", task_id=task_id, detail=str(exc))
        if exc.field:
            return jsonify({"error": str(exc), "field": exc.field}), 400
        body = {"error": str(exc), "blocking": exc.blocking}
        if exc.at_capacity:
            body["slots"] = slots.run_slots()
        return jsonify(body), 409
    if run is None:
        return _unknown_task()
    return jsonify({"run": run}), 201


def _waiting_for_gate(task_id: int):
    """A batch run (`respect_schedule`) skips a task whose gate is shut; a
    single manual Run overrides the gate and never asks."""
    body = request.get_json(silent=True)
    if not (isinstance(body, dict) and body.get("respect_schedule")):
        return None
    from lib.tasks import scheduler  # noqa: PLC0415

    gate = scheduler.gate_waiting(task_id)
    if gate is None:
        return None
    return jsonify({"error": f"waiting for schedule {gate['next_fire_at'] or ''}".strip(),
                    "waiting_schedule": True,
                    "next_fire_at": gate["next_fire_at"]}), 409


def _deliver(task_id: int, control):
    """Hand a control call to the run behind a task's latest attempt.

    `control` is the registry's own function — this route never settles a run
    it can still reach (a dead one is `_stop`'s to settle). The authority on that is the
    run's completion callback: a stop that only closed the prompt queue would
    land between turns and silently fail on a turn that never ends.
    """
    if store.get_task(task_id) is None:
        return _unknown_task()
    attempt = runs.latest_attempt(task_id)
    if attempt is None or not attempt["trace_id"]:
        return jsonify({"error": f"task {task_id} has no run to control"}), 409
    delivered, detail = control(attempt)
    return jsonify({"delivered": delivered, "detail": detail,
                    "trace_id": attempt["trace_id"], "run_id": attempt["id"]})


@tasks_bp.route('/api/tasks/<int:task_id>/stop', methods=['POST'])
@require_editor
def api_stop_task_run(task_id: int):
    """End the run: `registry.stop_run` interrupts the turn in flight as well
    as closing the queue. Asynchronous by design — the row advances when
    teardown actually completes, not when it is asked for."""
    return _deliver(task_id, _stop)


@tasks_bp.route('/api/tasks/<int:task_id>/interrupt', methods=['POST'])
@require_editor
def api_interrupt_task_run(task_id: int):
    """Cancel the turn in flight, leaving the session open for the next one."""
    return _deliver(task_id, _interrupt)


def _stop(attempt: dict) -> tuple[bool, str]:
    """A tmux run has no completion callback to settle it, so its Stop kills
    the session and settles in the same act; an SDK one is torn down by its
    registry and settles on its own completion — unless its agent is already
    unreachable, when no completion will ever come and Stop settles it."""
    if tmux_runs.is_tmux(attempt["params"]):
        killed = runs.stop_tmux(attempt)
        return killed, ("tmux session killed" if killed
                        else "no tmux session left to kill")
    if runs.settle_dead_sdk_run(attempt):
        return True, "agent session already gone; run settled"
    from lib.agent_sdk import registry

    return registry.stop_run(attempt["trace_id"])


def _interrupt(attempt: dict) -> tuple[bool, str]:
    if tmux_runs.is_tmux(attempt["params"]):
        return delivery.deliver_key(attempt["trace_id"], "Escape")
    from lib.agent_sdk import registry

    return registry.interrupt_run(attempt["trace_id"])


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
    unknown = sorted(set(payload) - {"status", "reason", "task_id"})
    if unknown:
        return jsonify({"error": f"unknown field: {unknown[0]}",
                        "field": unknown[0]}), 400
    wanted = (payload.get("status") or "done")
    if wanted not in _FINISH_STATUSES:
        return jsonify({"error": f"status must be one of "
                                 f"{sorted(_FINISH_STATUSES)}",
                        "field": "status"}), 400
    claimed = payload.get("task_id")
    if claimed is not None and (type(claimed) is not int):
        return jsonify({"error": "task_id must be an integer",
                        "field": "task_id"}), 400
    try:
        result = runs.finish_run(run_id, status=_FINISH_STATUSES[wanted],
                                 reason=str(payload.get("reason") or ""),
                                 task_id=claimed)
    except runs.RunRefused as exc:
        return jsonify({"error": str(exc), "open_defects": exc.blocking}), 409
    if result["task_id"] is None:
        return jsonify({"error": f"no run #{run_id}"}), 404
    return jsonify(result)


@tasks_bp.route('/api/tasks/<int:task_id>/runs', methods=['GET'])
@require_auth
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
    # Re-read rather than echo: an `after` edge drawn behind a dependency that
    # has already settled is honoured and retired inside that same call, and a
    # reply naming an edge the graph no longer holds sends its reader off to
    # draw one that is not there.
    edges = deps.dep_edges(task_id)
    return jsonify({
        "task_id": task_id,
        "depends_on": [edge.id for edge in edges],
        "deps": [{"id": edge.id, "kind": edge.kind} for edge in edges],
    })


def _lane_graph(cap):
    """`lanes=1`: the Graph view's drawing — feature lanes, open chains, and
    the footer's counts, all counted here over what is drawn, under the same
    filters the list and board take."""
    try:
        filters = query.parse_filters(request.args)
    except query.FilterRejected as exc:
        return _rejected_filter(exc)
    return jsonify(graph_lanes.read(graph_lanes.LaneQuery(
        open_only=(request.args.get("open") or "1").strip() != "0",
        filters=filters, cap=cap)))


@tasks_bp.route('/api/tasks/graph', methods=['GET'])
@require_auth
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
    if (request.args.get("lanes") or "").strip() == "1":
        return _lane_graph(cap)
    status = (request.args.get("status") or "").strip()
    body = deps.graph(root=root, depth=depth, status=status or None, cap=cap)
    task_rows.fix_defects(body["nodes"])
    return jsonify(body)


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

    `images` ride the same request and land in the comment's transaction.
    Everything is checked before a thread is opened, so a refused post leaves
    no row of any kind behind.
    """
    if store.get_task(task_id) is None:
        return _unknown_task()
    payload = request.get_json(silent=True) or {}
    thread_id = payload.get("thread_id")
    body = payload.get("body") or ""
    user = get_current_user()
    try:
        staged = comment_images.check_post(body, payload.get("images"))
        comments.check_body(body, bool(staged))
        thread = _thread_for(task_id, thread_id, user)
        if thread is None:
            return jsonify({"error": "unknown thread"}), 404
        comment = comments.add_comment(
            thread["id"], body=body, images=staged,
            author_id=user["id"] if user else None)
    except comment_images.ImagesRejected as exc:
        return jsonify({"error": str(exc), "field": "images"}), 400
    except comments.CommentRejected as exc:
        return jsonify({"error": str(exc), "field": exc.field}), 400
    return jsonify({"thread": {**thread, "comments": comments.comments_for(
        thread["id"])}, "comment": comment}), 201


@tasks_bp.route('/api/comments/<int:comment_id>/images/<int:idx>')
@require_auth
def api_get_comment_image(comment_id: int, idx: int):
    """Serve one comment image as raw bytes. A comment is written once and
    its images with it, so a (comment, idx) pair never changes bytes."""
    found = comment_images.get_bytes(comment_id, idx)
    if found is None:
        return jsonify({"error": "unknown image"}), 404
    data, media_type = found
    return Response(data, mimetype=media_type,
                    headers={"Cache-Control": "private, max-age=86400"})


def _thread_for(task_id: int, thread_id, user):
    return comments.thread_for(COMMENT_SUBJECT, str(task_id), thread_id,
                               created_by=user["id"] if user else None)


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


