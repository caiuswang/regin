"""The `@`-reference endpoints: one grouped search, one batch resolve, one
item's preview, and who cites one item.

Search is an editor's act — it feeds a composer only an editor can submit.
Resolve, preview and backlinks are a reader's: every signed-in role renders the bodies
that carry chips. Sessions follow their own surface's rule throughout: only
an admin is told what a session said, or who cites one.
"""

from __future__ import annotations

from flask import Blueprint, jsonify, request

from lib.auth import get_current_user, require_auth, require_editor
from lib.refs import backlinks as backlinks_module
from lib.refs import preview as preview_module
from lib.refs import resolve as resolve_module
from lib.refs import search as search_module
from lib.refs.grammar import RefError

refs_bp = Blueprint("refs", __name__)


def _is_admin() -> bool:
    user = get_current_user()
    return bool(user) and user.get("role") == "admin"


def _refused(exc: RefError):
    return jsonify({"error": str(exc), "field": exc.field}), 400


@refs_bp.route("/api/refs", methods=["GET"])
@require_editor
def api_refs_search():
    try:
        return jsonify(search_module.search(
            request.args.get("q") or "",
            kind=request.args.get("kind"),
            project_id=request.args.get("project_id"),
            limit=request.args.get("limit"),
            feature_id=request.args.get("feature_id"),
            can_read_sessions=_is_admin()))
    except RefError as exc:
        return _refused(exc)


@refs_bp.route("/api/refs/resolve", methods=["GET"])
@require_auth
def api_refs_resolve():
    try:
        refs = resolve_module.resolve_refs(
            request.args.getlist("ref"),
            project_id=request.args.get("project_id"),
            can_read_sessions=_is_admin())
    except RefError as exc:
        return _refused(exc)
    return jsonify({"refs": refs})


@refs_bp.route("/api/refs/preview", methods=["GET"])
@require_auth
def api_refs_preview():
    try:
        return jsonify(preview_module.preview(
            request.args.get("ref") or "",
            project_id=request.args.get("project_id"),
            can_read_sessions=_is_admin()))
    except RefError as exc:
        return _refused(exc)


@refs_bp.route("/api/refs/backlinks", methods=["GET"])
@require_auth
def api_refs_backlinks():
    ref = request.args.get("ref") or ""
    if ref.startswith("session:") and not _is_admin():
        return jsonify({"error": "sessions are admin only", "field": "ref"}), 403
    try:
        items = backlinks_module.referenced_by(ref)
    except RefError as exc:
        return _refused(exc)
    return jsonify({"ref": ref, "items": items})
