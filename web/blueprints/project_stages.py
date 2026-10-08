"""A project's step stages on the web — list, add, edit, remove, reorder.

`<project>` is a registered repo's id, or `default` for the global list that
features with no project read and that a project copies on its first write.
Reads are any authenticated role; writes are editor-or-better, the same split
as the feature ledger.
"""

from __future__ import annotations

from typing import Optional

from flask import Blueprint, jsonify, request

from lib.auth import require_auth, require_editor
from lib.features import stages

project_stages_bp = Blueprint("project_stages", __name__)


class _BadProject(Exception):
    pass


def _project(raw: str) -> Optional[int]:
    if raw == "default":
        return None
    try:
        return int(raw)
    except ValueError as exc:
        raise _BadProject(raw) from exc


def _payload() -> dict:
    payload = request.get_json(silent=True)
    return payload if isinstance(payload, dict) else {}


def _refused(exc: stages.StageError):
    return jsonify({"error": str(exc), "field": exc.field}), exc.status


def _call(raw: str, fn, *args):
    try:
        return fn(_project(raw), *args), None
    except _BadProject:
        return None, (jsonify({"error": f"unknown project: {raw}"}), 404)
    except stages.StageError as exc:
        return None, _refused(exc)


@project_stages_bp.route("/api/projects/<project>/stages")
@require_auth
def api_list_stages(project: str):
    def read(project_id):
        stages.require_project(project_id)
        return stages.list_stages(project_id)
    body, error = _call(project, read)
    return error or jsonify(body)


@project_stages_bp.route("/api/projects/<project>/stages", methods=["POST"])
@require_editor
def api_add_stage(project: str):
    body, error = _call(project, stages.add_stage, _payload())
    return error or (jsonify({"stage": body}), 201)


@project_stages_bp.route("/api/projects/<project>/stages/order",
                         methods=["PUT"])
@require_editor
def api_reorder_stages(project: str):
    body, error = _call(project, stages.reorder, _payload().get("keys"))
    return error or jsonify(body)


@project_stages_bp.route("/api/projects/<project>/stages/<key>",
                         methods=["PATCH"])
@require_editor
def api_update_stage(project: str, key: str):
    body, error = _call(project, stages.update_stage, key, _payload())
    return error or jsonify({"stage": body})


@project_stages_bp.route("/api/projects/<project>/stages/<key>",
                         methods=["DELETE"])
@require_editor
def api_remove_stage(project: str, key: str):
    body, error = _call(project, stages.remove_stage, key)
    return error or jsonify(body)
