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

    result = [
        {'id': rule['id'], 'applicable': True}
        for rule in rules
        if _rule_applies(rule, repo_path, engine)
    ]
    return jsonify(result)


def _rule_applies(rule, repo_path, engine):
    """True if `rule` has at least one matching file under `repo_path`.

    A rule with no filename/content triggers never applies. When both
    kinds of trigger are present BOTH must match (AND semantics).
    """
    extensions = engine.language_extensions(rule)
    filename_globs, content_triggers = engine._partition_triggers(
        rule.get('triggers', []), extensions,
    )

    if not filename_globs and not content_triggers:
        return False
    if filename_globs and not _repo_has_glob_match(repo_path, filename_globs):
        return False
    if content_triggers and not _repo_has_content_match(repo_path, content_triggers, extensions):
        return False
    return True


def _repo_has_glob_match(repo_path, filename_globs):
    """True if any file under `repo_path` matches one of `filename_globs`.

    Uses `find` and stops at the first hit (`-print -quit`).
    """
    for g in filename_globs:
        try:
            proc = subprocess.run(
                ['find', repo_path, '-name', g, '-type', 'f', '-print', '-quit'],
                capture_output=True, text=True, timeout=5)
            if proc.stdout.strip():
                return True
        except (subprocess.TimeoutExpired, OSError):
            pass
    return False


def _repo_has_content_match(repo_path, content_triggers, extensions):
    """True if any file under `repo_path` (filtered by `extensions`) contains
    one of `content_triggers`.

    Uses `grep -rql` and stops at the first matching trigger.
    """
    include_flags = [f'--include=*{ext}' for ext in extensions] or ['--include=*.java']
    for t in content_triggers:
        try:
            proc = subprocess.run(
                ['grep', '-rql', t, repo_path, *include_flags],
                capture_output=True, text=True, timeout=10)
            if proc.returncode == 0:
                return True
        except (subprocess.TimeoutExpired, OSError):
            pass
    return False


def _content_match(trig, content):
    """True if `trig` is present in `content`.

    `@`-prefixed triggers (e.g. annotations) match as a substring;
    everything else matches on a word boundary.
    """
    if trig.startswith('@'):
        return trig in content
    return _re.search(r'\b' + _re.escape(trig) + r'\b', content) is not None


def _find_rule(rules, rule_id):
    """Return the first rule whose id is `rule_id`, else None."""
    for r in rules:
        if r['id'] == rule_id:
            return r
    return None


def _file_content_matches(fpath, content_triggers):
    """True if the file at `fpath` satisfies any content trigger.

    Unreadable files (OSError/IOError) are treated as non-matching so
    the caller's walk is not aborted.
    """
    try:
        with open(fpath, 'r', errors='ignore') as fh:
            content = fh.read()
    except (OSError, IOError):
        return False
    return any(_content_match(t, content) for t in content_triggers)


def _file_qualifies(fpath, fname, extensions, filename_globs, content_triggers):
    """True if a single file matches a rule's extension/glob/content triggers."""
    if not any(fname.endswith(ext) for ext in extensions):
        return False
    if filename_globs and not any(_fnmatch.fnmatch(fname, g) for g in filename_globs):
        return False
    if content_triggers and not _file_content_matches(fpath, content_triggers):
        return False
    return True


def _walk_matches(repo_path, extensions, filename_globs, content_triggers):
    """Walk `repo_path` and collect relpaths matching a rule's triggers.

    Skips hidden dirs and target/build/node_modules. A file qualifies
    when its extension is in `extensions`, it matches a filename glob (if
    any), and it satisfies a content trigger (if any).
    """
    matched = []
    for dirpath, dirnames, filenames in os.walk(repo_path):
        dirnames[:] = [d for d in dirnames if not d.startswith('.') and d not in ('target', 'build', 'node_modules')]
        for fname in filenames:
            fpath = os.path.join(dirpath, fname)
            if _file_qualifies(fpath, fname, extensions, filename_globs, content_triggers):
                matched.append(os.path.relpath(fpath, repo_path))
    return matched


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
    rule = _find_rule(data.get('rules', []), rule_id)
    if not rule:
        return jsonify({'error': f'rule {rule_id} not found'}), 404

    engine = rule_engines.get('grit')
    extensions = engine.language_extensions(rule)
    filename_globs, content_triggers = engine._partition_triggers(
        rule.get('triggers', []), extensions,
    )

    matched = _walk_matches(repo_path, extensions, filename_globs, content_triggers)
    return jsonify(matched)


