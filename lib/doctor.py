"""Environment health checks used by CLI and web UI."""

import json
import os
import re
import shutil
import subprocess

from lib.providers import get_active_provider, provider_capability_rows
from lib.settings import settings as _settings


def _which(cmd: str) -> str | None:
    return shutil.which(cmd)


def _scan_hook_scripts() -> list[dict]:
    """Scan active-provider hook settings for script hook commands.
    path (e.g. `python /path/to/foo.py`). Returns one entry per command with
    {event, script, present}. Commands using `-m <module>` form (the unified
    hook_manager router) are skipped since there is no file path to check.
    """
    provider = get_active_provider()
    if provider.provider_id == "claude":
        # Keep HOME-sensitive behavior for existing tests and setups.
        settings_path = os.path.expanduser('~/.claude/settings.json')
    else:
        settings_path = str(provider.hook_settings_path())
    try:
        with open(settings_path, 'r') as f:
            settings = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return []

    results: list[dict] = []
    script_re = re.compile(r'(/[^\s]+\.(?:py|sh))(?:\s|$)')
    for event, entries in settings.get('hooks', {}).items():
        if not isinstance(entries, list):
            continue
        for entry in entries:
            for h in entry.get('hooks', []):
                cmd = h.get('command', '')
                if ' -m ' in cmd:  # module-invocation form — no file to check
                    continue
                m = script_re.search(cmd)
                if not m:
                    continue
                script = m.group(1)
                results.append({
                    'event': event,
                    'script': script,
                    'present': os.path.isfile(script),
                })
    return results


def _check_tool(name: str, cmd: list[str] | None = None) -> dict:
    path = _which(name)
    version = _version(cmd) if (path and cmd) else ''
    return {'present': bool(path), 'path': path, 'version': version}


def _check_module(module_name: str) -> dict:
    try:
        mod = __import__(module_name)
    except ImportError:
        return {'present': False, 'path': None, 'version': ''}
    return {
        'present': True,
        'path': getattr(mod, '__file__', None),
        'version': getattr(mod, '__version__', '') or '',
    }


def _check_fts5() -> dict:
    """Probe whether stdlib sqlite3 was compiled with FTS5 — required by
    the hybrid pattern search's lexical leg.
    """
    import sqlite3
    try:
        conn = sqlite3.connect(':memory:')
        try:
            conn.execute("CREATE VIRTUAL TABLE _fts5_probe USING fts5(x)")
        finally:
            conn.close()
        return {'present': True, 'path': None, 'version': sqlite3.sqlite_version}
    except sqlite3.OperationalError:
        return {'present': False, 'path': None, 'version': sqlite3.sqlite_version}


def _check_playwright() -> dict:
    path = _which('playwright')
    if path:
        return {'present': True, 'path': path, 'version': _version(['playwright', '--version'])}
    if not _which('npx'):
        return {'present': False, 'path': None, 'version': ''}
    try:
        ok = subprocess.run(
            ['npx', 'playwright', '--version'],
            capture_output=True, text=True, timeout=10
        ).returncode == 0
    except (subprocess.TimeoutExpired, FileNotFoundError):
        ok = False
    return {'present': ok, 'path': None, 'version': ''}


def _torch_device() -> dict:
    try:
        import torch
        if torch.cuda.is_available():
            label = 'cuda'
        elif torch.backends.mps.is_available():
            label = 'mps'
        else:
            label = 'cpu'
        return {'present': True, 'path': None, 'version': label}
    except Exception:
        return {'present': False, 'path': None, 'version': ''}


def _router_group_items() -> list[dict]:
    torch_info = _check_module('torch')
    return [
        {'id': 'rt_torch', 'label': 'torch', **torch_info, 'optional': True,
         'install_hint': 'pip install -r requirements-router.txt'},
        {'id': 'rt_transformers', 'label': 'transformers', **_check_module('transformers'),
         'optional': True, 'install_hint': 'pip install -r requirements-router.txt'},
        {'id': 'rt_socksio', 'label': 'socksio (for SOCKS proxy)', **_check_module('socksio'),
         'optional': True, 'install_hint': "pip install 'httpx[socks]'"},
        {'id': 'rt_device', 'label': 'torch device',
         **(_torch_device() if torch_info['present']
            else {'present': False, 'path': None, 'version': ''}),
         'optional': True},
    ]


