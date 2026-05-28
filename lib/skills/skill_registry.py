"""Registry of every provider skill managed by regin.

Two kinds of skills live under the active provider's global skills dir
(Claude default: `~/.claude/skills/`):

1. `auto` — generated from another source-of-truth (e.g. `grit-rules` from
   `.grit/patterns/java/*.grit`). Pull is refused; push re-runs the generator.

2. `pattern` — derived from a procedure guide directory at
   `patterns/<procedure_id>/` (containing `SKILL.md` plus optional
   `references/`, `scripts/`, etc.). Pull folds the deployed skill's
   Disciplines + Anti-Patterns sections back into `SKILL.md`; push calls
   `deploy_pattern_as_skill` which copies the whole directory.

Hand-curated standalone skills previously lived at `skills/<id>/` inside
this repo but now live in the sibling `regin-skillhub` project. Manage
them with the `regin-skillhub` CLI.
"""

import os

from lib.settings import settings
from lib.providers import get_active_provider


_DEFAULT_SKILLS_DIR = str(settings.skills_dir)


def _engine_contributed_auto_skills() -> dict:
    """Every configured rule engine may contribute auto-generated skills
    (e.g. GritEngine contributes the `grit-rules` skill). Returns a dict
    keyed by skill id. Returns {} when no engines are configured."""
    skills: dict = {}
    try:
        from lib import rule_engines
        engines = rule_engines.all_engines()
    except Exception:
        return {}
    for engine in engines:
        for contributed in engine.contributed_skills():
            if getattr(engine, 'kind', '') == 'grit':
                source_hint = ', '.join(
                    os.path.relpath(engine.patterns_dir(lid), str(settings.project_root)) + '/*.grit'
                    for lid in engine.language_ids
                )
            else:
                source_hint = engine.id
            source_hint = contributed.get('source_hint', source_hint)
            skills[contributed['id']] = {
                'type': contributed.get('kind', 'auto'),
                'generator': 'rules',
                'source_hint': source_hint,
                'engine_id': engine.id,
            }
    return skills


def _reserved_auto_skill_ids() -> frozenset:
    """Ids that any known engine kind exclusively owns as auto-generated.

    The pattern walk in `_build_skills` must skip these so a stale
    `patterns/<reserved-id>/` directory (e.g. left over from a previous
    `grit-rules` deploy) doesn't resurrect the skill after the engine is
    disabled.
    """
    try:
        from lib.rule_engines import _ENGINE_KINDS
    except Exception:
        return frozenset()
    out: set = set()
    for cls in _ENGINE_KINDS.values():
        fn = getattr(cls, 'reserved_auto_skill_ids', None)
        if fn is None:
            continue
        try:
            out |= set(fn())
        except Exception:
            continue
    return frozenset(out)


def _build_skills():
    skills = _engine_contributed_auto_skills()
    reserved = _reserved_auto_skill_ids()
    patterns_dir = str(settings.patterns_dir)
    if os.path.isdir(patterns_dir):
        for name in sorted(os.listdir(patterns_dir)):
            if name.startswith('_') or name.startswith('.'):
                continue
            skill_md = os.path.join(patterns_dir, name, 'SKILL.md')
            if not os.path.isfile(skill_md):
                continue
            # Reserved auto-skill ids belong to engines; skip them unless
            # an engine instance is currently contributing one (in which
            # case it's already in `skills` and we won't reach here).
            if name in reserved:
                continue
            # Use directory name as skill_id if not already registered;
            # otherwise keep the hard-coded entry.
            if name not in skills:
                skills[name] = {'type': 'pattern', 'procedure_id': name}
    return skills


SKILLS = _build_skills()


