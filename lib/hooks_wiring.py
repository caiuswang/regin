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
from lib.venv_paths import WINDOWS, venv_python


DEBUG_EVENTS = ('UserPromptSubmit', 'PostToolUse', 'PreToolUse')
HOOK_MANAGER_TIMEOUT = 60
DEBUG_TIMEOUT = 10
# Delimited-block label for the debug hook in a TOML config (Kimi), kept
# separate from the hook_manager block so the two never clobber each other.
DEBUG_LABEL = 'debug'

_HOOK_MANAGER_CMD_RE = re.compile(r'(^|\s)-m\s+hook_manager(?:\s|$)')
_GUARD_ARG_RE = re.compile(r'(^|\s)--guard(?:\s|=)')
GUARD_ON_FAILURE = 'block'
# The script name, extension included: `adopt` deletes what this matches, so a
# user's own `hook_payload_debug_wrapper.sh` must not look like our hook.
_DEBUG_SCRIPT_TOKEN = 'hook_payload_debug.py'

# Matches a stale `env KEY=VAL …` prefix from an earlier fix iteration. Kept
# only for detection so uninstall/reinstall still sees those entries as ours
# and replaces them; new installs no longer emit any env prefix.
_LEADING_ENV_RE = re.compile(r'^(?:/usr/bin/)?env(?:\s+[A-Za-z_][A-Za-z0-9_]*=\S*)+\s+')

_VENV_PYTHON_SUFFIXES = ('/.venv/bin/python', '/.venv/Scripts/python.exe')


# ── Command construction ──────────────────────────────────────

def _venv_python() -> str:
    # Forward slashes on Windows: Claude Code runs hook commands through Git
    # Bash there, which would read `C:\Users\…` backslashes as escapes, while
    # cmd and PowerShell accept `C:/Users/…` as well.
    return venv_python(settings.project_root).replace(os.sep, '/')


def _interpreter() -> str:
    # UTF-8 mode on Windows: hook payloads are UTF-8, and without it stdin and
    # open() decode with the ANSI code page (cp936 on a Chinese install).
    return shlex.quote(_venv_python()) + (' -X utf8' if WINDOWS else '')


def interpreter_prefix() -> str:
    """The exact interpreter token that install bakes into commands.

    Used to scope detection/removal to *this* regin checkout — two regin
    instances on one machine share a Claude settings.json, and matching by
    a bare `-m hook_manager` substring would let one instance's uninstall
    clobber another's entries. Trailing space anchors to a token boundary
    so `/foo/regin` doesn't match `/foo/regin-fork`.
    """
    return shlex.quote(_venv_python()) + ' '


def _interpreter_prefixes() -> tuple[str, ...]:
    """The quoted prefix install writes, plus the bare form an older regin
    wrote — which on a path with spaces never ran, so it must still read as
    ours for install to rewrite it."""
    return (interpreter_prefix(), _venv_python() + ' ')


def script_command(script_name: str) -> str:
    script = os.path.join(str(settings.project_root), 'scripts', script_name)
    return f"{_interpreter()} {shlex.quote(script)}"


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
        f"{_interpreter()} -P "
        f"-m hook_manager {shlex.quote(event_name)} "
        f"--agent-type {shlex.quote(agent_type)}"
    )


def guard_hook_command(event_name: str, provider, guard_name: str) -> str:
    return f"{hook_manager_command(event_name, provider)} --guard {shlex.quote(guard_name)}"


def _guard_handlers() -> list:
    from hook_manager.registry import REGISTRY
    return [h for h in REGISTRY if getattr(h, 'fail_closed', False)]


