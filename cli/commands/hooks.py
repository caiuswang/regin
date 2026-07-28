"""`regin hooks ...` — inspect, install, and repair regin's hook wiring.

The install writers were reachable only through the web API until CAI-21, so
a checkout whose hook command had drifted (CAI-15: a debug hook missing
`--silent`, printing into Kimi's UI) had no repair path but hand-editing the
provider's config. `repair` is the one-liner `regin doctor` points at.

Every subcommand works off `lib.hooks_wiring`, the same code the Settings hook
installers call, so the CLI and the UI can never write different commands.
"""

from __future__ import annotations

import json as _json

import typer

from cli.output import echo, error, table
from lib import hooks_wiring
from lib.providers import build_provider, list_visible_provider_ids


hooks_app = typer.Typer(
    name="hooks",
    help="Inspect, install, and repair regin's agent hook wiring.",
    no_args_is_help=True,
)

_KINDS = ('hook_manager', 'debug')


@hooks_app.callback()
def _main() -> None:
    """Inspect, install, and repair regin's agent hook wiring."""


def _providers(provider: str | None):
    """Hook-capable providers to act on, or exit 1 on an unknown id.

    Providers without hook support are named rather than silently dropped —
    an empty table and exit 0 reads as "nothing to do", not "not applicable".
    """
    ids = list_visible_provider_ids()
    if provider:
        if provider not in ids:
            error(f"Unknown provider: {provider} (known: {', '.join(ids)})")
            raise typer.Exit(1)
        ids = [provider]
    built = [build_provider(pid) for pid in ids]
    skipped = [p.provider_id for p in built if not p.capabilities.hooks]
    if skipped:
        echo(f"Skipping (hooks not supported): {', '.join(skipped)}")
    return [p for p in built if p.capabilities.hooks]


def _targets(provider: str | None, every: bool):
    """Providers for a write command — explicit scope required.

    `install`/`remove` write outside the repo (a provider's own config file),
    so fanning out across every visible provider must be asked for, not the
    default. `status`/`repair` are safe to run unscoped.
    """
    if not provider and not every:
        error('Specify --provider <id>, or --all to act on every hook-capable provider.')
        raise typer.Exit(2)
    return _providers(provider)


def _settings_path(provider) -> str:
    return str(provider.hook_settings_path())


def _status_rows(providers) -> list[dict]:
    """One row per provider/kind, skipping any provider whose config won't read.

    `repair` reads these rows before it writes anything, so an unreadable path
    on one provider would otherwise abort the run before the others are fixed.
    """
    rows = []
    for provider in providers:
        try:
            wiring = hooks_wiring.wiring_status(provider, _settings_path(provider))
        except Exception as exc:  # noqa: BLE001 — report and keep going
            error(f"  {provider.provider_id}: wiring read failed — {exc}")
            continue
        for kind in _KINDS:
            rows.append({'provider': provider.provider_id, 'hook': kind, **wiring[kind]})
    return rows


def _state_label(row: dict) -> str:
    if not row['installed']:
        return 'not installed'
    return 'STALE' if row['stale'] else 'ok'


def _foreign_label(row: dict) -> str:
    """Where another checkout's entries for this hook come from.

    Its own column rather than folded into STATE: a foreign entry can sit
    beside a perfectly healthy install of ours (both then fire), so it is not
    an alternative state — it is an extra fact about the same row.
    """
    if not row['foreign_events']:
        return '-'
    return ', '.join(row['foreign_roots']) or 'other checkout'


_REPAIR_PREVIEW = 3


def _repair_label(row: dict) -> str:
    """Which events need rewriting, truncated — a partially-installed router
    can list every spec event and turn one table row into a wall of text."""
    events = row['stale_events'] + row['missing_events']
    if not events:
        return '-'
    head = ', '.join(events[:_REPAIR_PREVIEW])
    extra = len(events) - _REPAIR_PREVIEW
    return f'{head} (+{extra} more)' if extra > 0 else head


@hooks_app.command("status", help="Show installed vs. expected hook commands per provider.")
def cmd_status(
    provider: str = typer.Option(None, "--provider", "-p", help="Only this provider id."),
    json: bool = typer.Option(False, "--json", help="Emit machine-readable JSON."),
) -> None:
    rows = _status_rows(_providers(provider))
    if json:
        echo(_json.dumps(rows, indent=2))
        return
    table(
        [(r['provider'], r['hook'], _state_label(r), len(r['routed_events']),
          _foreign_label(r), _repair_label(r)) for r in rows],
        headers=('PROVIDER', 'HOOK', 'STATE', 'ROUTED', 'OTHER CHECKOUT', 'NEEDS REPAIR'),
    )
    if any(r['stale'] for r in rows):
        echo("\nSome wiring is out of date — run `regin hooks repair`.")
    if any(r['foreign_events'] for r in rows):
        echo("\nSome events are routed to a different regin checkout — `regin hooks install` "
             "would add a second entry beside it. Run `regin hooks adopt` to take it over.")


