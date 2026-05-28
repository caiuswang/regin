"""CRUD + lookup endpoints for user-managed prompt templates."""

from __future__ import annotations

from flask import Blueprint, jsonify, request

from lib.auth import require_editor
from lib.prompt_templates import (
    PromptTemplateError,
    create_template,
    delete_template,
    get_template_by_slug,
    list_templates,
    update_template,
)


prompt_templates_bp = Blueprint("prompt_templates", __name__)


def _err(exc: Exception, status: int = 400):
    return jsonify({"ok": False, "error": str(exc)}), status


@prompt_templates_bp.route("/api/prompt-templates")
def api_prompt_templates_list():
    return jsonify({"ok": True, "templates": list_templates()})


@prompt_templates_bp.route("/api/prompt-templates/<slug>")
def api_prompt_templates_get(slug: str):
    template = get_template_by_slug(slug)
    if template is None:
        return jsonify({"ok": False, "error": "not found"}), 404
    return jsonify({"ok": True, "template": template})


@prompt_templates_bp.route("/api/prompt-templates", methods=["POST"])
@require_editor
def api_prompt_templates_create():
    payload = request.get_json(silent=True) or {}
    try:
        template = create_template(payload)
    except PromptTemplateError as exc:
        return _err(exc)
    return jsonify({"ok": True, "template": template})


@prompt_templates_bp.route("/api/prompt-templates/<slug>", methods=["PATCH"])
@require_editor
def api_prompt_templates_update(slug: str):
    payload = request.get_json(silent=True) or {}
    try:
        template = update_template(slug, payload)
    except PromptTemplateError as exc:
        status = 404 if "not found" in str(exc) else 400
        return _err(exc, status)
    return jsonify({"ok": True, "template": template})


@prompt_templates_bp.route("/api/prompt-templates/<slug>", methods=["DELETE"])
@require_editor
def api_prompt_templates_delete(slug: str):
    try:
        template = delete_template(slug)
    except PromptTemplateError as exc:
        status = 404 if "not found" in str(exc) else 400
        return _err(exc, status)
    return jsonify({"ok": True, "template": template})


__all__ = ["prompt_templates_bp"]