def _mysql_ok() -> bool:
    try:
        from lib.settings import settings
        if settings.mode == 'standalone':
            return True
        from lib.mysql_db import is_configured
        return is_configured()
    except Exception:
        return False


def _is_writable_dir(path) -> bool:
    """True iff `path` exists, is a directory, and the process can write to it."""
    p = str(path)
    return os.path.isdir(p) and os.access(p, os.W_OK)


def _hook_settings_parseable(provider) -> dict:
    """Whether the active provider's hook settings file is valid JSON."""
    if provider.provider_id == "claude":
        path = os.path.expanduser('~/.claude/settings.json')
    else:
        path = str(provider.hook_settings_path())
    if not os.path.isfile(path):
        return {'present': False, 'path': path, 'version': 'missing'}
    try:
        with open(path, 'r') as f:
            json.load(f)
        return {'present': True, 'path': path, 'version': ''}
    except json.JSONDecodeError as e:
        return {'present': False, 'path': path, 'version': f'invalid JSON: {e.msg}'}


def _active_provider_items(provider, check_tool) -> list[dict]:
    """Items for the Active provider doctor group."""
    items: list[dict] = []
    pid = provider.provider_id
    if pid not in ('generic', 'unknown'):
        items.append({
            'id': f'provider_cli_{pid}',
            'label': f'{pid} CLI',
            **check_tool(pid, [pid, '--version']),
            'install_hint': f'install the {provider.display_name} CLI and ensure `{pid}` is on PATH',
        })
    items.append({
        'id': 'provider_hook_settings',
        'label': 'hook settings parseable',
        **_hook_settings_parseable(provider),
    })
    return items


def _grit_engine_items(cfg, check_tool) -> list[dict]:
    langs = ', '.join(cfg.language_ids) if cfg.language_ids else 'none'
    return [{
        'id': f'engine_{cfg.id}_grit',
        'label': f'{cfg.id}: grit ({langs})',
        **check_tool('grit', ['grit', '--version']),
        'install_hint': 'https://docs.grit.io/cli/quickstart',
    }]


def _radon_engine_items(cfg, check_module) -> list[dict]:
    langs = ', '.join(cfg.language_ids) if cfg.language_ids else 'python'
    return [{
        'id': f'engine_{cfg.id}_radon',
        'label': f'{cfg.id}: radon ({langs})',
        **check_module('radon'),
        'install_hint': 'pip install radon',
    }]


def _bundle_engine_items(cfg) -> list[dict]:
    present = bool(cfg.bundle_root) and os.path.isdir(str(cfg.bundle_root))
    return [{
        'id': f'engine_{cfg.id}_bundle',
        'label': f'{cfg.id}: bundle_root',
        'present': present,
        'path': str(cfg.bundle_root) if cfg.bundle_root else None,
        'version': '',
    }]


def _unknown_engine_items(cfg) -> list[dict]:
    return [{
        'id': f'engine_{cfg.id}_unknown',
        'label': f'{cfg.id}: kind={cfg.kind!r} (unrecognised)',
        'present': False, 'path': None, 'version': '',
    }]


def _effective_rule_engine_configs() -> list:
    """Configured engines with the legacy grit_dir fallback applied."""
    configs = [c for c in _settings.rule_engines if c.enabled]
    if configs or not _settings.grit_dir or not os.path.isdir(str(_settings.grit_dir)):
        return configs
    from types import SimpleNamespace
    return [SimpleNamespace(
        id='grit', kind='grit', enabled=True,
        grit_dir=_settings.grit_dir, bundle_root=None,
        language_ids=('java',),
    )]


def _rule_engine_items(check_tool, check_module) -> list[dict]:
    """One row per configured rule engine (with legacy-fallback awareness),
    plus the toolchain each engine actually depends on. Returns an empty-state
    row when no engines are configured.
    """
    configs = _effective_rule_engine_configs()
    if not configs:
        return [{'id': 'engines_none', 'label': 'no rule engines configured', 'present': True}]

    builders = {
        'grit': lambda c: _grit_engine_items(c, check_tool),
        'radon': lambda c: _radon_engine_items(c, check_module),
        'bundle': lambda c: _bundle_engine_items(c),
    }
    items: list[dict] = []
    for cfg in configs:
        items.extend(builders.get(cfg.kind, _unknown_engine_items)(cfg))
    return items


