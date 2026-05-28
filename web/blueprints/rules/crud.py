"""Rules endpoints split by purpose."""

from __future__ import annotations

import fnmatch as _fnmatch
import os
import re as _re
import subprocess

from flask import request, jsonify

from lib.auth import require_editor, get_current_user
from lib import audit, rule_engines
from lib.rules import grit_rule_index
from lib.orm import SessionLocal
from lib.orm.models import PlanSession, RuleTrigger
from lib.utils.pagination import clamp_size

from web.blueprints import rules as _pkg
from web.blueprints.rules import rules_bp
from web.blueprints.rules._helpers import (
    _engine_rule_to_dict, _all_rules_index, _engine_descriptor,
    _rule_capabilities, _decorate_rule,
)


# ── Rule CRUD ──────────────────────────────────────────────────

@rules_bp.route('/api/rules/<rule_id>/delete', methods=['POST'])
@require_editor
def api_delete_rule(rule_id):
    """Delete a rule from its .grit source file and regenerate index."""
    data = grit_rule_index.load_rules_index()
    rule = next((r for r in data.get('rules', []) if r['id'] == rule_id), None)
    if not rule:
        return jsonify({'error': 'Rule not found'}), 404

    ok = grit_rule_index.delete_rule(rule_id)
    if not ok:
        return jsonify({'ok': False, 'msg': 'Failed to delete rule from source file'})

    grit_rule_index.regenerate(write_guides=False)
    _pkg.deploy_rules_index_skill(grit_rule_index.RULES_MD_PATH)

    user = get_current_user()
    audit.log_action(
        user['id'] if user else None,
        user['username'] if user else 'anonymous',
        'delete_rule', f'rules/{rule_id}',
    )

    return jsonify({'ok': True, 'msg': f'Deleted rule "{rule_id}"'})


@rules_bp.route('/api/rules/<rule_id>/update', methods=['POST'])
@require_editor
def api_update_rule(rule_id):
    """Update a rule's metadata and/or GritQL source."""
    idx = grit_rule_index.load_rules_index()
    rule = next((r for r in idx.get('rules', []) if r['id'] == rule_id), None)
    if not rule:
        return jsonify({'error': 'Rule not found'}), 404

    data = request.get_json(silent=True) or {}
    allowed = ('summary', 'severity', 'triggers', 'layer', 'guide', 'source')
    updates = {k: v for k, v in data.items() if k in allowed and v is not None}
    if not updates:
        return jsonify({'ok': False, 'msg': 'No fields to update'})

    ok = grit_rule_index.update_rule(rule_id, updates)
    if not ok:
        return jsonify({'ok': False, 'msg': 'Failed to update rule in source file'})

    grit_rule_index.regenerate(write_guides=False)
    _pkg.deploy_rules_index_skill(grit_rule_index.RULES_MD_PATH)

    user = get_current_user()
    audit.log_action(
        user['id'] if user else None,
        user['username'] if user else 'anonymous',
        'edit_rule', f'rules/{rule_id}',
    )

    return jsonify({'ok': True, 'msg': f'Updated rule "{rule_id}"'})