def _request_scoped_skills() -> dict:
    """Return `_build_skills()`, memoized for the lifetime of one Flask
    request when called inside an active request context. Outside Flask
    (CLI invocations, background tasks, tests without a request context)
    falls through to a fresh build every time.

    Each request triggers many internal `get(skill_id)` calls — including
    deeply via `source_path` / `source_exists` / `deployed_exists` — and
    naively rebuilding the dict per call previously cost ~50× per
    `GET /api/patterns`. The cache is request-scoped so newly-created
    patterns are visible on the next request without explicit invalidation.
    """
    try:
        from flask import g, has_request_context
    except ImportError:
        return _build_skills()
    if not has_request_context():
        return _build_skills()
    cached = getattr(g, '_regin_skills_cache', None)
    if cached is None:
        cached = _build_skills()
        g._regin_skills_cache = cached
    return cached


def get(skill_id):
    # Refresh on every (non-cached) lookup so newly-created patterns are
    # found without restarting the Flask dev server.
    skills = _request_scoped_skills()
    if skill_id not in skills:
        raise KeyError(f"unknown skill id: {skill_id}")
    return skills[skill_id]


def all_ids():
    return list(_request_scoped_skills().keys())


def snapshot() -> dict:
    """Return one `{skill_id: entry}` dict.

    Same as the result of an internal `_build_skills` call but participates
    in the per-request cache so callers can hold the reference and pass it
    around safely. Outside a Flask request context every call rebuilds.
    """
    return _request_scoped_skills()


def skill_id_for_procedure(procedure_id, snapshot_dict: dict | None = None):
    """Reverse lookup: given a pattern procedure_id, return the skill id that
    wraps it. Accepts an optional pre-built `snapshot_dict` to skip the
    per-call dict rebuild (see `snapshot()`)."""
    skills = snapshot_dict if snapshot_dict is not None else _request_scoped_skills()
    for skill_id, entry in skills.items():
        if entry.get('type') == 'pattern' and entry.get('procedure_id') == procedure_id:
            return skill_id
    return None


def procedure_to_skill_id_map(snapshot_dict: dict | None = None) -> dict:
    """One-shot reverse index of every pattern entry's procedure_id → skill_id.

    Cheaper than calling `skill_id_for_procedure` N times when listing every
    pattern in a request.
    """
    skills = snapshot_dict if snapshot_dict is not None else _request_scoped_skills()
    return {
        entry['procedure_id']: sid
        for sid, entry in skills.items()
        if entry.get('type') == 'pattern' and entry.get('procedure_id')
    }


def deployed_path(skill_id):
    """Absolute path of the deployed skill directory under active provider."""
    # Back-compat: tests and legacy call sites monkey-patch SKILLS_DIR.
    if str(settings.skills_dir) != _DEFAULT_SKILLS_DIR:
        base = str(settings.skills_dir)
    else:
        base = str(get_active_provider().global_skills_dir())
    return os.path.join(base, skill_id)


def deployed_skill_md(skill_id):
    return os.path.join(deployed_path(skill_id), 'SKILL.md')


def source_path(skill_id):
    """Absolute path of the regin source for a skill.

    For `pattern` skills this is the directory `patterns/<procedure_id>/`.
    For `auto` skills this is the generator hint (not a single file).
    """
    entry = get(skill_id)
    if entry['type'] == 'pattern':
        return os.path.join(str(settings.patterns_dir), entry['procedure_id'])
    if entry['type'] == 'auto':
        return os.path.join(str(settings.project_root), entry['source_hint'])
    raise ValueError(f"unknown type for {skill_id}: {entry['type']}")


def source_skill_md(skill_id):
    """For pattern skills, the path to SKILL.md inside the source directory."""
    return os.path.join(source_path(skill_id), 'SKILL.md')


def deployed_exists(skill_id):
    entry = get(skill_id)
    if entry['type'] == 'pattern':
        return os.path.isdir(deployed_path(skill_id)) and os.path.isfile(deployed_skill_md(skill_id))
    return os.path.isfile(deployed_skill_md(skill_id))


def source_exists(skill_id):
    entry = get(skill_id)
    if entry['type'] == 'pattern':
        return os.path.isfile(source_skill_md(skill_id))
    if entry['type'] == 'auto':
        # auto sources are always "present" since they're generators.
        return True
    return False
