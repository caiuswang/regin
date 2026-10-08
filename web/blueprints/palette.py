"""`/api/palette/*` — the palette's process-navigator reads. Nothing here writes."""

from __future__ import annotations

from flask import Blueprint, jsonify, request

from lib.activity_log import get_activity_logger
from lib.orm.engine import get_connection
from lib.palette.resolve import resolve

palette_bp = Blueprint("palette", __name__)
log = get_activity_logger("fulltext")


@palette_bp.route("/api/palette/resolve")
def api_palette_resolve():
    ref = request.args.get("ref") or ""
    log.read("palette_resolve", ref_len=len(ref))
    conn = get_connection()
    try:
        payload = resolve(conn, ref)
    finally:
        conn.close()
    return jsonify(payload)