def expected_guard_hooks(provider) -> dict[str, list[dict]]:
    """event → the guard hook dicts install writes, one entry each.

    Empty for a provider whose harness has no `onFailure`: there the guards
    stay inside the shared entry, and writing a second one would run them
    twice. A guard listing `*` is wired to every routed event.
    """
    if not getattr(provider, 'supports_guard_entries', False) or is_toml_provider(provider):
        return {}
    routed = hook_manager_events(provider)
    out: dict[str, list[dict]] = {}
    for h in _guard_handlers():
        events = routed if '*' in h.events else [e for e in h.events if e in routed]
        for event in events:
            hook = {'type': 'command', 'command': guard_hook_command(event, provider, h.name),
                    'timeout': HOOK_MANAGER_TIMEOUT, 'onFailure': GUARD_ON_FAILURE}
            if h.matcher:
                hook['if'] = h.matcher
            out.setdefault(event, []).append(hook)
    return out


def hook_manager_events(provider) -> tuple[str, ...]:
    """Events install routes to hook_manager for this provider.

    STDOUT_IS_DATA events are withheld even from a provider that names them:
    the harness reads such a hook's stdout as a value it must act on, and a
    hook wired there is one an older regin could only answer with JSON.
    """
    from hook_manager.core import SPEC_EVENTS, STDOUT_IS_DATA
    events = provider.hook_events() or tuple(sorted(SPEC_EVENTS))
    return tuple(e for e in events if e not in STDOUT_IS_DATA)


def expected_hook_manager_commands(provider) -> dict[str, str]:
    return {e: hook_manager_command(e, provider) for e in hook_manager_events(provider)}


def expected_debug_commands(provider) -> dict[str, str]:
    command = debug_hook_command(provider)
    return {e: command for e in DEBUG_EVENTS}


# ── Ownership predicates ──────────────────────────────────────

def _ours_prefix(command: str) -> bool:
    for token in _interpreter_prefixes():
        idx = command.find(token)
        if idx < 0:
            continue
        prefix = command[:idx]
        if prefix == '' or _LEADING_ENV_RE.match(prefix) is not None:
            return True
    return False


def _runs_hook_manager(command) -> bool:
    return isinstance(command, str) and bool(_HOOK_MANAGER_CMD_RE.search(command))


def _runs_guard(command) -> bool:
    return _runs_hook_manager(command) and bool(_GUARD_ARG_RE.search(command))


def _runs_debug_hook(command) -> bool:
    return isinstance(command, str) and _DEBUG_SCRIPT_TOKEN in command


def _other_checkout(command: str) -> bool:
    """Not ours, and not this checkout's interpreter behind a wrapper.

    `_ours_prefix` only accepts the interpreter at the head of the command, so
    a user's `cd /tmp && <our python> …` is "not ours" — but calling it
    another checkout would name *this* directory as the other one and offer to
    adopt the user's own wrapper. Drift we cannot name is better left unnamed.
    """
    return not _ours_prefix(command) and checkout_root(command) != str(settings.project_root)


def is_hook_manager_command(command: str) -> bool:
    """Ours, and the shared entry — a guard entry is a different hook whose
    `if`/`onFailure` a refresh to the shared command would silently strip."""
    return _runs_hook_manager(command) and not _runs_guard(command) and _ours_prefix(command)


def is_guard_hook_command(command: str) -> bool:
    return _runs_guard(command) and _ours_prefix(command)


def _is_any_hook_manager_command(command: str) -> bool:
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
    return _runs_hook_manager(command) and _other_checkout(command)


def is_foreign_debug_hook_command(command: str) -> bool:
    return _runs_debug_hook(command) and _other_checkout(command)


def checkout_root(command: str) -> str | None:
    """The regin checkout a command's interpreter lives in, when readable.

    Tokenised the way the shell will run it, so a path install quoted — a
    space, or an apostrophe that `shlex.quote` renders as `'"'"'` — reads
    back as the path it is, not as the fragment after the last quote.
    """
    try:
        tokens = shlex.split(command or '')
    except ValueError:
        tokens = (command or '').split()
    for token in tokens:
        for suffix in _VENV_PYTHON_SUFFIXES:
            if token.endswith(suffix) and len(token) > len(suffix):
                return token[:-len(suffix)]
    return None


# ── On-disk readers ───────────────────────────────────────────

def is_toml_provider(provider) -> bool:
    return getattr(provider, 'hook_config_format', 'json') == 'toml'


