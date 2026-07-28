"""Expose every configured rule engine via `/api/rule-engines`.

The Vue frontend reads this to render engine-agnostic chrome (the
Rules / Rule-detail / Skills views used to hardcode GritQL/.java
references). A deployment with zero engines configured returns `[]`
and the UI hides all lint-related panels.
"""

from __future__ import annotations

from flask import Blueprint, jsonify

from lib import rule_engines


rule_engines_bp = Blueprint('rule_engines', __name__)


@rule_engines_bp.route('/api/rule-engines')
def api_rule_engines():
    payload = []
    for engine in rule_engines.all_engines():
        language_ids = list(getattr(engine, 'language_ids', ()))
        try:
            rule_count = len(engine.parse_rules())
        except Exception:
            rule_count = 0
        origin_repo = getattr(engine, 'origin_repo_name', None)
        payload.append({
            'id': engine.id,
            'kind': engine.kind,
            'languages': language_ids,
            'rule_count': rule_count,
            'invocation_hint': _invocation_hint_for(engine),
            'install_hint': _install_hint_for(engine),
            # Where the rules live, and — for repo-shipped ones — whether
            # they may execute. A central engine is always `trusted`; it
            # came from config the user controls, not from a cloned repo.
            'origin': origin_repo or 'central',
            'origin_repo': origin_repo,
            'trusted': bool(getattr(engine, 'trusted', True)),
        })
    return jsonify(payload)


def _invocation_hint_for(engine) -> str:
    """Short bash snippet the UI shows in rule-detail. Engine-specific."""
    if getattr(engine, 'kind', '') == 'grit':
        return f"grit apply <rule-id> --dry-run --grit-dir {engine.grit_dir} <file>"
    if getattr(engine, 'kind', '') == 'bundle':
        return f'regin rules run --engine {engine.id} --rule <rule-id> --repo <repo-root> --file <relative-path>'
    if getattr(engine, 'kind', '') == 'radon':
        return f"radon cc -s --min {engine.min_grade} <file.py>"
    return ''


def _install_hint_for(engine) -> str:
    """Install instruction the SettingsView shows when the engine is
    configured but its binary is missing."""
    if getattr(engine, 'kind', '') == 'grit':
        return 'brew install grit / cargo install grit'
    if getattr(engine, 'kind', '') == 'bundle':
        return 'Run the bundle runner deps once (e.g. `npm install` in the bundle root)'
    if getattr(engine, 'kind', '') == 'radon':
        return 'pip install radon>=6.0  (already declared in pyproject.toml)'
    return ''
