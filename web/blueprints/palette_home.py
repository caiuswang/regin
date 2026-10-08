"""`GET /api/palette/home`, `GET /api/palette/list/<key>` and
`GET /api/palette/relations/<kind>/<id>` — the palette's empty-query desk, the
paged list behind each of its groups and kinds, and its entity preview. Both are reads: the palette
navigates and copies, it never changes state."""

from __future__ import annotations

from flask import Blueprint, jsonify, request

from lib.activity_log import get_activity_logger
from lib.orm.engine import get_connection
from lib.palette.home import chain, home, listing
from lib.palette.relations import relations

palette_home_bp = Blueprint("palette_home", __name__)
log = get_activity_logger("fulltext")


@palette_home_bp.route("/api/palette/home")
def api_palette_home():
    trace_id = (request.args.get("session") or "").strip()
    conn = get_connection()
    try:
        if trace_id:
            log.read("palette_chain", trace_id=trace_id)
            return jsonify({"chain": chain(conn, trace_id)})
        log.read("palette_home")
        return jsonify(home(conn))
    finally:
        conn.close()


LIST_MAX_LIMIT = 25


def _int_arg(name: str, default: int) -> int:
    try:
        return max(0, int(request.args.get(name, default)))
    except (TypeError, ValueError):
        return default


@palette_home_bp.route("/api/palette/list/<key>")
def api_palette_list(key: str):
    offset = _int_arg("offset", 0)
    limit = min(_int_arg("limit", 12) or 12, LIST_MAX_LIMIT)
    log.read("palette_list", key=key, offset=offset)
    conn = get_connection()
    try:
        payload = listing(conn, key, offset, limit)
    finally:
        conn.close()
    if payload is None:
        return jsonify({"error": f"no list {key}"}), 404
    return jsonify(payload)


@palette_home_bp.route("/api/palette/relations/<kind>/<ident>")
def api_palette_relations(kind: str, ident: str):
    log.read("palette_relations", kind=kind, ident=ident)
    conn = get_connection()
    try:
        payload = relations(conn, kind, ident)
    finally:
        conn.close()
    if payload is None:
        return jsonify({"error": f"no {kind} {ident}"}), 404
    return jsonify(payload)
