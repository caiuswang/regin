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


# ── Applicability queries (filesystem scans) ───────────────────

@rules_bp.route('/api/applicable-rules')
def api_applicable_rules():
    """Return all enabled rules with their trigger metadata.

    Query params: repo=<repo-path>
    Returns JSON list of rules that have at least one matching file.
    Each entry: {"id": "rule_id", "applicable": true}

    Trigger checking uses fast filesystem checks (glob via find,
    content via grep) instead of reading all files into memory.
    """
    repo_path = request.args.get('repo')
    if not repo_path:
        return jsonify({'error': 'repo param required'}), 400
    if not os.path.isdir(repo_path):
        return jsonify({'error': f'repo path not found: {repo_path}'}), 404

    data = _pkg.load_rules_index()
    rules = [r for r in data.get('rules', []) if not r.get('disabled')]
    engine = rule_engines.get('grit')

    result = []
    for rule in rules:
        extensions = engine.language_extensions(rule)
        filename_globs, content_triggers = engine._partition_triggers(
            rule.get('triggers', []), extensions,
        )

        if not filename_globs and not content_triggers:
            continue

        if filename_globs:
            found = False
            for g in filename_globs:
                try:
                    proc = subprocess.run(
                        ['find', repo_path, '-name', g, '-type', 'f', '-print', '-quit'],
                        capture_output=True, text=True, timeout=5)
                    if proc.stdout.strip():
                        found = True
                        break
                except (subprocess.TimeoutExpired, OSError):
                    pass
            if not found:
                continue

        if content_triggers:
            include_flags = [f'--include=*{ext}' for ext in extensions] or ['--include=*.java']
            found = False
            for t in content_triggers:
                try:
                    proc = subprocess.run(
                        ['grep', '-rql', t, repo_path, *include_flags],
                        capture_output=True, text=True, timeout=10)
                    if proc.returncode == 0:
                        found = True
                        break
                except (subprocess.TimeoutExpired, OSError):
                    pass
            if not found:
                continue

        result.append({'id': rule['id'], 'applicable': True})

    return jsonify(result)


@rules_bp.route('/api/applicable-files')
def api_applicable_files():
    """Return files in a repo that match a rule's triggers.

    Query params: rule=<rule-id>&repo=<repo-path>
    Returns JSON list of relative file paths.
    """
    rule_id = request.args.get('rule')
    repo_path = request.args.get('repo')
    if not rule_id or not repo_path:
        return jsonify({'error': 'rule and repo params required'}), 400
    if not os.path.isdir(repo_path):
        return jsonify({'error': f'repo path not found: {repo_path}'}), 404

    data = _pkg.load_rules_index()
    rule = None
    for r in data.get('rules', []):
        if r['id'] == rule_id:
            rule = r
            break
    if not rule:
        return jsonify({'error': f'rule {rule_id} not found'}), 404

    engine = rule_engines.get('grit')
    extensions = engine.language_extensions(rule)
    filename_globs, content_triggers = engine._partition_triggers(
        rule.get('triggers', []), extensions,
    )

    def _content_match(trig, content):
        if trig.startswith('@'):
            return trig in content
        return _re.search(r'\b' + _re.escape(trig) + r'\b', content) is not None

    matched = []
    for dirpath, dirnames, filenames in os.walk(repo_path):
        dirnames[:] = [d for d in dirnames if not d.startswith('.') and d not in ('target', 'build', 'node_modules')]
        for fname in filenames:
            if not any(fname.endswith(ext) for ext in extensions):
                continue
            if filename_globs and not any(_fnmatch.fnmatch(fname, g) for g in filename_globs):
                continue
            fpath = os.path.join(dirpath, fname)
            if content_triggers:
                try:
                    with open(fpath, 'r', errors='ignore') as fh:
                        content = fh.read()
                except (OSError, IOError):
                    continue
                if not any(_content_match(t, content) for t in content_triggers):
                    continue
            matched.append(os.path.relpath(fpath, repo_path))

    return jsonify(matched)


