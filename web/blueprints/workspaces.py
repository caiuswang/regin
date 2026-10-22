"""Workspace endpoints: list / create / delete. Mutations require `editor`."""

from __future__ import annotations

from flask import Blueprint, jsonify, request

from lib.auth import require_editor
from lib.workspaces import WorkspaceError, create, list_workspaces, remove


workspaces_bp = Blueprint("workspaces", __name__)


@workspaces_bp.route("/api/workspaces")
def api_list_workspaces():
    return jsonify({"workspaces": list_workspaces()})


@workspaces_bp.route("/api/workspaces", methods=["POST"])
@require_editor
def api_create_workspace():
    """Body: `{"path": "/abs/root", "name"?: str}`. 400 when the path holds no
    git tree, 409 when the name or root is taken; `field` names which."""
    data = request.get_json(silent=True) or {}
    try:
        result = create(str(data.get("path") or ""), data.get("name"))
    except WorkspaceError as exc:
        return jsonify({"error": str(exc), "field": exc.field}), exc.status
    return jsonify({"ok": True, **result}), 201


@workspaces_bp.route("/api/workspaces/<int:workspace_id>", methods=["DELETE"])
@require_editor
def api_delete_workspace(workspace_id: int):
    gone = remove(workspace_id)
    if gone is None:
        return jsonify({"error": f"workspace not found: {workspace_id}"}), 404
    return jsonify({"ok": True, "workspace": gone})
