"""/api/files — read-only file meta and bytes for the viewer.

Role gates vary by source kind (editor for a project, admin for a session or
span), so they are checked here per request rather than by a route decorator.
There is deliberately no write route.
"""

from __future__ import annotations

from io import BytesIO

from flask import Blueprint, jsonify, request, send_file

from lib.auth import get_current_user
from lib.files.confine import authorize
from lib.files.errors import FileAccessError
from lib.files.reader import read_meta, read_raw
from lib.files.sources import parse_source

files_bp = Blueprint("files", __name__)


def _authorized_source():
    source = parse_source(request.args)
    user = get_current_user() or {}
    authorize(source, user.get("role"))
    return source


def _error(exc: FileAccessError):
    return jsonify({"error": exc.error}), exc.status


@files_bp.route("/api/files/meta")
def api_files_meta():
    try:
        return jsonify(read_meta(_authorized_source()))
    except FileAccessError as exc:
        return _error(exc)


@files_bp.route("/api/files/raw")
def api_files_raw():
    try:
        loaded, mime = read_raw(_authorized_source())
    except FileAccessError as exc:
        return _error(exc)
    download = request.args.get("download") == "1"
    body = loaded.path if loaded.path is not None else BytesIO(loaded.data)
    resp = send_file(body, mimetype=mime, as_attachment=download,
                     download_name=loaded.name, conditional=True,
                     etag=loaded.path is not None, max_age=0)
    # A raw URL opened directly in a tab must not run its content in regin's
    # origin: no MIME sniffing, and a sandbox with no allowances.
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["Content-Security-Policy"] = "sandbox"
    resp.headers["Cache-Control"] = "private, no-cache"
    return resp
