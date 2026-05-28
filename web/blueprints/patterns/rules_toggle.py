"""Pattern endpoints split by purpose."""

from __future__ import annotations

import hashlib
import os
import re
import shutil
from datetime import datetime, timezone

import yaml
from flask import abort, jsonify, make_response, request
from sqlalchemy import func
from sqlmodel import select

from lib import rule_engines
from lib.auth import get_current_user, require_editor
from web.blueprints import patterns as _pkg
from lib.rules import engine_rule_disable, grit_rule_index
from lib.rules.grit_rule_index import RULES_MD_PATH, rules_for_guide
from lib.orm import SessionLocal
from lib.orm.models import DocTag, PatternDeployment, PatternDoc, Repo, Tag

from lib import audit, experiments
from lib.patterns import pattern_deployments
from lib.skills import skill_registry, skill_sync

from web.blueprints.patterns import patterns_bp
from web.blueprints.patterns._helpers import (
    _get_pattern_or_404, _pattern_to_dict, _now_iso,
    _attached_rule_bundles_for_pattern,
)


# ── Enable/disable linked rules ────────────────────────────────

def _toggle_pattern_rules(slug: str, disabled: bool, verb: str):
    rules = grit_rule_index.rules_for_guide(slug)
    if not rules:
        return jsonify({"ok": False, "msg": "no rules linked"})

    payload = request.get_json(silent=True) or {}
    requested = payload.get("rule_ids")
    if requested:
        linked_ids = {r["id"] for r in rules}
        ids = [rid for rid in requested if rid in linked_ids]
        if not ids:
            return jsonify({"ok": False, "msg": "no requested rules belong to this pattern"})
    else:
        ids = [r["id"] for r in rules]

    grit_rule_index.set_rules_disabled(ids, disabled)

    deploy_note = ""
    if not disabled:
        skill_id = skill_registry.skill_id_for_procedure(slug)
        if skill_id and not skill_registry.deployed_exists(skill_id):
            try:
                msg = skill_sync.push(skill_id, force=True)
                deploy_note = f" (deployed skill: {msg})"
            except Exception as e:
                return jsonify({
                    "ok": False,
                    "msg": f"enabled {len(ids)} rule(s) but skill deploy failed: {e}",
                }), 500

    grit_rule_index.regenerate(write_guides=False)
    _pkg.deploy_rules_index_skill(RULES_MD_PATH)
    return jsonify({"ok": True, "msg": f'{verb} {len(ids)} rule(s): {", ".join(ids)}{deploy_note}'})


@patterns_bp.route("/api/patterns/<path:slug>/rules/disable", methods=["POST"])
@require_editor
def api_disable_pattern_rules(slug):
    return _toggle_pattern_rules(slug, True, "disabled")


@patterns_bp.route("/api/patterns/<path:slug>/rules/enable", methods=["POST"])
@require_editor
def api_enable_pattern_rules(slug):
    return _toggle_pattern_rules(slug, False, "enabled")


@patterns_bp.route("/api/patterns/<path:slug>/bundle-rules/<action>", methods=["POST"])
@require_editor
def api_toggle_bundle_rules(slug, action):
    if action not in ("disable", "enable"):
        return jsonify({"ok": False, "msg": "action must be disable or enable"}), 400
    payload = request.get_json(silent=True) or {}
    engine_id = payload.get("engine_id")
    rule_ids = payload.get("rule_ids") or []
    if not engine_id:
        return jsonify({"ok": False, "msg": "engine_id required"}), 400
    if not isinstance(rule_ids, list) or not rule_ids:
        return jsonify({"ok": False, "msg": "rule_ids must be a non-empty list"}), 400

    valid_ids = {
        r.id for engine in rule_engines.all_engines()
        if engine.id == engine_id
        for r in engine.parse_rules()
    }
    requested = [rid for rid in rule_ids if rid in valid_ids]
    if not requested:
        return jsonify({"ok": False, "msg": f"no rule_ids belong to engine '{engine_id}'"}), 400

    engine_rule_disable.set_disabled(engine_id, requested, action == "disable")
    verb = "disabled" if action == "disable" else "enabled"
    return jsonify({
        "ok": True,
        "msg": f"{verb} {len(requested)} {engine_id} rule(s): {', '.join(requested)}",
    })


