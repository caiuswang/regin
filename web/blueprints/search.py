"""`GET /api/search` — the full-text palette's one fan-out call."""

from __future__ import annotations

from flask import Blueprint, jsonify, request

from lib.activity_log import get_activity_logger
from lib.fulltext import DEFAULT_LIMIT, SCOPE_GLOBAL, SCOPE_SESSION, search
from lib.fulltext.docs import KIND_ORDER
from lib.orm.engine import get_connection
from lib.trace.alias import trace_group

search_bp = Blueprint("search", __name__)
log = get_activity_logger("fulltext")

MAX_LIMIT = 25


def _requested_kinds() -> list[str] | None:
    raw = (request.args.get("kinds") or "").strip()
    if not raw:
        return None
    wanted = [k.strip() for k in raw.split(",") if k.strip() in KIND_ORDER]
    return wanted or None


def _scope() -> str:
    """An unrecognised (or absent) scope reads as `session`, which is what
    every caller that only knows how to pass `session=` already meant."""
    raw = (request.args.get("scope") or "").strip()
    return SCOPE_GLOBAL if raw == SCOPE_GLOBAL else SCOPE_SESSION


def _limit() -> int:
    try:
        return max(1, min(MAX_LIMIT, int(request.args.get("limit") or DEFAULT_LIMIT)))
    except (TypeError, ValueError):
        return DEFAULT_LIMIT


def _scoped_trace_ids(conn, raw: str) -> list[str]:
    """The open session plus every trace id aliased to it — a resumed or
    forked session keeps its spans under the old id, so searching the route's
    id alone would miss the first half of its own trace."""
    trace_id = (raw or "").strip()
    if not trace_id:
        return []
    try:
        group = trace_group(conn, trace_id)
    except Exception:
        log.error("alias_group_failed", exc_info=True)
        group = []
    return group or [trace_id]


@search_bp.route("/api/search")
def api_search():
    query = (request.args.get("q") or "").strip()
    scope = _scope()
    log.read("search_query", query_len=len(query),
             kinds=request.args.get("kinds") or "all", scope=scope)
    conn = get_connection()
    try:
        trace_ids = _scoped_trace_ids(conn, request.args.get("session") or "")
        payload = search(conn, query, _requested_kinds(), trace_ids, _limit(),
                         scope)
    finally:
        conn.close()
    return jsonify(payload)