def _apply(action, providers, kinds: tuple[str, ...]) -> int:
    """Run one writer per provider/kind. Returns the number that failed.

    Each call is isolated: one provider with a hand-mangled config must not
    abort the rest of the fan-out.
    """
    failures = 0
    for provider in providers:
        for kind in kinds:
            try:
                result = action[kind](provider, _settings_path(provider))
            except Exception as exc:  # noqa: BLE001 — report and keep going
                failures += 1
                error(f"  {provider.provider_id}/{kind}: failed — {exc}")
                continue
            if not result.get('ok', True):
                failures += 1
                error(f"  {provider.provider_id}/{kind}: {result['msg']}")
                continue
            echo(f"  {provider.provider_id}/{kind}: {result['msg']}")
    return failures


def _kinds(debug: bool, only_debug: bool) -> tuple[str, ...]:
    if only_debug:
        return ('debug',)
    return _KINDS if debug else ('hook_manager',)


@hooks_app.command("install", help="Install (or refresh) hook wiring for a provider.")
def cmd_install(
    provider: str = typer.Option(None, "--provider", "-p", help="Only this provider id."),
    every: bool = typer.Option(False, "--all", help="Act on every hook-capable provider."),
    debug: bool = typer.Option(False, "--debug", help="Also install the debug payload logger."),
    only_debug: bool = typer.Option(False, "--only-debug", help="Install only the debug hook."),
) -> None:
    failed = _apply(hooks_wiring.INSTALLERS, _targets(provider, every), _kinds(debug, only_debug))
    if failed:
        raise typer.Exit(1)


@hooks_app.command("remove", help="Remove regin's hook wiring for a provider.")
def cmd_remove(
    provider: str = typer.Option(None, "--provider", "-p", help="Only this provider id."),
    every: bool = typer.Option(False, "--all", help="Act on every hook-capable provider."),
    debug: bool = typer.Option(False, "--debug", help="Also remove the debug payload logger."),
    only_debug: bool = typer.Option(False, "--only-debug", help="Remove only the debug hook."),
) -> None:
    failed = _apply(hooks_wiring.UNINSTALLERS, _targets(provider, every), _kinds(debug, only_debug))
    if failed:
        raise typer.Exit(1)


@hooks_app.command("adopt", help="Take over hook entries another regin checkout installed here.")
def cmd_adopt(
    provider: str = typer.Option(None, "--provider", "-p", help="Only this provider id."),
    every: bool = typer.Option(False, "--all", help="Act on every hook-capable provider."),
    debug: bool = typer.Option(False, "--debug", help="Also adopt the debug payload logger."),
    only_debug: bool = typer.Option(False, "--only-debug", help="Adopt only the debug hook."),
) -> None:
    """Replace another checkout's entries with this one's.

    Deliberately a separate command, not a fallback inside `install`: a moved
    checkout and a genuine second one are indistinguishable on disk, so taking
    over has to be asked for.
    """
    failed = _apply(hooks_wiring.ADOPTERS, _targets(provider, every), _kinds(debug, only_debug))
    if failed:
        raise typer.Exit(1)


@hooks_app.command("repair", help="Rewrite any hook command that has drifted from what install writes today.")
def cmd_repair(
    provider: str = typer.Option(None, "--provider", "-p", help="Only this provider id."),
) -> None:
    """Reinstall only where install would actually change something.

    Scoped to *stale* wiring rather than reinstalling everything so running it
    on a healthy machine is a genuine no-op and safe to suggest from doctor.
    """
    providers = {p.provider_id: p for p in _providers(provider)}
    stale = [r for r in _status_rows(providers.values()) if r['stale']]
    if not stale:
        echo("Hook wiring is up to date — nothing to repair.")
        return
    failures = 0
    for row in stale:
        target = providers[row['provider']]
        try:
            result = hooks_wiring.INSTALLERS[row['hook']](target, _settings_path(target))
        except Exception as exc:  # noqa: BLE001 — one bad config must not
            failures += 1              # abort the remaining providers
            error(f"  {row['provider']}/{row['hook']}: repair failed — {exc}")
            continue
        if not result.get('ok', True):
            failures += 1
            error(f"  {row['provider']}/{row['hook']}: {result['msg']}")
            continue
        echo(f"  {row['provider']}/{row['hook']}: {result['msg']}")
    if failures:
        raise typer.Exit(1)