def read_json_settings(path: str) -> dict:
    try:
        with open(path, 'r', encoding='utf-8') as f:
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
        with open(path, 'r', encoding='utf-8') as f:
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
    """Atomic, like `hook_manager/config.py`: the agent reads this file on
    every hook event, and a torn write is an agent with no hooks at all.

    Resolved through symlinks first — `os.replace` on the link itself would
    swap a dotfiles-managed settings.json for a plain file — and the target's
    permission bits carried over to the replacement.

    Non-ASCII is written as-is: the user's own entries ride through this
    rewrite, and `\\uXXXX`-escaping a Chinese hook command or env value would
    change bytes that were never regin's to touch.
    """
    target = os.path.realpath(path)
    os.makedirs(os.path.dirname(target), exist_ok=True)
    tmp = target + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    if os.path.exists(target):
        os.chmod(tmp, os.stat(target).st_mode & 0o7777)
    os.replace(tmp, target)


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


def retractable_events(current) -> list[str]:
    """Routed events install actively un-wires, rather than merely stops writing.

    A route install no longer emits is otherwise left alone, but one on a
    STDOUT_IS_DATA event is a live defect — an older regin wired
    `WorktreeCreate`, whose stdout the harness reads as the path of the
    worktree the hook created — so install retracts these and status must
    call them stale for repair to reach them.
    """
    from hook_manager.core import STDOUT_IS_DATA
    return sorted(set(current) & STDOUT_IS_DATA)


