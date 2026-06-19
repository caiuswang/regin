"""Session grades API (`lib/grader`) — list, detail, run, pareto.

POST grade runs default to the mechanical screen tier: the deep tier
shells out to an external judge agent with a multi-minute timeout, which
is a CLI-shaped workload (`regin grade run --tier deep`), not a web
request. The web caller can still request it explicitly.
"""

from __future__ import annotations

from flask import Blueprint, jsonify, request

import lib.grader as grader
from lib.auth import require_editor
from lib.grader.service import AXES, TIERS, GradingError

grades_bp = Blueprint("grades", __name__)


def _bool_arg(name: str) -> bool:
    return (request.args.get(name) or "").lower() in ("1", "true", "yes")


def _limit_arg(default: int, cap: int) -> int | None:
    """Parsed + clamped `limit`, or None when unparseable (caller 400s)."""
    raw = request.args.get("limit")
    if raw is None:
        return default
    try:
        return max(1, min(int(raw), cap))
    except ValueError:
        return None


@grades_bp.route("/api/grades")
def api_grades_list():
    limit = _limit_arg(default=100, cap=500)
    if limit is None:
        return jsonify({"error": "limit must be an integer"}), 400
    rows = grader.list_grades(
        limit=limit,
        axis=request.args.get("axis") or None,
        verdict=request.args.get("verdict") or None,
        include_tests=_bool_arg("include_tests"),
    )
    return jsonify({"grades": rows})


@grades_bp.route("/api/grades/pareto")
def api_grades_pareto():
    return jsonify(grader.pareto_points(
        include_tests=_bool_arg("include_tests")))


@grades_bp.route("/api/sessions/<trace_id>/grades")
def api_session_grades(trace_id):
    grades = grader.latest_grades(trace_id, with_detail=True)
    return jsonify({"trace_id": trace_id, "grades": grades})


def _provider_error(provider: str | None):
    """None when `provider` is unset or a configured judge agent; else a
    (json, status) error tuple."""
    if provider is None:
        return None
    from lib.settings import settings
    agents = settings.topic_proposal_external_agents or {}
    if provider in agents:
        return None
    return jsonify({"error": f"unknown judge provider {provider!r}; "
                    f"configured: {sorted(agents)}"}), 400


def _parse_axes(payload: dict):
    """Resolve the axes to grade. Returns (axes_tuple, None) or
    (None, error). Accepts a per-run `axes` list (may be empty for an
    aspect-only run) or the legacy single `axis`; defaults to both when no
    axes key is given."""
    axes_in = payload.get("axes")
    if axes_in is not None:
        if not isinstance(axes_in, list) or any(a not in AXES for a in axes_in):
            return None, (jsonify(
                {"error": f"axes must be a subset of {list(AXES)}"}), 400)
        return tuple(dict.fromkeys(axes_in)), None
    axis = payload.get("axis")
    if axis is not None and axis not in AXES:
        return None, (jsonify({"error": f"axis must be one of {list(AXES)}"}),
                      400)
    return ((axis,) if axis else AXES), None


def _parse_aspects(payload: dict):
    """Resolve the gradeable aspect keys for this run. Returns (aspects_list,
    None) or (None, error). Absent → []. Each must be a configured,
    non-builtin aspect (the builtin axes are graded as axes, not aspects)."""
    aspects_in = payload.get("aspects")
    if aspects_in is None:
        return [], None
    if not isinstance(aspects_in, list):
        return None, (jsonify({"error": "aspects must be a list of keys"}), 400)
    from lib.settings import settings
    gradeable = {a.key for a in (settings.grader.aspects or [])
                 if not getattr(a, "builtin", False)}
    unknown = [a for a in aspects_in if a not in gradeable]
    if unknown:
        return None, (jsonify(
            {"error": f"unknown/non-gradeable aspect(s) {unknown}; "
                      f"gradeable: {sorted(gradeable)}"}), 400)
    return list(dict.fromkeys(aspects_in)), None


def _parse_dimensions(payload: dict):
    """Validate tier + axes + aspects together. Returns
    ((tier, axes, aspects), None) or (None, error)."""
    tier = str(payload.get("tier") or "auto")
    if tier not in TIERS:
        return None, (jsonify({"error": f"tier must be one of {list(TIERS)}"}), 400)
    axes, aerr = _parse_axes(payload)
    if aerr is not None:
        return None, aerr
    aspects, asperr = _parse_aspects(payload)
    if asperr is not None:
        return None, asperr
    if not axes and not aspects:
        return None, (jsonify(
            {"error": "select at least one axis or aspect to grade"}), 400)
    return (tier, axes, aspects), None


def _parse_grade_request(payload: dict):
    """Validate a grade POST. Returns (kwargs, None) on success or
    (None, (json, status)) on the first validation failure. Default tier is
    `auto`: screen mechanically, then escalate any non-satisfied session to
    the agentic judge so a UI grade actually consults the configured LLM."""
    dims, derr = _parse_dimensions(payload)
    if derr is not None:
        return None, derr
    tier, axes, aspects = dims
    provider = payload.get("provider") or None
    perr = _provider_error(provider)
    if perr is not None:
        return None, perr
    distill = payload.get("distill")
    return {
        "axes": axes,
        "tier": tier,
        "provider": provider,
        "aspects": aspects,
        "distill": bool(distill) if distill is not None else None,
        "is_test": 1 if payload.get("is_test") else 0,
    }, None


@grades_bp.route("/api/sessions/<trace_id>/grade", methods=["POST"])
@require_editor
def api_session_grade(trace_id):
    kwargs, error = _parse_grade_request(request.get_json(silent=True) or {})
    if error is not None:
        return error
    try:
        result = grader.grade_session(trace_id, **kwargs)
    except GradingError as exc:
        status = 404 if "no trace data" in str(exc) else 400
        return jsonify({"error": str(exc)}), status
    return jsonify(result)
