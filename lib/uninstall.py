"""What `regin uninstall` removes, and on what evidence each item is ours.

Every removal is planned first and carries its ownership basis, so the dry
run and the real run list the same thing and nothing is deleted on a guess:
hook entries go only when their interpreter prefix is this checkout's, skill
directories only when `pattern_deployments` records them, the `regin`
symlink only when it resolves into this checkout's venv. Anything that looks
like regin's but fails that test is reported for the user to handle.
"""

from __future__ import annotations

import filecmp
import os
import shutil
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from lib import hooks_wiring
from lib.settings import settings
from lib.venv_paths import WINDOWS, venv_script

HOOK_KINDS = ('hook_manager', 'debug')
TOPIC_GIT_HOOKS = ('pre-commit', 'post-commit', 'post-merge', 'post-checkout')


@dataclass
class Removal:
    what: str
    path: str
    basis: str
    apply: Callable[[], None] = field(repr=False)


@dataclass
class Kept:
    what: str
    path: str
    reason: str


@dataclass
class Plan:
    removals: list[Removal] = field(default_factory=list)
    kept: list[Kept] = field(default_factory=list)
    other_checkouts: set[str] = field(default_factory=set)


def hook_providers() -> list:
    """Every registered provider that has hook wiring, visible or not.

    Not just the visible set: a provider hidden after its hooks were
    installed still has entries pointing at this checkout.
    """
    from lib.providers import build_provider, list_provider_ids
    built = [build_provider(pid) for pid in list_provider_ids()]
    return [p for p in built if p.capabilities.hooks]


def _count(command_map: dict) -> int:
    return sum(len(commands) for commands in command_map.values())


def _plan_hooks(plan: Plan, provider) -> None:
    path = str(provider.hook_settings_path())
    if not os.path.exists(path):
        return
    try:
        _require_parseable(provider, path)
        _plan_provider_hooks(plan, provider, path)
    except Exception as exc:  # noqa: BLE001 — one bad file must not stop the rest
        plan.kept.append(Kept(f"{provider.provider_id} hooks", path, f"could not read: {exc}"))
        plan.other_checkouts.add(f"unreadable {path}")


def _require_parseable(provider, path: str) -> None:
    """Raise on a hook config that has content but will not parse.

    The readers flatten that to "no hooks", which here would read as "no other
    checkout is wired" and release the shared artifacts.
    """
    if not hooks_wiring.is_toml_provider(provider):
        if hooks_wiring.json_settings_unreadable(path):
            raise ValueError('not valid JSON')
        return
    with open(path, 'rb') as f:
        tomllib.load(f)


def _plan_provider_hooks(plan: Plan, provider, path: str) -> None:
    specs = hooks_wiring._kind_specs(provider)
    for kind in HOOK_KINDS:
        spec = specs[kind]
        ours = _count(hooks_wiring.installed_command_map(provider, path, spec['is_ours']))
        if ours:
            plan.removals.append(Removal(
                f"{provider.provider_id} {kind} hooks ({ours} entries)", path,
                'command runs this checkout\'s interpreter',
                lambda p=provider, k=kind: hooks_wiring.remove_entries(p, path, k),
            ))
        foreign = hooks_wiring.installed_command_map(provider, path, spec['is_foreign'])
        if foreign:
            roots = hooks_wiring._foreign_roots(foreign) or ['another regin checkout']
            plan.other_checkouts.update(roots)
            plan.kept.append(Kept(
                f"{provider.provider_id} {kind} hooks ({_count(foreign)} entries)", path,
                f"routed to {', '.join(roots)}",
            ))


def _plan_hook_manager_config(plan: Plan, providers) -> None:
    seen: set[str] = set()
    for provider in providers:
        path = str(provider.hook_manager_config_path())
        if path in seen or not os.path.isfile(path):
            continue
        seen.add(path)
        plan.removals.append(Removal(
            f"{provider.provider_id} hook-manager config", path,
            'path fixed by the provider; contents are regin\'s alone',
            lambda p=path: os.remove(p),
        ))


def _skill_dir(row: dict) -> str | None:
    path = row.get('deployed_path')
    if not path:
        return None
    path = os.path.abspath(path)
    return os.path.dirname(path) if os.path.basename(path) == 'SKILL.md' else path


def _forget_and_remove(row: dict, skill_dir: str) -> None:
    from lib.patterns import pattern_deployments
    _remove_path(Path(skill_dir))
    pattern_deployments.remove_deployment(
        row['pattern_slug'], row['scope'], row['project_id'], row['provider'])


