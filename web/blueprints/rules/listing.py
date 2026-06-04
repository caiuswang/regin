"""Rules endpoints split by purpose."""

from __future__ import annotations

import fnmatch as _fnmatch
import os
import re as _re
import subprocess

from flask import request, jsonify

from lib.auth import require_editor, get_current_user
from lib import audit, rule_engines
from lib.settings import settings
from lib.patterns import pattern_scope
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


@rules_bp.route('/api/rules')
def api_rules():
    # Pattern deployments may have changed since the last request; bust
    # the per-pattern scope cache before computing this response.
    pattern_scope.reset_cache()

    data = _all_rules_index()
    engines = rule_engines.all_engines()
    engines_by_id = {engine.id: engine for engine in engines}
    rules = [_decorate_rule(r, engines_by_id) for r in data.get('rules', [])]

    repo_filter = (request.args.get('repo') or '').strip()
    if repo_filter:
        rules = [
            r for r in rules
            if pattern_scope.pattern_allowed_for_repo(r.get('guide'), repo_filter)
        ]

    group_by = request.args.get('by', 'guide')

    groups: dict = {}
    for r in rules:
        key = r['guide'] if group_by == 'guide' else r['layer']
        groups.setdefault(key, []).append(r)
    for k in groups:
        groups[k].sort(key=lambda r: r['id'])
    # Defensive sort: tolerate None group keys (legacy rule dicts may carry
    # `guide=None` or `layer=None` even after the helper defaults; mixed
    # None/str keys would otherwise raise TypeError on `<` comparison).
    grouped = sorted(groups.items(), key=lambda kv: (kv[0] is None, kv[0] or ''))

    return jsonify({
        'grouped': [[k, v] for k, v in grouped],
        'group_by': group_by,
        'repo_filter': repo_filter or None,
        'total': len(rules),
        'engines': [
            _engine_descriptor(engine, rule_count=sum(1 for r in rules if r['engine'] == engine.id))
            for engine in engines
        ],
    })


_LANG_BY_EXT = {
    '.py': 'python',
    '.sh': 'shell',
    '.bash': 'shell',
    '.js': 'javascript',
    '.ts': 'typescript',
    '.rb': 'ruby',
}


@rules_bp.route('/api/pattern-scripts')
def api_pattern_scripts():
    """List every pattern's `scripts/` directory.

    Powers the Rules page "Scripts" tab. For each pattern that has a
    `scripts/` dir, returns the slug + title + per-file metadata. Also
    flags patterns that have linked grit rules so the UI can mention
    the shared runner scripts (`check_grit.sh`,
    `find_applicable_files.py`) that get added to the bundle at
    `pattern promote` time.
    """
    if not os.path.isdir(str(settings.patterns_dir)):
        return jsonify({'patterns': [], 'total_scripts': 0})

    out = []
    total = 0
    for name in sorted(os.listdir(str(settings.patterns_dir))):
        if name.startswith('.') or name.startswith('_'):
            continue
        pattern_dir = os.path.join(str(settings.patterns_dir), name)
        script_dir = os.path.join(pattern_dir, 'scripts')
        skill_md = os.path.join(pattern_dir, 'SKILL.md')
        if not os.path.isdir(script_dir):
            continue
        has_rules = bool(grit_rule_index.rules_for_guide(name))

        title = name
        if os.path.isfile(skill_md):
            try:
                with open(skill_md, 'r', encoding='utf-8') as f:
                    head = f.read(4096)
                m = _re.search(r'^title:\s*"?([^"\n]+?)"?\s*$', head, _re.M)
                if m:
                    title = m.group(1).strip()
            except OSError:
                pass

        files = []
        for root, _, fnames in os.walk(script_dir):
            for fn in sorted(fnames):
                abs_path = os.path.join(root, fn)
                try:
                    size = os.path.getsize(abs_path)
                except OSError:
                    continue
                rel = os.path.relpath(abs_path, script_dir)
                ext = os.path.splitext(fn)[1].lower()
                files.append({
                    'name': rel,
                    'size': size,
                    'language': _LANG_BY_EXT.get(ext, 'text'),
                })
                total += 1

        if not files:
            continue  # empty scripts/ dir → nothing to show

        out.append({
            'slug': name,
            'title': title,
            'own_scripts': files,
            'has_grit_rules': has_rules,
        })

    return jsonify({'patterns': out, 'total_scripts': total})


def _pattern_block_end(src, idx):
    """Index just past the closing brace of the pattern body starting at `idx`.

    Falls back to `idx` if no balanced `{...}` block is found.
    """
    depth = 0
    in_pattern = False
    for i in range(idx, len(src)):
        ch = src[i]
        if ch == '{':
            depth += 1
            in_pattern = True
        elif ch == '}':
            depth -= 1
            if in_pattern and depth == 0:
                return i + 1
    return idx


def _extract_rule_snippet(source_path, rule_id):
    """Return the `pattern <rule_id>(...)` block from `source_path`, or None.

    None when the file is missing or the pattern marker is absent.
    """
    if not os.path.exists(source_path):
        return None
    with open(source_path, 'r') as f:
        src = f.read()
    marker = f"pattern {rule_id}("
    idx = src.find(marker)
    if idx == -1:
        return None
    start = src.rfind('\n\n', 0, idx)
    start = 0 if start == -1 else start + 2
    end = _pattern_block_end(src, idx)
    return src[start:end]


@rules_bp.route('/api/rules/<rule_id>')
def api_rule_detail(rule_id):
    pattern_scope.reset_cache()
    data = _all_rules_index()
    engines = rule_engines.all_engines()
    engines_by_id = {engine.id: engine for engine in engines}
    raw_rule = next((r for r in data.get('rules', []) if r['id'] == rule_id), None)
    rule = _decorate_rule(raw_rule, engines_by_id) if raw_rule else None
    if not rule:
        return jsonify({'error': 'not found'}), 404
    source_path = os.path.join(os.path.dirname(str(settings.patterns_dir)), rule['source_file'])
    source_snippet = _extract_rule_snippet(source_path, rule['id'])
    engine = engines_by_id.get(rule['engine'])
    return jsonify({
        'rule': rule,
        'source_snippet': source_snippet,
        'engine': _engine_descriptor(
            engine, rule_count=sum(1 for r in data.get('rules', []) if r.get('engine', 'grit') == rule['engine'])
        ) if engine else None,
        'ui': {
            'source_label': f"Rule source ({rule['engine_kind']})",
            'source_help': (
                'Rule source for this engine. Keep the metadata headers and rule declaration intact.'
                if rule['capabilities'].get('can_edit_source')
                else ''
            ),
            'automatic_run_description': (
                'The PostToolUse hook evaluates this rule on edited files when its triggers match.'
            ),
        },
    })


