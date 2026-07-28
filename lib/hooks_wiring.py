"""Construction, detection, and repair of regin's hook wiring.

Two surfaces need the same fact: is the hook command on disk the one install
would write today? CAI-15 shipped a stale command (a debug hook missing
`--silent`, printing into Kimi's UI on every prompt) that no surface could see
or repair. So command construction, ownership detection, the on-disk readers,
and the install/uninstall writers all live here; the web blueprint, `regin
hooks`, and `regin doctor` are thin callers over one implementation.

Every entry point takes the provider's hook settings path explicitly rather
than deriving it, because the web layer overrides that path in tests and for
non-active providers.
"""

from __future__ import annotations

import json
import os
import re
import shlex

from lib.providers import kimi_hooks
from lib.settings import settings


DEBUG_EVENTS = ('UserPromptSubmit', 'PostToolUse', 'PreToolUse')
HOOK_MANAGER_TIMEOUT = 60
DEBUG_TIMEOUT = 10
# Delimited-block label for the debug hook in a TOML config (Kimi), kept
# separate from the hook_manager block so the two never clobber each other.
DEBUG_LABEL = 'debug'

_HOOK_MANAGER_CMD_RE = re.compile(r'(^|\s)-m\s+hook_manager(?:\s|$)')
_DEBUG_SCRIPT_TOKEN = 'hook_payload_debug'

# Matches a stale `env KEY=VAL …` prefix from an earlier fix iteration. Kept
# only for detection so uninstall/reinstall still sees those entries as ours
# and replaces them; new installs no longer emit any env prefix.
_LEADING_ENV_RE = re.compile(r'^(?:/usr/bin/)?env(?:\s+[A-Za-z_][A-Za-z0-9_]*=\S*)+\s+')

# The checkout a foreign command runs out of, read back from its interpreter.
_VENV_PYTHON_RE = re.compile(r'(?P<root>\S+?)/\.venv/bin/python(?:\s|$)')


# ── Command construction ──────────────────────────────────────

def _venv_python() -> str:
    return os.path.join(str(settings.project_root), '.venv/bin/python')


def interpreter_prefix() -> str:
    """The exact interpreter token that install bakes into commands.

    Used to scope detection/removal to *this* regin checkout — two regin
    instances on one machine share a Claude settings.json, and matching by
    a bare `-m hook_manager` substring would let one instance's uninstall
    clobber another's entries. Trailing space anchors to a token boundary
    so `/foo/regin` doesn't match `/foo/regin-fork`.
    """
    return _venv_python() + ' '


def script_command(script_name: str) -> str:
    return f"{_venv_python()} {os.path.join(str(settings.project_root), 'scripts', script_name)}"


def debug_base_command() -> str:
    return script_command('hook_payload_debug.py')


def debug_hook_command(provider) -> str:
    """Per-provider debug-hook command.

    Claude keeps the bare command (logs to ``~/.claude`` and emits Claude-style
    stdout). Other agents get their own log path appended — Kimi logs to
    ``~/.kimi-code`` not ``~/.claude``, so without this its debug payloads never
    reach the viewer — plus ``--silent`` when the agent renders raw hook stdout
    (Kimi), so we never print a Claude-only response into its UI.
    """
    if provider.provider_id == 'claude':
        return debug_base_command()
    parts = [debug_base_command(), shlex.quote(str(provider.hook_payload_log_path()))]
    if getattr(provider, 'hook_output_format', 'claude') != 'claude':
        parts.append('--silent')
    return ' '.join(parts)


def hook_manager_command(event_name: str, provider) -> str:
    # `-P` is the CLI form of PYTHONSAFEPATH=1: it stops `python -m` from
    # injecting `sys.path[0] = cwd`. Without it, a hook installed from one
    # regin checkout would silently import another checkout's `hook_manager`
    # when Claude ran from there, sending spans to the wrong DB.
    agent_type = getattr(provider, 'provider_id', None) or 'generic'
    return (
        f"{_venv_python()} -P "
        f"-m hook_manager {shlex.quote(event_name)} "
        f"--agent-type {shlex.quote(agent_type)}"
    )


def hook_manager_events(provider) -> tuple[str, ...]:
    """Events install routes to hook_manager for this provider."""
    from hook_manager.core import SPEC_EVENTS
    return tuple(provider.hook_events() or tuple(sorted(SPEC_EVENTS)))


