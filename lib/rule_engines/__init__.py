"""Rule-engine registry.

Engines come from two sources, merged with explicit-wins precedence:

1. Explicit `settings.rule_engines` entries (`enabled=True`).
2. Auto-discovered bundles: when `settings.bundle_autoload` is true,
   `settings.patterns_dir/*/regin-bundle.{yaml,json}` is scanned and each
   well-formed manifest spawns a `BundleEngine`. An auto-discovered
   bundle is skipped if its id collides with one already loaded by (1).

If both sources are empty regin runs as a generic harness with no
rule enforcement at all.
"""

from __future__ import annotations

from pathlib import Path
from typing import Type

from lib.logging_setup import get_logger
from lib.rule_engines.base import Rule, RuleEngine, Violation
from lib.rule_engines.bundle import BundleEngine
from lib.rule_engines.grit import GritEngine
from lib.rule_engines.manifest import discover_bundles, load_manifest, manifest_path
from lib.rule_engines.radon_engine import RadonEngine
# Resolve `settings` dynamically (via the module) each call — tests
# monkeypatch `lib.settings.settings` and `reload_settings()` can swap it.
from lib import settings as _settings_mod


_log = get_logger(__name__)

_ENGINE_KINDS: dict[str, Type] = {
    'grit': GritEngine,
    'bundle': BundleEngine,
    'radon': RadonEngine,
}

def _current_settings():
    return _settings_mod.settings


def _build_engine(cfg) -> RuleEngine:
    import os as _os
    kind = cfg.kind
    if kind not in _ENGINE_KINDS:
        raise ValueError(f"unknown rule engine kind: {kind}")
    if kind == 'grit':
        raw = cfg.grit_dir if cfg.grit_dir else _current_settings().grit_dir
        # Expand `~` and env vars — settings.json may contain literal
        # tildes that pydantic preserves in Path objects.
        grit_dir = _os.path.expanduser(str(raw))
        return GritEngine(
            id=cfg.id,
            grit_dir=grit_dir,
            language_ids=tuple(cfg.language_ids),
            project_root=str(_settings_mod.settings.project_root),
        )
    if kind == 'bundle':
        if cfg.bundle_root is None:
            raise ValueError(
                f"rule_engines entry {cfg.id!r}: kind='bundle' requires bundle_root",
            )
        bundle_root = Path(_os.path.expanduser(str(cfg.bundle_root))).resolve()
        mpath = manifest_path(bundle_root)
        if mpath is None:
            raise ValueError(
                f"bundle {cfg.id!r} at {bundle_root} has no regin-bundle.{{yaml,json}}",
            )
        manifest = load_manifest(mpath)
        return BundleEngine(
            id=cfg.id,
            bundle_root=bundle_root,
            manifest=manifest,
        )
    if kind == 'radon':
        kwargs: dict = {
            'id': cfg.id,
            'language_ids': tuple(cfg.language_ids) or ('python',),
            'project_root': str(_settings_mod.settings.project_root),
        }
        if cfg.min_grade is not None:
            kwargs['min_grade'] = cfg.min_grade
        if cfg.severity is not None:
            kwargs['severity'] = cfg.severity
        return RadonEngine(**kwargs)
    # Unreachable — _ENGINE_KINDS membership has already been checked.
    raise ValueError(f"unhandled kind: {kind}")


def _discovered_bundle_engines(already_taken_ids: set[str]) -> dict[str, RuleEngine]:
    """Scan `settings.patterns_dir` for self-describing rule bundles.

    Skips bundles whose manifest id collides with an explicit-config
    engine: explicit always wins. Disabled silently when
    `settings.bundle_autoload` is false.
    """
    s = _current_settings()
    if not s.bundle_autoload:
        return {}
    out: dict[str, RuleEngine] = {}
    for bundle_root, manifest in discover_bundles(Path(s.patterns_dir)):
        if manifest.id in already_taken_ids or manifest.id in out:
            _log.info(
                'rule_engines.bundle.skipped_collision',
                bundle=str(bundle_root), id=manifest.id,
            )
            continue
        out[manifest.id] = BundleEngine(
            id=manifest.id,
            bundle_root=bundle_root,
            manifest=manifest,
        )
    return out


def _load_engines() -> dict[str, RuleEngine]:
    configured = [c for c in _current_settings().rule_engines if c.enabled]
    engines: dict[str, RuleEngine] = {}
    for c in configured:
        try:
            engines[c.id] = _build_engine(c)
        except Exception:
            # A `kind=bundle` engine whose bundle_root was removed (e.g. its
            # pattern was deleted) must not take down the whole registry —
            # that would 500 every page that calls all_engines() (rules,
            # pattern detail, settings). Skip the dangling bundle and keep the
            # healthy engines. Other kinds (typo'd `kind`, etc.) stay loud:
            # those are config errors the user should see and fix.
            if c.kind != 'bundle':
                raise
            _log.warning(
                'rule_engines.bundle.build_failed', id=c.id,
                bundle_root=str(c.bundle_root), exc_info=True,
            )
    discovered = _discovered_bundle_engines(set(engines.keys()))
    engines.update(discovered)
    return engines


