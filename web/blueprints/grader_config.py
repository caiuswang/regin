"""Grader configuration API — aspects, editable judge prompts, judge provider.

Backs the Grades view's "Grader settings" panel and the prompt tab's grader
editor. Reads/writes `settings.grader.{aspects,system_prompt_overrides,
external_agent}`. The builtin aspects (correctness/process) mirror the two
grounded axes: they can be toggled but never deleted, so the server always
re-asserts their presence and `builtin` flag regardless of the payload.
"""

from __future__ import annotations

from flask import Blueprint, jsonify, request

from lib.auth import require_editor
from lib.grader.prompts import default_system_prompts
from lib.grader.service import AXES, TIERS
from lib.settings import (
    GraderAspect, _load_settings, save_settings, settings,
)

grader_config_bp = Blueprint("grader_config", __name__)

# The two builtin aspects mirror the grounded axes; toggle-only, never deleted.
_BUILTIN_KEYS = set(AXES)


def _aspect_dicts() -> list[dict]:
    return [{"key": a.key, "label": a.label, "description": a.description,
             "enabled": bool(a.enabled), "builtin": bool(a.builtin)}
            for a in settings.grader.aspects]


def _system_prompts() -> dict:
    overrides = settings.grader.system_prompt_overrides or {}
    defaults = default_system_prompts()
    return {axis: {"override": overrides.get(axis) or "",
                   "default": defaults.get(axis, "")}
            for axis in AXES}


@grader_config_bp.route("/api/grader/config")
def api_grader_config_get():
    agents = sorted(settings.topic_proposal_external_agents or {})
    return jsonify({
        "aspects": _aspect_dicts(),
        "system_prompts": _system_prompts(),
        "providers": agents,
        "external_agent": settings.grader.external_agent,
        "axes": list(AXES),
        "tiers": list(TIERS),
    })


def _coerce_aspect(item) -> dict | None:
    """One posted aspect → a validated dict (builtin flag forced server-side),
    or None when the entry is malformed / keyless."""
    if not isinstance(item, dict) or not str(item.get("key") or "").strip():
        return None
    key = str(item["key"]).strip()
    return GraderAspect(
        key=key, label=str(item.get("label") or key),
        description=str(item.get("description") or ""),
        enabled=bool(item.get("enabled", True)),
        builtin=key in _BUILTIN_KEYS).model_dump()


def _coerce_aspects(raw) -> list[dict]:
    """Validate the posted aspects, then re-assert any missing builtin aspect
    so the grounded-axis aspects can never be deleted."""
    seen: dict[str, dict] = {}
    for item in raw if isinstance(raw, list) else []:
        aspect = _coerce_aspect(item)
        if aspect is not None:
            seen[aspect["key"]] = aspect
    for aspect in settings.grader.aspects:
        if aspect.key in _BUILTIN_KEYS:
            seen.setdefault(aspect.key, aspect.model_dump())
    return list(seen.values())


def _coerce_overrides(raw) -> dict:
    if not isinstance(raw, dict):
        return {}
    return {axis: str(raw[axis]) for axis in AXES
            if axis in raw and str(raw.get(axis) or "").strip()}


def _provider_error(value):
    if value is None:
        return None
    agents = settings.topic_proposal_external_agents or {}
    if value in agents:
        return None
    return f"unknown judge provider {value!r}; configured: {sorted(agents)}"


@grader_config_bp.route("/api/grader/config", methods=["PUT"])
@require_editor
def api_grader_config_put():
    payload = request.get_json(silent=True) or {}
    updates: dict = {}
    if "aspects" in payload:
        updates["aspects"] = _coerce_aspects(payload["aspects"])
    if "system_prompt_overrides" in payload:
        updates["system_prompt_overrides"] = _coerce_overrides(
            payload["system_prompt_overrides"])
    if "external_agent" in payload:
        agent = payload["external_agent"] or None
        err = _provider_error(agent)
        if err is not None:
            return jsonify({"ok": False, "error": err}), 400
        updates["external_agent"] = agent
    if not updates:
        return jsonify({"ok": False, "error": "no recognized fields"}), 400

    grader = (_load_settings().get("grader") or {})
    grader.update(updates)
    save_settings({"grader": grader}, scope="shared")
    return jsonify({"ok": True,
                    "aspects": _aspect_dicts(),
                    "system_prompts": _system_prompts(),
                    "external_agent": settings.grader.external_agent})


__all__ = ["grader_config_bp"]