def _agent_bridge_items(check_tool) -> list[dict]:
    """Doctor rows mirroring the /live composer's render conditions: the
    feature flag, the launch-shell opt-in env var, tmux on PATH, and at
    least one registered reachable pane. Every row is optional — the
    bridge ships disabled (see docs/setup.md, *Agent bridge*).
    """
    if not _settings.agent_bridge.enabled:
        return [{'id': 'bridge_enabled', 'label': 'bridge enabled',
                 'present': False, 'optional': True,
                 'install_hint': 'set {"agent_bridge": {"enabled": true}} in '
                                 'config/settings.local.json — see docs/setup.md'}]
    env_on = os.environ.get('REGIN_BRIDGE', '').strip().lower() in ('1', 'true', 'yes', 'on')
    items = [
        {'id': 'bridge_enabled', 'label': 'bridge enabled', 'present': True},
        {'id': 'bridge_env', 'label': 'REGIN_BRIDGE in this shell',
         'present': env_on, 'optional': True,
         'install_hint': 'export REGIN_BRIDGE=1 in the shell that launches '
                         'claude (fish: set -Ux REGIN_BRIDGE 1)'},
        {'id': 'bridge_tmux', 'label': 'tmux',
         **check_tool('tmux', ['tmux', '-V']),
         'optional': True, 'install_hint': 'brew install tmux'},
    ]
    try:
        from lib.agent_bridge.store import list_reachable_sessions
        n = len(list_reachable_sessions())
        items.append({'id': 'bridge_panes', 'label': 'reachable panes',
                      'present': n > 0, 'version': f'{n} registered', 'optional': True,
                      'install_hint': 'launch claude inside tmux with REGIN_BRIDGE=1 set'})
    except Exception as exc:  # noqa: BLE001 — doctor must never crash a row
        items.append({'id': 'bridge_panes', 'label': 'reachable panes',
                      'present': False, 'optional': True,
                      'install_hint': f'registry query failed: {exc}'})
    return items


def _version(cmd: list[str]) -> str:
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
        first = (result.stdout or result.stderr or '').strip().splitlines()[0]
        return first.strip()
    except Exception:
        return ''


_TOPIC_SYNC_HINTS = {
    "no_snapshot": "run `regin topics import` (next read will auto-seed)",
    "disk_newer": "run `regin topics import` — or install hooks via `regin topics install-hook`",
    "disk_unreadable": "topic.json is corrupted; restore from git or re-bootstrap",
}


def _topic_sync_item(repo, result: dict) -> dict:
    """Build one doctor item from a `check_graph_sync` result for `repo`."""
    base = {"id": f"topic_sync_{repo.id}", "label": repo.name}
    state = result["state"]
    if state == "in_sync":
        return {**base, "present": True, "version": "in sync"}
    if state == "no_disk_file":
        return {**base, "present": True, "version": "no topic.json (skip)", "optional": True}
    hint = _TOPIC_SYNC_HINTS.get(state, f"unknown state: {state}")
    if state == "disk_unreadable" and result.get("error"):
        hint = f"{hint} (error: {result['error']})"
    return {**base, "present": False, "optional": True, "install_hint": hint}


def _topic_sync_items() -> list[dict]:
    """One item per registered repo summarising disk ↔ snapshot sync."""
    from sqlmodel import select

    from lib.orm import SessionLocal
    from lib.orm.models import Repo
    from lib.topics.graph_io import check_graph_sync

    with SessionLocal() as s:
        repos = list(s.exec(select(Repo).where(Repo.is_active == 1)))
    items: list[dict] = []
    for repo in repos:
        try:
            result = check_graph_sync(repo.path)
        except Exception as exc:  # noqa: BLE001 — doctor must never crash a row
            items.append({
                "id": f"topic_sync_{repo.id}",
                "label": repo.name,
                "present": False,
                "install_hint": f"sync check failed: {exc}",
            })
            continue
        if result["state"] == "unregistered":
            # Defensive — `repo.path` should always match a Repo row.
            continue
        items.append(_topic_sync_item(repo, result))
    return items


