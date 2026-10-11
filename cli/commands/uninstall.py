"""`regin uninstall` — remove what this checkout wrote outside the repo."""

from __future__ import annotations

from pathlib import Path

import typer

from cli.output import echo, error
from lib import uninstall as uninstall_lib


def _print_plan(plan: uninstall_lib.Plan, dry_run: bool) -> None:
    verb = 'Would remove' if dry_run else 'Will remove'
    if plan.removals:
        echo(f"{verb}:")
        for item in plan.removals:
            echo(f"  - {item.what}: {item.path}  [{item.basis}]")
    else:
        echo("Nothing of this checkout's to remove.")
    if plan.kept:
        echo("\nLeft in place (no evidence regin wrote it from this checkout):")
        for item in plan.kept:
            echo(f"  - {item.what}: {item.path}  [{item.reason}]")


def _apply(plan: uninstall_lib.Plan) -> int:
    failures = 0
    for item in plan.removals:
        try:
            item.apply()
        except Exception as exc:  # noqa: BLE001 — report and keep going
            failures += 1
            error(f"  failed: {item.what}: {item.path} — {exc}")
            continue
        echo(f"  removed: {item.what}: {item.path}")
    return failures


def _print_manual_steps() -> None:
    echo("\nManual steps regin cannot do for you:")
    for step in uninstall_lib.manual_steps():
        echo(f"  - {step}")


def cmd_uninstall(
    dry_run: bool = typer.Option(False, "--dry-run", help="List what would be removed; change nothing."),
    purge_data: bool = typer.Option(False, "--purge-data",
                                    help="Also delete settings.data_dir and db/*.db."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip the confirmation prompt."),
    bin_dir: Path = typer.Option(Path.home() / '.local' / 'bin', "--bin-dir", envvar="REGIN_BIN_DIR",
                                 help="Where setup.sh linked the `regin` launcher."),
) -> None:
    plan = uninstall_lib.build_plan(uninstall_lib.hook_providers(), bin_dir, purge_data)
    _print_plan(plan, dry_run)
    if dry_run or not plan.removals:
        _print_manual_steps()
        return
    if not yes and not typer.confirm("\nProceed?", default=False):
        echo("Aborted; nothing removed.")
        raise typer.Exit(1)
    failed = _apply(plan)
    _print_manual_steps()
    if failed:
        raise typer.Exit(1)


def register(app: typer.Typer) -> None:
    app.command(
        "uninstall",
        help="Remove regin's hooks, deployed skills, hook-manager config and launcher "
             "symlink written by this checkout; --purge-data also deletes its data.",
    )(cmd_uninstall)