def _stale_events(expected: dict[str, str], current: dict[str, list[str]],
                  duplicates: list[str], timeout_drift: list[str]) -> list[str]:
    """Routed events install would rewrite or retract: drifted command or
    timeout, a duplicate, or a route install now un-wires.

    Duplicates count even when every copy is correct — two entries under one
    matcher fire the hook twice per event, and comparing command *sets* (the
    obvious implementation) reports that as healthy.
    """
    redundant = set(duplicates) | set(timeout_drift) | set(retractable_events(current))

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
    installer only adds and refreshes, except for the `retractable_events`
    subset it un-wires, which `stale` does carry), `malformed_events` (an event key
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


def _installed_guard_hooks(provider, settings_path: str) -> dict[str, list[dict]]:
    if is_toml_provider(provider):
        return {}
    hooks = read_json_settings(settings_path).get('hooks')
    if not isinstance(hooks, dict):
        return {}
    out: dict[str, list[dict]] = {}
    for event, entries in hooks.items():
        if isinstance(entries, list):
            found = [h for h in hook_entries(entries) if is_guard_hook_command(h.get('command', ''))]
            if found:
                out[event] = found
    return out


_GUARD_FIELDS = ('timeout', 'onFailure', 'if')


def _guard_event_drift(want: list[dict], have: list[dict]) -> tuple[bool, bool]:
    """(stale, missing) for one event's guard hooks."""
    expected = {h['command']: h for h in want}
    commands = [h.get('command') for h in have]
    missing = any(c not in commands for c in expected)
    stale = (len(set(commands)) != len(commands)
             or any(c not in expected for c in commands)
             or any(h.get(f) != expected[h['command']].get(f)
                    for h in have if h.get('command') in expected for f in _GUARD_FIELDS))
    return stale, missing


def guard_drift(provider, settings_path: str) -> dict[str, list[str]]:
    """Guard entries install would add, rewrite, or retract, by event."""
    expected = expected_guard_hooks(provider)
    installed = _installed_guard_hooks(provider, settings_path)
    stale, missing = [], []
    for event in sorted(set(expected) | set(installed)):
        is_stale, is_missing = _guard_event_drift(expected.get(event, []), installed.get(event, []))
        if is_stale:
            stale.append(event)
        if is_missing:
            missing.append(event)
    return {'stale': stale, 'missing': missing}


def _with_guards(status: dict, provider, settings_path: str) -> dict:
    """Fold guard drift into the hook_manager row: install writes both, so
    repair (which keys off this row) must see a missing guard as stale."""
    drift = guard_drift(provider, settings_path)
    stale = sorted(set(status['stale_events']) | set(drift['stale']))
    missing = sorted(set(status['missing_events']) | set(drift['missing']))
    return {**status,
            'expected_guards': expected_guard_hooks(provider),
            'guard_stale_events': drift['stale'],
            'guard_missing_events': drift['missing'],
            'stale_events': stale,
            'missing_events': missing,
            'stale': status['installed'] and bool(stale or missing)}


def wiring_status(provider, settings_path: str) -> dict:
    """Full install-vs-disk report for one provider's hook wiring."""
    malformed = malformed_events(provider, settings_path)
    specs = _kind_specs(provider)
    hook_manager = _kind_status(provider, settings_path, specs['hook_manager'], malformed)
    return {
        'provider': provider.provider_id,
        'settings_path': settings_path,
        'hook_manager': _with_guards(hook_manager, provider, settings_path),
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


def _retract_unwired_routes(hooks: dict) -> int:
    """Drop our command from the events install now un-wires; count removed.

    Scoped to those event keys so the generic stripper cannot reach a route
    that is merely unexpected — another checkout's, or a hand-written one.
    """
    present = retractable_events(hooks)
    scoped = {e: hooks[e] for e in present}
    removed = _strip_matching_blocks(scoped, is_hook_manager_command)
    for event_name in present:
        if event_name in scoped:
            hooks[event_name] = scoped[event_name]
        else:
            del hooks[event_name]
    return removed


def _strip_guards(hooks: dict, expected: dict[str, list[dict]]) -> int:
    """Drop guard hooks install no longer writes, and repeat copies."""
    removed = 0
    for event in list(hooks):
        entries = hooks[event]
        if not isinstance(entries, list):
            continue
        wanted = {h['command'] for h in expected.get(event, [])}
        seen: set[str] = set()

        def unwanted(command, wanted=wanted, seen=seen) -> bool:
            if not is_guard_hook_command(command):
                return False
            if command not in wanted or command in seen:
                return True
            seen.add(command)
            return False
        filtered, dropped = _strip_event(entries, unwanted)
        removed += dropped
        if filtered or not dropped:
            hooks[event] = filtered
        else:
            del hooks[event]
    return removed


def _refresh_guard(hook: dict, want: dict) -> bool:
    changed = False
    for field_name in _GUARD_FIELDS:
        if want.get(field_name) == hook.get(field_name):
            continue
        changed = True
        if field_name in want:
            hook[field_name] = want[field_name]
        else:
            hook.pop(field_name, None)
    return changed


def _sync_guard_entries(hooks: dict, provider) -> tuple[int, int, int]:
    """Give every guard its own fail-closed entry; (added, updated, retracted)."""
    expected = expected_guard_hooks(provider)
    retracted = _strip_guards(hooks, expected)
    added = updated = 0
    for event, wanted in expected.items():
        entries = hooks.setdefault(event, [])
        if not isinstance(entries, list):
            continue
        present = {h.get('command'): h for h in hook_entries(entries)
                   if is_guard_hook_command(h.get('command', ''))}
        for want in wanted:
            if want['command'] in present:
                updated += _refresh_guard(present[want['command']], want)
            else:
                entries.append({'hooks': [dict(want)]})
                added += 1
    return added, updated, retracted


def _json_install_hook_manager(provider, settings_path: str) -> dict:
    data = read_json_settings(settings_path)
    hooks = data.setdefault('hooks', {})
    added, updated = _merge_hook_manager_blocks(hooks, hook_manager_events(provider), provider)
    retracted = _retract_unwired_routes(hooks)
    g_added, g_updated, g_retracted = _sync_guard_entries(hooks, provider)
    added, updated, retracted = added + g_added, updated + g_updated, retracted + g_retracted
    if added == 0 and updated == 0 and retracted == 0:
        return {'ok': True, 'msg': 'Hook manager already installed'}
    write_json_settings(data, settings_path)
    parts = []
    if added:
        parts.append(f'{added} added')
    if updated:
        parts.append(f'{updated} updated')
    if retracted:
        parts.append(f'{retracted} retracted')
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


def _strip_entry(entry, is_match) -> tuple[object, int]:
    """One matcher entry with our commands dropped, and how many went.

    Every hand-mangled shape the readers tolerate reaches this writer too, and
    `regin doctor` now prints `hooks adopt` — so a traceback here strands the
    user exactly where the repair line told them to go. An entry it cannot
    understand is returned untouched rather than rewritten or dropped.
    """
    if not isinstance(entry, dict) or not isinstance(entry.get('hooks'), list):
        return entry, 0
    kept = [h for h in entry['hooks']
            if not (isinstance(h, dict) and is_match(h.get('command', '')))]
    if len(kept) == len(entry['hooks']):
        return entry, 0
    return {**entry, 'hooks': kept}, len(entry['hooks']) - len(kept)


def _strip_matching_blocks(hooks: dict, is_match) -> int:
    """Remove command blocks satisfying `is_match` from a settings.json `hooks` map."""
    removed = 0
    for event_name in list(hooks.keys()):
        entries = hooks[event_name]
        if not isinstance(entries, list):
            continue
        filtered, dropped = _strip_event(entries, is_match)
        removed += dropped
        # Same rule one level up: an event key we emptied goes, one the
        # user left empty stays.
        if filtered or not dropped:
            hooks[event_name] = filtered
        else:
            del hooks[event_name]
    return removed


def _strip_event(entries: list, is_match) -> tuple[list, int]:
    filtered = []
    removed = 0
    for entry in entries:
        next_entry, dropped = _strip_entry(entry, is_match)
        removed += dropped
        # An entry we emptied goes; one that was already empty is the
        # user's, and deleting it is not this function's business.
        if dropped == 0 or next_entry['hooks']:
            filtered.append(next_entry)
    return filtered, removed


def _remove_json_entries(settings_path: str, is_ours) -> int:
    data = read_json_settings(settings_path)
    hooks = data.get('hooks')
    if not isinstance(hooks, dict):
        return 0
    removed = _strip_matching_blocks(hooks, is_ours)
    if not removed:
        return 0
    if not hooks:
        data.pop('hooks', None)
    write_json_settings(data, settings_path)
    return removed


def _remove_toml_entries(settings_path: str, is_ours, label: str) -> int:
    before = sum(len(c) for c in kimi_hooks.installed_command_map(settings_path, is_ours).values())
    removed = kimi_hooks.uninstall(settings_path, label=label, is_ours=is_ours)
    return before if removed else 0


def remove_entries(provider, settings_path: str, kind: str) -> int:
    """Delete this checkout's `kind` entries from a provider's hook config.

    The one removal every entry point shares, so none of them can grow its
    own looser idea of "ours" — `init --force` once matched any
    `-m hook_manager` and stripped a second checkout's wiring with it. A
    missing or unparseable file is left exactly as found, and the file is
    only rewritten when something was removed. Returns how many went.
    """
    is_ours = _kind_specs(provider)[kind]['is_ours']
    if kind == 'hook_manager':
        is_ours = _is_any_hook_manager_command
    if is_toml_provider(provider):
        label = kimi_hooks.DEFAULT_LABEL if kind == 'hook_manager' else DEBUG_LABEL
        return _remove_toml_entries(settings_path, is_ours, label)
    return _remove_json_entries(settings_path, is_ours)


def uninstall_hook_manager(provider, settings_path: str) -> dict:
    if not is_toml_provider(provider) and (refusal := _unreadable_error(settings_path)):
        return refusal
    removed = remove_entries(provider, settings_path, 'hook_manager')
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
    if not is_toml_provider(provider) and (refusal := _unreadable_error(settings_path)):
        return refusal
    removed = remove_entries(provider, settings_path, 'debug')
    return {'ok': True, 'msg': 'Debug hook removed' if removed else 'Debug hook was not installed'}


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
    _remove_json_entries(settings_path, is_foreign)


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
