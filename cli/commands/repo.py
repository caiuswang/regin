"""Discover / add-repo / remove-repo — commands that manage registered repos."""

from __future__ import annotations

import typer

from cli.deps import require_db


# ── discover ──────────────────────────────────────────────────

@require_db
def cmd_discover(
    dry_run: bool = typer.Option(False, "--dry-run", help="Show repos without registering"),
    verbose: bool = typer.Option(False, "-v", "--verbose"),
) -> None:
    from lib.sync.repo_discovery import scan_repos, register_repos

    print("Scanning configured paths for repositories...")
    repos = scan_repos()
    print(f"Found {len(repos)} git repositories.")

    if dry_run:
        for r in repos:
            print(f"  {r['name']:30s}  branch={r['default_branch']:15s}  {r['path']}")
        return

    stats = register_repos(repos)
    print(f"Registered: {stats['added']} added, {stats['updated']} updated, "
          f"{stats['removed']} removed, {stats['skipped']} unchanged.")

    if verbose:
        for r in repos:
            print(f"  {r['name']:30s}  branch={r['default_branch']}")


# ── add-repo / remove-repo ────────────────────────────────────

@require_db
def cmd_add_repo(
    path: str = typer.Argument(..., help="Absolute path of the git repo to register"),
) -> None:
    from lib.sync.repo_discovery import RepoAddError, add_repo

    try:
        info = add_repo(path)
    except RepoAddError as exc:
        print(f"  error: {exc}")
        raise typer.Exit(1)
    print(f"  added {info['name']} ({info['default_branch']}) at {info['path']}")


@require_db
def cmd_remove_repo(
    name: str = typer.Argument(..., help="Name of the registered repo to drop"),
) -> None:
    from lib.sync.repo_discovery import remove_repo

    result = remove_repo(name)
    if not result["removed"]:
        print(f"  not found: {name}")
        raise typer.Exit(1)
    print(f"  removed {name}")


@require_db
def cmd_prune_repos(
    dry_run: bool = typer.Option(False, "--dry-run", help="Show what would be pruned without deleting"),
) -> None:
    from lib.sync.repo_discovery import prune_orphan_repos

    pruned = prune_orphan_repos(dry_run=dry_run)
    if not pruned:
        print("  no orphan repos to prune")
        return
    verb = "would prune" if dry_run else "pruned"
    for row in pruned:
        print(f"  {verb} {row['name']} ({row['reason']}) at {row['path']}")
    print(f"  {len(pruned)} total")


def register(app: typer.Typer) -> None:
    app.command("discover", help="Re-register every repo in settings.repo_paths")(cmd_discover)
    app.command("add-repo", help="Register a single git repo by path")(cmd_add_repo)
    app.command("remove-repo", help="Unregister a repo by name")(cmd_remove_repo)
    app.command("prune-repos", help="Drop Repo rows whose path is missing or under $TMPDIR")(cmd_prune_repos)