def expected_hook_manager_commands(provider) -> dict[str, str]:
    return {e: hook_manager_command(e, provider) for e in hook_manager_events(provider)}


def expected_debug_commands(provider) -> dict[str, str]:
    command = debug_hook_command(provider)
    return {e: command for e in DEBUG_EVENTS}


# ── Ownership predicates ──────────────────────────────────────

def _ours_prefix(command: str) -> bool:
    idx = command.find(interpreter_prefix())
    if idx < 0:
        return False
    prefix = command[:idx]
    return prefix == '' or _LEADING_ENV_RE.match(prefix) is not None


def _runs_hook_manager(command) -> bool:
    return isinstance(command, str) and bool(_HOOK_MANAGER_CMD_RE.search(command))


def _runs_debug_hook(command) -> bool:
    return isinstance(command, str) and _DEBUG_SCRIPT_TOKEN in command


def is_hook_manager_command(command: str) -> bool:
    return _runs_hook_manager(command) and _ours_prefix(command)


def is_debug_hook_command(command: str) -> bool:
    """Ours, scoped to this checkout — same rule as `is_hook_manager_command`.

    Two regin checkouts share one config file and one script *name*, and this
    predicate drives rewriting and deletion, so an unscoped substring match
    would let either instance clobber the other's entries.
    """
    return _runs_debug_hook(command) and _ours_prefix(command)


def is_foreign_hook_manager_command(command: str) -> bool:
    """Runs regin's router, but out of a different checkout than this one.

    A *moved* checkout is indistinguishable from a second one, so this state
    cannot be auto-repaired: install would add a second entry beside the old
    one and both would fire. Surfacing it as its own state is what lets a
    caller offer `adopt` instead of a blind reinstall.
    """
    return _runs_hook_manager(command) and not _ours_prefix(command)


def is_foreign_debug_hook_command(command: str) -> bool:
    return _runs_debug_hook(command) and not _ours_prefix(command)


def checkout_root(command: str) -> str | None:
    """The regin checkout a command's interpreter lives in, when readable."""
    match = _VENV_PYTHON_RE.search(command or '')
    return match.group('root') if match else None


# ── On-disk readers ───────────────────────────────────────────

def is_toml_provider(provider) -> bool:
    return getattr(provider, 'hook_config_format', 'json') == 'toml'


def read_json_settings(path: str) -> dict:
    try:
        with open(path, 'r') as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def json_settings_unreadable(path: str) -> bool:
    """A settings.json that has content but will not parse.

    `read_json_settings` flattens that to `{}`, which every writer below would
    read as "no hooks yet" and then overwrite, dropping whatever else the file
    held. A stray trailing comma is enough to reach it, and doctor now names
    the install command out loud, so the writers have to refuse.
    """
    try:
        with open(path, 'r') as f:
            text = f.read()
    except FileNotFoundError:
        return False
    if not text.strip():
        return False
    try:
        json.loads(text)
    except json.JSONDecodeError:
        return True
    return False


def _unreadable_error(settings_path: str) -> dict | None:
    if not json_settings_unreadable(settings_path):
        return None
    return {'ok': False,
            'msg': f'{settings_path} is not valid JSON — repair it by hand first; '
                   'regin will not overwrite a file it cannot read'}


