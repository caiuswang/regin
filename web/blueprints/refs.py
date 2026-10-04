"""The `@`-reference endpoints: one grouped search, one batch resolve.

Search is an editor's act — it feeds a composer only an editor can submit.
Resolve is a reader's: every signed-in role renders the bodies that carry
chips. Sessions follow their own surface's rule on both: only an admin is
told what a session said.
"""

from __future__ import annotations

from flask import Blueprint, jsonify, request

from lib.auth import get_current_user, require_auth, require_editor
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
