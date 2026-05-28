"""Shared helpers for rules endpoints."""

from __future__ import annotations

from lib import rule_engines
from lib.patterns import pattern_scope
from lib.orm import SessionLocal
from lib.orm.models import PatternDoc
from lib.skills import skill_registry
from sqlmodel import select

from web.blueprints import rules as _pkg


def _classify_guide(slug: str | None) -> str | None:
    """Return 'pattern' if `slug` resolves to a PatternDoc, 'auto' if it's
    an auto-skill (or any other known skill_registry entry), else None.

    Used by `_decorate_rule` so the frontend knows whether to link to
    `/patterns/<slug>`, `/skills/<slug>`, or render plain text.
    """
    if not slug:
        return None
    with SessionLocal() as session:
        doc = session.exec(
            select(PatternDoc.id)
            .where(PatternDoc.slug == slug)
            .where(PatternDoc.source_kind == 'pattern')
        ).first()
    if doc is not None:
        return 'pattern'
    try:
        skill_registry.get(slug)
        return 'auto'
    except KeyError:
        return None
    except Exception:
        # Tolerate registry init failures (e.g. test stubs missing
        # `contributed_skills`). Classification falls back to None so
        # the rules page still renders without the auto-skill link.
        return None


def _engine_rule_to_dict(rule, engine) -> dict:
    metadata = dict(getattr(rule, 'metadata', {}) or {})
    guide = metadata.get('guide') or engine.id
    layer = metadata.get('layer') or metadata.get('category') or engine.kind
    # Compute fields go LAST so a metadata None for `guide`/`layer` from a
    # bundle YAML doesn't clobber the non-None fallback the API contract
    # depends on (the /api/rules sort breaks on mixed None/str keys).
    return {
        'id': rule.id,
        'engine': engine.id,
        'triggers': list(rule.triggers),
        'severity': rule.severity,
        'summary': rule.summary,
        'source_file': rule.source_file,
        'disabled': False,
        **metadata,
        'layer': layer,
        'guide': guide,
    }


def _all_rules_index() -> dict:
    grit_payload = _pkg.load_rules_index()
    rules = list(grit_payload.get('rules', []))
    seen = {r.get('id') for r in rules}
    for engine in rule_engines.all_engines():
        if getattr(engine, 'kind', '') == 'grit':
            continue
        for rule in engine.parse_rules():
            if rule.id in seen:
                continue
            seen.add(rule.id)
            rules.append(_engine_rule_to_dict(rule, engine))
    return {
        'version': grit_payload.get('version', 1),
        'rules': rules,
    }

def _engine_descriptor(engine, *, rule_count: int = 0) -> dict:
    language_ids = list(getattr(engine, 'language_ids', ()))
    invocation_hint = ''
    install_hint = ''
    if getattr(engine, 'kind', '') == 'grit':
        invocation_hint = (
            f"grit apply <rule-id> --dry-run --grit-dir {engine.grit_dir} <file>"
        )
        install_hint = 'brew install grit / cargo install grit'
    elif getattr(engine, 'kind', '') == 'bundle':
        invocation_hint = (
            f"regin rules run --engine {engine.id} --rule <rule-id> "
            "--repo <repo-root> --file <relative-path>"
        )
    return {
        'id': engine.id,
        'kind': engine.kind,
        'languages': language_ids,
        'rule_count': rule_count,
        'invocation_hint': invocation_hint,
        'install_hint': install_hint,
    }


def _rule_capabilities(rule: dict, engine) -> dict:
    kind = getattr(engine, 'kind', '')
    if kind == 'grit':
        return {
            'can_edit_metadata': True,
            'can_edit_source': True,
            'can_delete': True,
            'can_show_source': True,
            'can_test_run': True,
        }
    return {
        'can_edit_metadata': False,
        'can_edit_source': False,
        'can_delete': False,
        'can_show_source': False,
        'can_test_run': False,
    }


def _decorate_rule(rule: dict, engines_by_id: dict[str, object]) -> dict:
    out = dict(rule)
    engine_id = out.get('engine', 'grit')
    engine = engines_by_id.get(engine_id)
    engine_kind = getattr(engine, 'kind', engine_id) if engine else engine_id
    out['engine'] = engine_id
    out['engine_kind'] = engine_kind
    out['capabilities'] = _rule_capabilities(out, engine) if engine else {}
    out['scope'] = pattern_scope.describe(out.get('guide'))
    out['guide_kind'] = _classify_guide(out.get('guide'))
    return out
