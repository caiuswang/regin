"""Task schedules — `at` / `window` gates and `cron` recurrence (F173).

Every schedule write lands here, in the server process, because the scheduler
thread that acts on it lives here too: a write sets its wake event, so a new
earliest time is honoured without a polling interval. The CLI goes through
these routes for the same reason.
"""

from __future__ import annotations

from flask import Blueprint, jsonify, request

from lib.activity_log import get_activity_logger
from lib.auth import get_current_user, require_auth, require_editor
from lib.tasks import schedules, store

task_schedules_bp = Blueprint('task_schedules', __name__)

log = get_activity_logger("tasks")

_BULK_MAX = 200


def _error(message: str, status: int = 400):
    return jsonify({"error": message}), status


def _user_id():
    user = get_current_user() or {}
    return user.get("id")


def _put(task_id: int, payload) -> dict:
    """Validate and write one schedule. Raises `ScheduleRejected` /
    `UnknownTask`."""
    spec = schedules.parse_spec(payload)
    schedules.put(task_id, spec, user_id=_user_id())
    return schedules.detail(task_id)


@task_schedules_bp.route('/api/tasks/<int:task_id>/schedule', methods=['GET'])
@require_auth
def api_get_schedule(task_id: int):
    found = schedules.detail(task_id)
    if found is None:
        return _error("no schedule", 404)
    return jsonify(found)


@task_schedules_bp.route('/api/tasks/<int:task_id>/schedule', methods=['PUT'])
@require_editor
def api_put_schedule(task_id: int):
    """Write or replace the schedule. A body of `{enabled: bool}` alone
    toggles the existing one instead."""
    payload = request.get_json(silent=True)
    if isinstance(payload, dict) and set(payload) == {"enabled"}:
        return _toggle(task_id, bool(payload["enabled"]))
    try:
        return jsonify(_put(task_id, payload))
    except schedules.UnknownTask:
        return _error("unknown task", 404)
    except schedules.ScheduleRejected as exc:
        log.warn("task_schedule_rejected", task_id=task_id, detail=str(exc))
        return _error(str(exc))


def _toggle(task_id: int, enabled: bool):
    task = store.get_task(task_id)
    if task is None:
        return _error("unknown task", 404)
    if enabled and task["status"] == "archived":
        return _error("已归档的 task 不能定时")
    if schedules.set_enabled(task_id, enabled) is None:
        return _error("no schedule", 404)
    return jsonify(schedules.detail(task_id))


@task_schedules_bp.route('/api/tasks/<int:task_id>/schedule',
                         methods=['DELETE'])
@require_editor
def api_delete_schedule(task_id: int):
    if not schedules.remove(task_id):
        return _error("no schedule", 404)
    return jsonify({"deleted": True})


@task_schedules_bp.route('/api/tasks/schedule/preview', methods=['POST'])
@require_auth
def api_preview_schedule():
    now = schedules.now_utc()
    try:
        spec = schedules.parse_spec(request.get_json(silent=True))
        # The refusals that need no task, so a preview never offers a slot the
        # save would turn down.
        schedules.check_timing(spec, now)
    except schedules.ScheduleRejected as exc:
        return _error(str(exc))
    return jsonify({"upcoming": schedules.upcoming(spec, now)})


def _bulk_ids(payload) -> list[int]:
    ids = payload.get("task_ids") if isinstance(payload, dict) else None
    if not isinstance(ids, list) or not ids or not all(
            isinstance(i, int) and not isinstance(i, bool) for i in ids):
        raise schedules.ScheduleRejected("task_ids 需要是 task id 列表")
    return ids[:_BULK_MAX]


def _apply_one(task_id: int, body) -> None:
    if body is None:
        if store.get_task(task_id) is None:
            raise schedules.UnknownTask("unknown task")
        schedules.remove(task_id)
        return
    _put(task_id, body)


@task_schedules_bp.route('/api/tasks/schedule/bulk', methods=['POST'])
@require_editor
def api_bulk_schedule():
    """Apply one schedule (or `null` = clear) to many tasks, per-row."""
    payload = request.get_json(silent=True)
    try:
        ids = _bulk_ids(payload)
    except schedules.ScheduleRejected as exc:
        return _error(str(exc))
    body = payload.get("schedule")
    applied, refused = [], []
    for task_id in ids:
        try:
            _apply_one(task_id, body)
            applied.append(task_id)
        except schedules.UnknownTask:
            refused.append({"id": task_id, "error": "unknown task"})
        except schedules.ScheduleRejected as exc:
            refused.append({"id": task_id, "error": str(exc)})
    log.write("task_schedule_bulk", applied=len(applied),
              refused=len(refused))
    return jsonify({"applied": applied, "refused": refused})


@task_schedules_bp.route('/api/tasks/<int:task_id>/schedule/fires',
                         methods=['GET'])
@require_auth
def api_schedule_fires(task_id: int):
    try:
        limit = int(request.args.get("limit", 50))
    except ValueError:
        return _error("limit must be an integer")
    return jsonify(schedules.fires(task_id, limit))


@task_schedules_bp.route('/api/tasks/schedules', methods=['GET'])
@require_auth
def api_list_schedules():
    return jsonify(schedules.list_all())