def write_json_settings(data: dict, path: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w') as f:
        json.dump(data, f, indent=2)


def read_text(path: str) -> str:
    try:
        with open(path, 'r') as f:
            return f.read()
    except FileNotFoundError:
        return ''


def hook_entries(event_hooks: list):
    """Every hook dict under one event, tolerating hand-mangled settings."""
    for entry in event_hooks:
        if not isinstance(entry, dict):
            continue
        inner = entry.get('hooks')
        if not isinstance(inner, list):
            continue
        yield from (h for h in inner if isinstance(h, dict))


def _matcher_key(entry: dict):
    """What Claude Code scopes an entry to; a hand-edited unhashable value
    falls back to its repr so grouping never raises on a mangled file."""
    matcher = entry.get('matcher')
    try:
        hash(matcher)
    except TypeError:
        return repr(matcher)
    return matcher


def _entry_hooks(entry, is_ours) -> list[dict]:
    """Our hook dicts inside one matcher entry, tolerating a mangled shape."""
    if not isinstance(entry, dict) or not isinstance(entry.get('hooks'), list):
        return []
    return [h for h in entry['hooks']
            if isinstance(h, dict) and is_ours(h.get('command', ''))]


def _entry_commands(entry, is_ours) -> list[str]:
    return [h.get('command', '') for h in _entry_hooks(entry, is_ours)]


def _matcher_groups(entries: list, is_ours) -> dict:
    groups: dict = {}
    for entry in entries:
        commands = _entry_commands(entry, is_ours)
        if commands:
            groups.setdefault(_matcher_key(entry), []).extend(commands)
    return groups


def _json_command_groups(path: str, is_ours) -> dict[str, dict]:
    """event → matcher → the commands of ours written under that matcher.

    The matcher has to survive into every duplicate decision: two of our hooks
    under one matcher fire twice for a single tool call, but the same command
    under two matchers is a hand-written scoping, and collapsing the two cases
    together makes refresh delete config the user wrote.
    """
    hooks = read_json_settings(path).get('hooks')
    if not isinstance(hooks, dict):
        return {}
    groups_by_event = ((event, _matcher_groups(entries, is_ours))
                       for event, entries in hooks.items() if isinstance(entries, list))
    return {event: groups for event, groups in groups_by_event if groups}


def _json_command_map(path: str, is_ours) -> dict[str, list[str]]:
    return {event: [c for commands in groups.values() for c in commands]
            for event, groups in _json_command_groups(path, is_ours).items()}


def _json_timeout_map(path: str, is_ours) -> dict[str, list]:
    """event → the `timeout` written beside each of our commands.

    Read separately from the commands because a drifted timeout is real drift
    that a command-only comparison calls healthy — an entry left at an older
    `HOOK_MANAGER_TIMEOUT` gets killed mid-handler on a slow event.
    """
    hooks = read_json_settings(path).get('hooks')
    if not isinstance(hooks, dict):
        return {}
    out: dict[str, list] = {}
    for event, entries in hooks.items():
        if not isinstance(entries, list):
            continue
        timeouts = [h.get('timeout') for entry in entries for h in _entry_hooks(entry, is_ours)]
        if timeouts:
            out[event] = timeouts
    return out


def installed_command_map(provider, settings_path: str, is_ours) -> dict[str, list[str]]:
    """Event → the commands of ours currently written for it, format-agnostic."""
    if is_toml_provider(provider):
        return kimi_hooks.installed_command_map(settings_path, is_ours)
    return _json_command_map(settings_path, is_ours)


def installed_timeout_map(provider, settings_path: str, is_ours) -> dict[str, list]:
    """Event → the timeouts written beside our commands, format-agnostic."""
    if is_toml_provider(provider):
        return kimi_hooks.installed_timeout_map(settings_path, is_ours)
    return _json_timeout_map(settings_path, is_ours)


def routed_events(provider, settings_path: str) -> set[str]:
    """Events routed to this regin checkout's hook_manager, format-agnostic."""
    if is_toml_provider(provider):
        return kimi_hooks.routed_events(settings_path, is_hook_manager_command)
    return set(_json_command_map(settings_path, is_hook_manager_command))


def debug_routed_events(provider, settings_path: str) -> set[str]:
    """Events the debug hook is installed for, format-agnostic."""
    if is_toml_provider(provider):
        return kimi_hooks.routed_events(settings_path, is_debug_hook_command)
    return set(_json_command_map(settings_path, is_debug_hook_command))


# ── Staleness ─────────────────────────────────────────────────

def duplicate_events(provider, settings_path: str, is_ours) -> list[str]:
    """Events carrying a redundant copy of our hook that install would drop.

    Redundancy is per matcher on JSON providers: the same command under two
    matchers fires for two different tool sets, not twice for one call. TOML
    providers have no matcher, so any second copy there is redundant.
    """
    if is_toml_provider(provider):
        installed = kimi_hooks.installed_command_map(settings_path, is_ours)
        return sorted(e for e, commands in installed.items() if len(commands) > 1)
    return sorted(e for e, groups in _json_command_groups(settings_path, is_ours).items()
                  if any(len(commands) > 1 for commands in groups.values()))


def timeout_drift_events(expected: dict[str, str], expected_timeout: int,
                         timeouts: dict[str, list]) -> list[str]:
    """Routed events whose `timeout` is not the one install writes today.

    Scoped to events install would write, for the same reason `stale` excludes
    `unexpected_events`: nothing rewrites a route install no longer emits, so
    reporting drift there would leave doctor red pointing at a no-op repair.

    A *missing* timeout is not drift. The agent applies its own default there,
    so an entry predating the timeout key behaves correctly, and calling every
    one of those stale would turn the first upgrade into a wall of red.
    """
    def drifted(values) -> bool:
        return any(t is not None and t != expected_timeout for t in values)
    return sorted(e for e, values in timeouts.items() if e in expected and drifted(values))


def _stale_events(expected: dict[str, str], current: dict[str, list[str]],
                  duplicates: list[str], timeout_drift: list[str]) -> list[str]:
    """Routed events install would rewrite: drifted command or timeout, or a duplicate.

    Duplicates count even when every copy is correct — two entries under one
    matcher fire the hook twice per event, and comparing command *sets* (the
    obvious implementation) reports that as healthy.
    """
    redundant = set(duplicates) | set(timeout_drift)

    def differs(event: str) -> bool:
        if event in redundant:
            return True
        return event in expected and any(c != expected[event] for c in current[event])
    return sorted(e for e in current if differs(e))


def _foreign_roots(foreign: dict[str, list[str]]) -> list[str]:
    roots = {checkout_root(c) for commands in foreign.values() for c in commands}
    return sorted(r for r in roots if r)


def _kind_status(provider, settings_path: str, spec: dict, malformed: list[str]) -> dict:
    """Compare what install would write against what is on disk.

    Three states are reported but deliberately excluded from `stale`, because
    install cannot clear any of them and gating on them would leave doctor
    permanently red pointing at a repair that answers "already installed":
    `unexpected_events` (routes install would no longer write — the JSON
    installer only adds and refreshes), `malformed_events` (an event key
    whose value is not a list, which only a human edit can have produced and
    only a human edit should undo), and `foreign_events` (another checkout's
    entries, which only `adopt` may touch).
    """
    expected, is_ours = spec['expected'], spec['is_ours']
    current = installed_command_map(provider, settings_path, is_ours)
    timeouts = installed_timeout_map(provider, settings_path, is_ours)
    foreign = installed_command_map(provider, settings_path, spec['is_foreign'])
    drift = timeout_drift_events(expected, spec['timeout'], timeouts)
    stale = _stale_events(expected, current,
                          duplicate_events(provider, settings_path, is_ours), drift)
    missing = sorted(set(expected) - set(current) - set(malformed)) if current else []
    return {
        'installed': bool(current),
        'routed_events': sorted(current),
        # Not de-duplicated: collapsing to a set is what hides a
        # duplicated-but-correct entry from every surface downstream.
        'commands': {e: sorted(c) for e, c in sorted(current.items())},
        'expected_commands': dict(sorted(expected.items())),
        'timeouts': {e: list(t) for e, t in sorted(timeouts.items())},
        'expected_timeout': spec['timeout'],
        'timeout_drift_events': drift,
        'stale_events': stale,
        'missing_events': missing,
        'unexpected_events': sorted(set(current) - set(expected)),
        'malformed_events': sorted(malformed),
        'foreign_events': sorted(foreign),
        'foreign_commands': {e: sorted(c) for e, c in sorted(foreign.items())},
        'foreign_roots': _foreign_roots(foreign),
        'stale': bool(current) and bool(stale or missing),
    }


def malformed_events(provider, settings_path: str) -> list[str]:
    """Event keys whose value is not a list of hook entries.

    Hand-editing is the only way to reach this shape, and neither installer
    can write into it, so it is surfaced separately from repairable drift.
    """
    if is_toml_provider(provider):
        return []
    hooks = read_json_settings(settings_path).get('hooks')
    if not isinstance(hooks, dict):
        return []
    return [e for e, entries in hooks.items() if not isinstance(entries, list)]


def _kind_specs(provider) -> dict[str, dict]:
    """Everything that differs between the two hooks, in one place, so every
    comparison and writer below is the same code with a different spec."""
    return {
        'hook_manager': {
            'is_ours': is_hook_manager_command,
            'is_foreign': is_foreign_hook_manager_command,
            'expected': expected_hook_manager_commands(provider),
            'timeout': HOOK_MANAGER_TIMEOUT,
        },
        'debug': {
            'is_ours': is_debug_hook_command,
            'is_foreign': is_foreign_debug_hook_command,
            'expected': expected_debug_commands(provider),
            'timeout': DEBUG_TIMEOUT,
        },
    }


def wiring_status(provider, settings_path: str) -> dict:
    """Full install-vs-disk report for one provider's hook wiring."""
    malformed = malformed_events(provider, settings_path)
    specs = _kind_specs(provider)
    return {
        'provider': provider.provider_id,
        'settings_path': settings_path,
        'hook_manager': _kind_status(provider, settings_path, specs['hook_manager'], malformed),
        'debug': _kind_status(provider, settings_path, specs['debug'],
                              [e for e in malformed if e in DEBUG_EVENTS]),
    }


# ── hook_manager install / uninstall ──────────────────────────

def _drop_duplicate_hooks(event_hooks: list, is_ours) -> int:
    """Keep only the first hook of ours per matcher; return how many went.

    Two of ours under one matcher fire twice for a single tool call and nothing
    else in the install path removes the extra, so refresh has to or repair
    never converges. Scoping that to the matcher is what keeps it from eating a
    hand-written `matcher: "Edit"` copy, which is deliberate config and not a
    duplicate at all.
    """
    seen = set()
    removed = 0
    kept_entries = []
    for entry in event_hooks:
        if not isinstance(entry, dict) or not isinstance(entry.get('hooks'), list):
            kept_entries.append(entry)
            continue
        matcher = _matcher_key(entry)
        kept = []
        for h in entry['hooks']:
            if isinstance(h, dict) and is_ours(h.get('command', '')):
                if matcher in seen:
                    removed += 1
                    continue
                seen.add(matcher)
            kept.append(h)
        if kept or not entry['hooks']:
            entry['hooks'] = kept
            kept_entries.append(entry)
    event_hooks[:] = kept_entries
    return removed


def _refresh_hook(hook: dict, command: str, timeout: int) -> bool:
    """Bring one of our hook dicts up to what install writes; True if it moved.

    The timeout matters as much as the command: an entry left at an older
    value is a hook that gets killed mid-handler, and nothing about the
    command string shows it.
    """
    changed = False
    if hook.get('command') != command:
        hook['command'] = command
        changed = True
    if hook.get('timeout') != timeout:
        hook['timeout'] = timeout
        changed = True
    return changed


def _merge_event_command(hooks: dict, event_name: str, command: str,
                         is_ours, timeout: int) -> str:
    """Wire exactly one of our hooks, carrying `command`, to `event_name`.

    Returns 'added', 'updated', 'ok', or 'malformed' — the last when the
    event's value is not a list, a shape only a hand edit produces and which
    install must leave alone rather than overwrite.
    """
    entries = hooks.setdefault(event_name, [])
    if not isinstance(entries, list):
        return 'malformed'
    if not any(is_ours(h.get('command', '')) for h in hook_entries(entries)):
        entries.append({'hooks': [
            {'type': 'command', 'command': command, 'timeout': timeout}]})
        return 'added'
    changed = _drop_duplicate_hooks(entries, is_ours) > 0
    # Every surviving copy, not just the first: the dedup above deliberately
    # keeps one per matcher, and leaving the others on a drifted command would
    # report the event stale forever.
    rewritten = [_refresh_hook(h, command, timeout) for h in hook_entries(entries)
                 if is_ours(h.get('command', ''))]
    return 'updated' if changed or any(rewritten) else 'ok'


def _merge_hook_manager_blocks(hooks: dict, events, provider) -> tuple[int, int]:
    """Add/refresh hook_manager command blocks in a settings.json `hooks` map."""
    outcomes = [
        _merge_event_command(hooks, event_name, hook_manager_command(event_name, provider),
                             is_hook_manager_command, HOOK_MANAGER_TIMEOUT)
        for event_name in sorted(events)
    ]
    return outcomes.count('added'), outcomes.count('updated')


def _json_install_hook_manager(provider, settings_path: str) -> dict:
    data = read_json_settings(settings_path)
    hooks = data.setdefault('hooks', {})
    added, updated = _merge_hook_manager_blocks(hooks, hook_manager_events(provider), provider)
    if added == 0 and updated == 0:
        return {'ok': True, 'msg': 'Hook manager already installed'}
    write_json_settings(data, settings_path)
    parts = []
    if added:
        parts.append(f'{added} added')
    if updated:
        parts.append(f'{updated} updated')
    return {'ok': True,
            'msg': f"Hook manager installed for {provider.display_name} events ({', '.join(parts)})"}


def _toml_install_hook_manager(provider, settings_path: str) -> dict:
    before = read_text(settings_path)
    kimi_hooks.install(
        settings_path,
        list(hook_manager_events(provider)),
        lambda event_name: hook_manager_command(event_name, provider),
        timeout=HOOK_MANAGER_TIMEOUT,
        is_ours=is_hook_manager_command,
    )
    if read_text(settings_path) == before:
        return {'ok': True, 'msg': 'Hook manager already installed'}
    after = kimi_hooks.routed_events(settings_path, is_hook_manager_command)
    return {'ok': True,
            'msg': f"Hook manager installed for {provider.display_name} events ({len(after)} routed)"}


def install_hook_manager(provider, settings_path: str) -> dict:
    if is_toml_provider(provider):
        return _toml_install_hook_manager(provider, settings_path)
    return _unreadable_error(settings_path) or _json_install_hook_manager(provider, settings_path)


def _strip_matching_blocks(hooks: dict, is_match) -> int:
    """Remove command blocks satisfying `is_match` from a settings.json `hooks` map."""
    removed = 0
    for event_name in list(hooks.keys()):
        entries = hooks[event_name]
        if not isinstance(entries, list):
            continue
        filtered = []
        for entry in entries:
            entry_hooks = [h for h in entry.get('hooks', [])
                           if not is_match(h.get('command', ''))]
            removed += len(entry.get('hooks', [])) - len(entry_hooks)
            if entry_hooks:
                next_entry = dict(entry)
                next_entry['hooks'] = entry_hooks
                filtered.append(next_entry)
        if filtered:
            hooks[event_name] = filtered
        else:
            del hooks[event_name]
    return removed


def uninstall_hook_manager(provider, settings_path: str) -> dict:
    if is_toml_provider(provider):
        removed = kimi_hooks.uninstall(settings_path, is_ours=is_hook_manager_command)
        return {'ok': True,
                'msg': 'Hook manager removed' if removed else 'Hook manager was not installed'}
    if refusal := _unreadable_error(settings_path):
        return refusal
    data = read_json_settings(settings_path)
    hooks = data.get('hooks', {})
    removed = _strip_matching_blocks(hooks, is_hook_manager_command) if isinstance(hooks, dict) else 0
    if not hooks:
        data.pop('hooks', None)
    write_json_settings(data, settings_path)
    return {'ok': True,
            'msg': 'Hook manager removed' if removed else 'Hook manager was not installed'}


# ── debug hook install / uninstall ────────────────────────────

def _merge_debug_blocks(hooks: dict, command: str) -> tuple[int, int]:
    """Add/refresh debug-hook command blocks in a settings.json `hooks` map.

    Mirrors `_merge_hook_manager_blocks`: an entry installed before the
    per-provider command existed carries a stale command, and rewriting it is
    the only way a reinstall can fix it.
    """
    outcomes = [
        _merge_event_command(hooks, event_name, command,
                             is_debug_hook_command, DEBUG_TIMEOUT)
        for event_name in DEBUG_EVENTS
    ]
    return outcomes.count('added'), outcomes.count('updated')


def _toml_install_debug(provider, settings_path: str) -> dict:
    command = debug_hook_command(provider)
    installed = kimi_hooks.installed_commands(settings_path, is_debug_hook_command)
    before = read_text(settings_path)
    # Owns its own `debug`-labelled block; the hook_manager block (if any)
    # is left untouched. Rewriting unconditionally and diffing the file is what
    # catches a duplicated-but-correct entry, which a command-set comparison
    # reports as already installed.
    kimi_hooks.install(
        settings_path, list(DEBUG_EVENTS), lambda _event: command,
        timeout=DEBUG_TIMEOUT, label=DEBUG_LABEL, is_ours=is_debug_hook_command,
    )
    if read_text(settings_path) == before:
        return {'ok': True, 'msg': 'Already installed'}
    msg = 'Debug hook refreshed for all events' if installed else 'Debug hook installed for all events'
    return {'ok': True, 'msg': msg}


def install_debug_hook(provider, settings_path: str) -> dict:
    if is_toml_provider(provider):
        return _toml_install_debug(provider, settings_path)
    if refusal := _unreadable_error(settings_path):
        return refusal
    data = read_json_settings(settings_path)
    hooks = data.get('hooks')
    if not isinstance(hooks, dict):
        hooks = data['hooks'] = {}
    added, refreshed = _merge_debug_blocks(hooks, debug_hook_command(provider))
    if not added and not refreshed:
        return {'ok': True, 'msg': 'Already installed'}
    write_json_settings(data, settings_path)
    msg = 'Debug hook refreshed for all events' if refreshed else 'Debug hook installed for all events'
    return {'ok': True, 'msg': msg}


def uninstall_debug_hook(provider, settings_path: str) -> dict:
    if is_toml_provider(provider):
        kimi_hooks.uninstall(settings_path, label=DEBUG_LABEL, is_ours=is_debug_hook_command)
        return {'ok': True, 'msg': 'Debug hook removed'}
    if refusal := _unreadable_error(settings_path):
        return refusal
    data = read_json_settings(settings_path)
    for event_name in list(data.get('hooks', {}).keys()):
        event_hooks = data['hooks'][event_name]
        if not isinstance(event_hooks, list):
            continue
        filtered = []
        for entry in event_hooks:
            # Scoped to this checkout, like every other write here: two regin
            # instances share one settings.json, and `regin hooks remove` now
            # reaches this path directly.
            entry_hooks = [h for h in entry.get('hooks', [])
                           if not is_debug_hook_command(h.get('command', ''))]
            if entry_hooks:
                entry['hooks'] = entry_hooks
                filtered.append(entry)
        data['hooks'][event_name] = filtered
    write_json_settings(data, settings_path)
    return {'ok': True, 'msg': 'Debug hook removed'}


INSTALLERS = {'hook_manager': install_hook_manager, 'debug': install_debug_hook}
UNINSTALLERS = {'hook_manager': uninstall_hook_manager, 'debug': uninstall_debug_hook}


# ── adopting another checkout's entries ───────────────────────

def _strip_foreign(provider, settings_path: str, is_foreign) -> None:
    if is_toml_provider(provider):
        text = read_text(settings_path)
        cleaned = kimi_hooks.strip_entries(text, is_foreign)
        if cleaned != text:
            with open(settings_path, 'w') as f:
                f.write(cleaned.rstrip() + '\n')
        return
    data = read_json_settings(settings_path)
    hooks = data.get('hooks')
    if not isinstance(hooks, dict) or not _strip_matching_blocks(hooks, is_foreign):
        return
    if not hooks:
        data.pop('hooks', None)
    write_json_settings(data, settings_path)


def _adopt(provider, settings_path: str, kind: str) -> dict:
    """Replace another checkout's entries for `kind` with this checkout's.

    Never implicit: a *moved* checkout and a genuine second one look identical
    on disk, and silently rewriting the second case would break whichever
    regin the user did not run. The counterpart failure — install adding its
    own entry beside the old one, so both fire — is what this exists to avoid.
    """
    if not is_toml_provider(provider) and (refusal := _unreadable_error(settings_path)):
        return refusal
    spec = _kind_specs(provider)[kind]
    foreign = installed_command_map(provider, settings_path, spec['is_foreign'])
    adopted = sum(len(commands) for commands in foreign.values())
    if not adopted:
        return {'ok': True, 'msg': 'No entries from another checkout to adopt', 'adopted': 0}
    _strip_foreign(provider, settings_path, spec['is_foreign'])
    result = INSTALLERS[kind](provider, settings_path)
    where = ', '.join(_foreign_roots(foreign)) or 'another checkout'
    return {**result, 'adopted': adopted,
            'msg': f"Adopted {adopted} entr{'y' if adopted == 1 else 'ies'} "
                   f"from {where} — {result['msg']}"}


def adopt_hook_manager(provider, settings_path: str) -> dict:
    return _adopt(provider, settings_path, 'hook_manager')


def adopt_debug_hook(provider, settings_path: str) -> dict:
    return _adopt(provider, settings_path, 'debug')


ADOPTERS = {'hook_manager': adopt_hook_manager, 'debug': adopt_debug_hook}
