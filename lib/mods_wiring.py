"""regin's own Claude Code mods (`regin-plugin/plugins/*`) as installed wiring.

A mod only runs once its marketplace is known to the CLI and the plugin is
switched on in `enabledPlugins`. Both live in the same user settings file the
hook entries do, so mods are checked and written beside them rather than left
to a manual `/plugin install` that nothing reports on.
"""

from __future__ import annotations

import json
from pathlib import Path

from lib.hooks_wiring import _unreadable_error, read_json_settings, write_json_settings
from lib.settings import settings

MARKETPLACE = 'regin-local'


def supports_mods(provider) -> bool:
    return bool(getattr(provider, 'supports_mods', False))


def marketplace_root() -> Path:
    return Path(settings.project_root) / 'regin-plugin'


def mod_names() -> list[str]:
    manifest = marketplace_root() / '.claude-plugin' / 'marketplace.json'
    try:
        plugins = json.loads(manifest.read_text(encoding='utf-8')).get('plugins') or []
    except (OSError, ValueError):
        return []
    return [p['name'] for p in plugins if isinstance(p, dict) and p.get('name')]


def plugin_key(name: str) -> str:
    return f'{name}@{MARKETPLACE}'


def _marketplace_entry() -> dict:
    return {'source': {'source': 'directory', 'path': str(marketplace_root())}}


def _as_dict(value) -> dict:
    return value if isinstance(value, dict) else {}


def _registered_source(data: dict) -> dict:
    entry = _as_dict(_as_dict(data.get('extraKnownMarketplaces')).get(MARKETPLACE))
    return _as_dict(entry.get('source'))


def foreign_root(data: dict) -> str | None:
    """The other checkout `regin-local` points at, if it is not this one.

    The marketplace key is user-wide, so a second regin checkout (or a
    worktree) that rewrote it would unload every mod once that checkout goes
    away — the same reason hook entries of another checkout are adopted only
    on request, never repaired over.
    """
    source = _registered_source(data)
    if not source or source == _marketplace_entry()['source']:
        return None
    return str(source.get('path') or source)


def _mod_state(enabled_map: dict, name: str) -> str:
    key = plugin_key(name)
    if key not in enabled_map:
        return 'missing'
    return 'enabled' if enabled_map[key] is True else 'disabled'


def _mod_states(enabled_map: dict) -> dict[str, str]:
    return {n: _mod_state(enabled_map, n) for n in mod_names()}


def _split_states(states: dict[str, str]) -> tuple[list[str], list[str], list[str]]:
    return tuple([n for n, s in states.items() if s == want]
                 for want in ('enabled', 'disabled', 'missing'))


def _marketplace_state(data: dict) -> tuple[bool, list[str], list[str]]:
    """(registered here, [other checkout's path], [drift label if unregistered])."""
    registered = _registered_source(data) == _marketplace_entry()['source']
    other = foreign_root(data)
    foreign = [other] if other else []
    unregistered = [] if registered or foreign else [f'marketplace {MARKETPLACE}']
    return registered, foreign, unregistered


def mods_status(settings_path: str) -> dict:
    """Marketplace registration plus each mod's enabled / disabled / missing state.

    Carries the same row keys as a hook-wiring kind so `regin hooks status`
    and `repair` render and act on it without a second code path. Like the
    hook rows, nothing installed at all reads as *not installed* (install's
    job), and only a partial install reads as *stale* (repair's job).
    """
    data = read_json_settings(settings_path)
    enabled, disabled, missing = _split_states(_mod_states(_as_dict(data.get('enabledPlugins'))))
    registered, foreign, unregistered = _marketplace_state(data)
    installed = registered or bool(enabled or disabled)
    return {
        'installed': installed,
        'stale': installed and not foreign and bool(disabled or missing or unregistered),
        'marketplace_registered': registered,
        'enabled': enabled,
        'disabled': disabled,
        'missing': missing,
        'routed_events': enabled,
        'stale_events': disabled + unregistered,
        'missing_events': missing,
        'foreign_events': [f'marketplace {MARKETPLACE}' for _ in foreign],
        'foreign_roots': foreign,
    }


def install_mods(provider, settings_path: str, *, adopt: bool = False) -> dict:
    """Register `regin-local` and enable every mod it lists.

    A mod the user switched off is switched back on: install (and repair,
    which calls it) writes the state `status` checks against, the same way a
    hand-edited hook command is rewritten. A marketplace registered to
    another checkout is refused unless adopting.
    """
    if error := _unreadable_error(settings_path):
        return error
    names = mod_names()
    if not names:
        return {'ok': False, 'msg': f'no mods listed in {marketplace_root()}/.claude-plugin/marketplace.json'}
    data = read_json_settings(settings_path)
    if (other := foreign_root(data)) and not adopt:
        return {'ok': False, 'msg': f'{MARKETPLACE} is registered to {other} — run '
                                    f'`regin hooks adopt --provider {provider.provider_id}` '
                                    'to point it at this checkout'}
    before = json.dumps(data, sort_keys=True)
    marketplaces = _as_dict(data.get('extraKnownMarketplaces'))
    marketplaces[MARKETPLACE] = _marketplace_entry()
    data['extraKnownMarketplaces'] = marketplaces
    enabled = _as_dict(data.get('enabledPlugins'))
    enabled.update({plugin_key(n): True for n in names})
    data['enabledPlugins'] = enabled
    if json.dumps(data, sort_keys=True) == before:
        return {'ok': True, 'msg': f'{len(names)} mods already enabled'}
    write_json_settings(data, settings_path)
    return {'ok': True, 'msg': f'{MARKETPLACE} registered, {len(names)} mods enabled '
                               f'({", ".join(names)})'}


def adopt_mods(provider, settings_path: str) -> dict:
    return install_mods(provider, settings_path, adopt=True)
