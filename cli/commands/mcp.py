"""`regin mcp register` — write regin's MCP servers into each provider's config."""

from __future__ import annotations

import typer

from cli.output import echo, error
from lib.providers import build_provider, list_provider_ids
from lib.providers.mcp_registration import McpConfigUnreadable, register_provider


mcp_app = typer.Typer(
    name="mcp",
    help="Register regin's MCP servers (memory, send-to-user) with agent providers.",
    no_args_is_help=True,
)


@mcp_app.callback()
def _main() -> None:
    """Register regin's MCP servers with agent providers."""


def _providers(provider: str | None):
    """An explicit --provider is written even on a fresh machine; the unscoped
    sweep skips agents whose config dir is absent rather than create it."""
    ids = list_provider_ids()
    if provider:
        if provider not in ids:
            error(f"Unknown provider: {provider} (known: {', '.join(ids)})")
            raise typer.Exit(1)
        ids = [provider]
    built = [build_provider(pid) for pid in ids]
    takes_mcp = [p for p in built if p.mcp_config_path() is not None]
    if provider:
        return takes_mcp
    return [p for p in takes_mcp if p.mcp_config_path().parent.is_dir()]


def _report(result) -> None:
    state = "updated" if result.changed else "unchanged"
    echo(f"{result.provider_id}: {', '.join(result.registered)} → {result.path} ({state})")
    if result.removed:
        echo(f"  removed: {', '.join(result.removed)}")
    if result.trust_hint:
        echo(f"  {result.trust_hint}")


@mcp_app.command("register", help="Merge memory / send-to-user into each provider's MCP config.")
def cmd_register(
    provider: str = typer.Option(None, "--provider", "-p", help="Only this provider id."),
) -> None:
    targets = _providers(provider)
    if not targets:
        echo("No provider takes MCP registration from regin.")
        return
    failed = 0
    for target in targets:
        try:
            _report(register_provider(target))
        except McpConfigUnreadable as exc:
            error(f"{target.provider_id}: {exc}")
            failed += 1
    if failed:
        raise typer.Exit(1)


def register(app: typer.Typer) -> None:
    app.add_typer(mcp_app)