def run_checks() -> dict:
    """Return structured doctor check results."""
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    provider = get_active_provider()

    frontend = {
        'node': _check_tool('node', ['node', '--version']),
        'npm': _check_tool('npm', ['npm', '--version']),
        'npx': _check_tool('npx', ['npx', '--version']),
        'playwright': _check_playwright(),
    }
    utilities = {
        'jq': _check_tool('jq', ['jq', '--version']),
        'curl': _check_tool('curl', ['curl', '--version']),
    }

    hook_scripts = _scan_hook_scripts()
    experimental = bool(_settings.experimental_providers)

    project = {
        'venv': os.path.isdir(os.path.join(root, '.venv')),
        'node_modules': os.path.isdir(os.path.join(root, 'frontend', 'node_modules')),
        'settings_local': os.path.isfile(os.path.join(root, 'config', 'settings.local.json')),
        'web_ui': os.path.isdir(os.path.join(root, 'web', 'static', 'dist')),
        'sqlite_db': os.path.isfile(os.path.join(root, 'db', 'regin.db')),
        'mysql_configured': _mysql_ok(),
        'data_dir_writable': _is_writable_dir(_settings.data_dir),
        'log_dir_writable': _is_writable_dir(_settings.log_dir),
        'patterns_dir_writable': _is_writable_dir(_settings.patterns_dir),
    }

    groups = [
        {'name': 'Core tools', 'items': [
            {'id': 'git', 'label': 'git', **_check_tool('git', ['git', '--version'])},
        ]},
        {'name': f'Active provider ({provider.display_name})',
         'items': _active_provider_items(provider, _check_tool)},
        {'name': 'Configured rule engines',
         'items': _rule_engine_items(_check_tool, _check_module)},
        {'name': 'Frontend (Node.js)', 'items': [
            {'id': 'node', 'label': 'node', **frontend['node']},
            {'id': 'npm', 'label': 'npm', **frontend['npm']},
            {'id': 'npx', 'label': 'npx', **frontend['npx']},
            {'id': 'playwright', 'label': 'playwright', **frontend['playwright'], 'optional': True},
        ]},
        {'name': 'Utilities', 'items': [
            {'id': 'jq', 'label': 'jq', **utilities['jq'], 'optional': True},
            {'id': 'curl', 'label': 'curl', **utilities['curl'], 'optional': True},
        ]},
    ]
    if experimental:
        groups.append({'name': 'Pattern router (experimental)',
                       'items': _router_group_items()})

    groups.append({'name': 'Pattern search (SQLite)', 'items': [
        {'id': 'fts5', 'label': 'SQLite FTS5 (lexical leg)',
         **_check_fts5(),
         'install_hint': 'rebuild Python against a SQLite with FTS5 (default on macOS Homebrew Python)'},
    ]})
    topic_sync = _topic_sync_items()
    if topic_sync:
        groups.append({'name': 'Topic graph sync (per repo)', 'items': topic_sync})

    groups.append({'name': 'Agent bridge (web steering)',
                   'items': _agent_bridge_items(_check_tool)})

    groups.append({'name': f'{provider.display_name} hooks (script paths in {provider.hook_settings_path()})', 'items': [
        {
            'id': f"hook_{i}",
            'label': f"{h['event']}: {os.path.basename(h['script'])}",
            'present': h['present'],
            'path': h['script'],
        }
        for i, h in enumerate(hook_scripts)
    ] or [{'id': 'hook_none', 'label': 'no direct-script hooks', 'present': True}]})

    return {
        'provider': {
            'id': provider.provider_id,
            'name': provider.display_name,
            'capabilities': provider_capability_rows(include_experimental=experimental),
        },
        'groups': groups,
        'project': {
            'name': 'Project health',
            'items': [
                {'id': 'venv', 'label': '.venv', 'present': project['venv']},
                {'id': 'node_modules', 'label': 'node_modules', 'present': project['node_modules']},
                {'id': 'settings_local', 'label': 'settings.local.json', 'present': project['settings_local']},
                {'id': 'web_ui', 'label': 'web UI built', 'present': project['web_ui']},
                {'id': 'sqlite_db', 'label': 'SQLite DB', 'present': project['sqlite_db']},
                {'id': 'mysql_configured', 'label': 'MySQL configured', 'present': project['mysql_configured'], 'optional': True},
                {'id': 'data_dir_writable', 'label': f'data_dir writable ({_settings.data_dir})', 'present': project['data_dir_writable']},
                {'id': 'log_dir_writable', 'label': f'log_dir writable ({_settings.log_dir})', 'present': project['log_dir_writable']},
                {'id': 'patterns_dir_writable', 'label': f'patterns_dir writable ({_settings.patterns_dir})', 'present': project['patterns_dir_writable']},
            ],
        },
    }
