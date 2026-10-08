"""`GET /api/palette/home` and `GET /api/palette/relations/<kind>/<id>` — the
palette's empty-query desk and its entity preview. Both are reads: the palette
navigates and copies, it never changes state."""

from __future__ import annotations

from flask import Blueprint, jsonify, request

from lib.activity_log import get_activity_logger
from lib.orm.engine import get_connection
from lib.palette.home import chain, home
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
