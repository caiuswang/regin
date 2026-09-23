"""The Pipeline shell's own endpoint: all three tab counts, one request.

Three requests for three counts would make the shell's own header the page's
slowest part, and would let the three disagree about which scope they were
answering under. One read, one scope, three numbers.
"""

from __future__ import annotations

from flask import Blueprint, jsonify, request

from lib.auth import require_auth
from lib.pipeline import counts as counts_module
from lib.pipeline import projects as projects_module

pipeline_bp = Blueprint("pipeline", __name__)


@pipeline_bp.route("/api/pipeline/counts", methods=["GET"])
@require_auth
def api_pipeline_counts():
    try:
        scope = counts_module.parse_scope(request.args.get("project"))
    except counts_module.ScopeError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify({
        "counts": counts_module.counts(scope),
        "project": request.args.get("project") or counts_module.SCOPE_ALL,
    })


@pipeline_bp.route("/api/pipeline/projects", methods=["GET"])
@require_auth
def api_pipeline_projects():
    items = projects_module.summaries()
    return jsonify({"items": items, "total": len(items)})


@pipeline_bp.route("/api/pipeline/projects/<scope>", methods=["GET"])
@require_auth
def api_pipeline_project(scope: str):
    try:
        parsed = counts_module.parse_scope(scope)
    except counts_module.ScopeError as exc:
        return jsonify({"error": str(exc)}), 400
    if parsed == counts_module.SCOPE_ALL:
        return jsonify({"error": "a project panel is one scope, not 'all'"}), 400
    try:
        return jsonify(projects_module.overview(parsed))
    except projects_module.ProjectNotFound as exc:
        return jsonify({"error": str(exc)}), 404