def invalidate_cache() -> None:
    """Kept for API stability; engines are no longer cached."""
    return None


def get(engine_id: str) -> RuleEngine:
    engines = _load_engines()
    if engine_id not in engines:
        raise KeyError(engine_id)
    return engines[engine_id]


def all_engines() -> list[RuleEngine]:
    return list(_load_engines().values())


def _cfg_to_dict(c) -> dict:
    """Serialize a RuleEngineConfig back to the lean dict shape used in
    settings.json — only fields that are meaningfully set, so a round-trip
    doesn't litter the file with nulls/defaults."""
    d: dict = {'id': c.id, 'kind': c.kind}
    if not c.enabled:
        d['enabled'] = False
    if c.grit_dir is not None:
        d['grit_dir'] = str(c.grit_dir)
    if c.bundle_root is not None:
        d['bundle_root'] = str(c.bundle_root)
    if c.language_ids:
        d['language_ids'] = list(c.language_ids)
    if c.min_grade is not None:
        d['min_grade'] = c.min_grade
    if c.severity is not None:
        d['severity'] = c.severity
    return d


def _shared_rule_engines_dicts() -> list[dict]:
    """Return the `rule_engines` list as plain dicts, preserving the on-disk
    shape of settings.json. Falls back to materializing the typed config
    when the file has no explicit `rule_engines` (defaults live in code)."""
    import json as _json
    from lib.settings import SETTINGS_PATH
    try:
        with open(SETTINGS_PATH) as f:
            raw = _json.load(f)
    except (FileNotFoundError, _json.JSONDecodeError, OSError):
        raw = {}
    engines = raw.get('rule_engines')
    if isinstance(engines, list) and engines:
        return engines
    return [_cfg_to_dict(c) for c in _current_settings().rule_engines]


def _missing_grit_languages(wanted: list[str]) -> list[str] | None:
    """Languages in `wanted` not already registered on any configured grit
    engine. Returns None when no grit engine is configured at all."""
    grit_cfgs = [c for c in _current_settings().rule_engines if c.kind == 'grit']
    if not grit_cfgs:
        return None
    already: set[str] = set()
    for c in grit_cfgs:
        already.update(c.language_ids)
    return [lang for lang in wanted if lang not in already]


def _add_langs_to_grit_entry(engines: list[dict], to_add: list[str]) -> list[str]:
    """Append `to_add` languages to the first grit entry in `engines`
    (mutating it in place). Returns the languages actually added."""
    added: list[str] = []
    for entry in engines:
        if entry.get('kind', 'grit') != 'grit':
            continue
        lang_list = list(entry.get('language_ids') or [])
        for lang in to_add:
            if lang not in lang_list:
                lang_list.append(lang)
                added.append(lang)
        entry['language_ids'] = lang_list
        break
    return added


def ensure_grit_languages(langs) -> list[str]:
    """Ensure each language in `langs` is registered on the configured grit
    engine, persisting any additions to settings.json (shared scope).

    Used by the pattern importer when a bundle ships grit rules for a
    language the grit engine isn't yet configured for (e.g. Java rules on a
    python-only engine): without this the rules would never be parsed by
    `regenerate()` nor enforced by the PostToolUse hook. The change is
    additive — existing languages are never removed.

    `save_settings` refreshes the `settings` singleton in place, and the
    engine registry is rebuilt on every `get()`, so the new `language_ids`
    take effect in-process. Callers that read the cached dir constants in
    `lib.rules.grit_rule_index` should also call `refresh_language_dirs()`.

    Returns the languages newly added (empty if all already enabled, or no
    grit engine is configured).
    """
    from lib.settings import save_settings

    wanted = [lang for lang in dict.fromkeys(langs) if lang]
    if not wanted:
        return []
    to_add = _missing_grit_languages(wanted)
    if not to_add:  # None = no grit engine, [] = all already enabled
        return []

    engines = _shared_rule_engines_dicts()
    added = _add_langs_to_grit_entry(engines, to_add)
    if not added:
        return []
    save_settings({'rule_engines': engines}, scope='shared')
    _log.info('rule_engines.languages_enabled', added=added)
    return added


__all__ = [
    'Rule', 'Violation', 'RuleEngine', 'GritEngine', 'BundleEngine',
    'RadonEngine',
    'get', 'all_engines', 'invalidate_cache', 'ensure_grit_languages',
]