def _plan_skills(plan: Plan) -> None:
    from lib.patterns import pattern_deployments
    for row in pattern_deployments.list_deployments():
        skill_dir = _skill_dir(row)
        if not skill_dir or not os.path.isdir(skill_dir):
            continue
        # A row is evidence for `<base>/<slug>` and nothing wider; a path
        # that does not end in the slug would make rmtree reach past it.
        if os.path.basename(skill_dir) != row['pattern_slug']:
            plan.kept.append(Kept(f"skill {row['pattern_slug']}", skill_dir,
                                  'recorded path does not end in the skill id'))
            continue
        plan.removals.append(Removal(
            f"skill {row['pattern_slug']}", skill_dir, 'recorded in pattern_deployments',
            lambda r=row, d=skill_dir: _forget_and_remove(r, d),
        ))


def _plan_symlink(plan: Plan, bin_dir: Path) -> None:
    ours = Path(venv_script(settings.project_root, 'regin'))
    link = bin_dir / ours.name
    if not os.path.lexists(link):
        return
    if WINDOWS:
        # Setup copies the launcher there (symlinks need admin rights), and
        # pip's launcher embeds its venv's interpreter path, so a byte-equal
        # copy is this checkout's.
        if ours.exists() and filecmp.cmp(link, ours, shallow=False):
            plan.removals.append(Removal(
                'regin launcher', str(link), f"copy of {ours}", lambda: os.remove(link),
            ))
        else:
            plan.kept.append(Kept('regin launcher', str(link), "not this checkout's"))
        return
    if link.is_symlink() and os.path.realpath(link) == os.path.realpath(ours):
        plan.removals.append(Removal(
            'regin launcher symlink', str(link), f"points at {ours}",
            lambda: os.remove(link),
        ))
        return
    target = os.readlink(link) if link.is_symlink() else 'not a symlink'
    plan.kept.append(Kept('regin launcher', str(link), f"not this checkout's ({target})"))


def _remove_path(path: Path) -> None:
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
    elif os.path.lexists(path):
        os.remove(path)


def data_paths() -> list[Path]:
    db_dir = settings.main_worktree / 'db'
    paths = [Path(settings.data_dir)]
    paths += sorted(p for pattern in ('*.db', '*.db-wal', '*.db-shm')
                    for p in db_dir.glob(pattern))
    return [p for p in paths if os.path.lexists(p)]


def _plan_data(plan: Plan) -> None:
    for path in data_paths():
        plan.removals.append(Removal(
            'regin data', str(path), '--purge-data', lambda p=path: _remove_path(p),
        ))


def _sharers(plan: Plan) -> list[str]:
    """Who else may be using the shared artifacts, if anyone.

    Hooks and the launcher name their checkout; skills, hook-manager config
    and data do not. The deployment records and DB belong to the main
    checkout and every worktree of it, and the config path is per provider,
    not per checkout. So they go only when nothing else can still be relying
    on them: no other checkout's hooks remain, and this is not a worktree.
    """
    sharers = sorted(plan.other_checkouts)
    if os.path.realpath(settings.project_root) != os.path.realpath(settings.main_worktree):
        sharers.append(f"main checkout {settings.main_worktree} (this is a worktree of it)")
    return sharers


def _keep_shared(plan: Plan, shared: Plan, sharers: list[str]) -> None:
    reason = 'shared with ' + '; '.join(sharers)
    plan.kept += shared.kept
    plan.kept += [Kept(item.what, item.path, reason) for item in shared.removals]


def build_plan(providers, bin_dir: Path, purge_data: bool = False) -> Plan:
    """Everything uninstall would remove, plus what it found and will leave.

    Data goes last: skill removal reads and updates `pattern_deployments`,
    which lives in the database `--purge-data` deletes.
    """
    plan = Plan()
    for provider in providers:
        _plan_hooks(plan, provider)
    _plan_symlink(plan, bin_dir)
    shared = Plan()
    _plan_hook_manager_config(shared, providers)
    _plan_skills(shared)
    if purge_data:
        _plan_data(shared)
    sharers = _sharers(plan)
    if sharers:
        _keep_shared(plan, shared, sharers)
    else:
        plan.removals += shared.removals
        plan.kept += shared.kept
    return plan


def manual_steps() -> list[str]:
    hooks = ', '.join(TOPIC_GIT_HOOKS)
    return [
        "Claude Code plugins: run `/plugin uninstall <name>` for each regin plugin you installed.",
        "statusLine: remove `statusLine.command` from ~/.claude/settings.json if it runs regin.",
        f"Topic git hooks: check .git/hooks/{{{hooks}}} in repos where you ran "
        "`regin topics` — regin does not mark them, so it does not remove them.",
        f"Delete the checkout itself: rm -rf {settings.project_root}",
    ]
