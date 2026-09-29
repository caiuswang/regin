"""`regin topics` validation and disk sync: check, reconcile, import,
install-hook, router-skill and rebuild-query-df."""

from __future__ import annotations

import json
from pathlib import Path

import typer

from lib.topics import (
    TopicGraphError,
    generate_topic_router_skill,
    install_topic_hooks,
    validate,
)

from ._common import _repo_path, topics_app


@topics_app.command("check", help="Validate topic graph schema and approved refs")
def cmd_topics_check(
    repo: str | None = typer.Option(None, "--repo", help="Repository path"),
) -> None:
    result = validate(_repo_path(repo))
    for warning in result.warnings:
        print(f"warning: {warning}")
    if not result.ok:
        for error in result.errors:
            print(f"error: {error}")
        raise typer.Exit(1)
    print("Topic graph is valid.")


def _resolve_repo_id_for_import(repo_path: Path) -> int:
    """Return Repo.id for `repo_path`, or exit(1) with a helpful message."""
    from sqlmodel import select

    from lib.orm import SessionLocal
    from lib.orm.models import Repo

    p = str(repo_path.resolve())
    with SessionLocal() as s:
        repo_row = s.exec(select(Repo).where(Repo.path == p)).first()
    if repo_row is None or repo_row.id is None:
        print(f"Repo not registered: run `regin add-repo {repo_path}` first")
        raise typer.Exit(1)
    return repo_row.id


def _read_disk_state(repo_path: Path) -> tuple[dict, dict[str, str]]:
    """Return (merged graph, wikis) from disk, or exit(1) on read failure.

    The graph is the effective merge of the base graph and the local
    ``topic.local.json`` overlay, matching what `load_authoritative_graph`
    captures into a snapshot — so the import sync compares like for like.
    """
    from lib.topics.core import TopicGraphError, load_graph_merged, topic_split_dir
    from lib.topics.graph_io import _read_wiki_pages_from_disk

    try:
        disk_graph = load_graph_merged(repo_path)
    except (OSError, json.JSONDecodeError, TopicGraphError) as exc:
        print(f"Failed to read {topic_split_dir(repo_path)}: {exc}")
        raise typer.Exit(1)
    return disk_graph, _read_wiki_pages_from_disk(repo_path, disk_graph)


def _snap_matches_disk(snap, disk_graph: dict, disk_wikis: dict[str, str]) -> bool:
    """True iff `snap`'s graph + wiki bodies equal the on-disk content."""
    from lib.topics.graph_io import _graph_hash

    snap_graph = json.loads(snap.graph_json)
    snap_wikis = json.loads(snap.wiki_pages_json or "{}")
    return _graph_hash(disk_graph) == _graph_hash(snap_graph) and disk_wikis == snap_wikis


def _latest_snapshot_row(repo_id: int):
    """Return the `is_latest=1` GraphSnapshot row for `repo_id`, or None."""
    from sqlmodel import select

    from lib.orm import SessionLocal
    from lib.orm.models import GraphSnapshot

    with SessionLocal() as s:
        return s.exec(
            select(GraphSnapshot)
            .where(GraphSnapshot.repo_id == repo_id)
            .where(GraphSnapshot.is_latest == 1)
        ).first()


@topics_app.command(
    "reconcile",
    help="Reconcile stale proposal 'accepted' markers against the approved graph")
def cmd_topics_reconcile(
    repo: str | None = typer.Option(None, "--repo", help="Repository path"),
    fix: bool = typer.Option(
        False, "--fix",
        help="Clear stale 'accepted' markers via the recovery primitive"),
) -> None:
    from lib.topics.reconcile import (
        find_stale_acceptances,
        fix_stale_acceptances,
        record_reconcile_audit,
    )

    rp = _repo_path(repo)
    stale = find_stale_acceptances(rp)
    if not stale:
        print("No stale acceptances — every accepted proposal topic is "
              "present in the approved graph.")
        return

    print(f"Stale acceptances ({len(stale)} topic(s) accepted but absent "
          f"from the approved graph):")
    for item in stale:
        runs = ", ".join(item["runs"])
        print(f"  • {item['topic_id']}  (last accepted@{item['accepted_at']}; "
              f"runs: {runs})")
    record_reconcile_audit(rp, stale, fixed=fix)

    if not fix:
        print("\nReport-only. Re-run with --fix to clear these stale markers.")
        return

    reset = fix_stale_acceptances(rp, stale)
    print(f"\nFixed: cleared {reset} stale 'accepted' marker(s) across "
          f"proposal rows.")


@topics_app.command("import", help="Sync the on-disk graph + wikis into a new GraphSnapshot")
def cmd_topics_import(
    repo: str | None = typer.Option(None, "--repo", help="Repository path"),
    reason: str = typer.Option(
        "manual",
        "--reason",
        help="Provenance tag for the snapshot (e.g. git_pull, manual)",
    ),
    quiet: bool = typer.Option(
        False, "--quiet",
        help="Suppress 'already in sync' output (intended for git hooks)",
    ),
) -> None:
    """Sync the on-disk approved graph into a new GraphSnapshot.

    Idempotent: no-op when the on-disk graph + per-topic wikis
    already match the latest snapshot. Use after `git pull` (manually
    or via the post-merge hook installed by `regin topics install-hook`)
    so a teammate's approved topics become routable locally — multi-user
    sharing without a shared database.
    """
    from lib.topics.core import graph_exists, topic_dir, topic_path, topic_split_dir
    from lib.topics.graph_io import load_authoritative_graph, sync_snapshot_from_disk

    repo_path = _repo_path(repo)
    if not graph_exists(repo_path):
        # Not gated by --quiet: the post-merge hook runs quiet, and a
        # legacy-only repo would otherwise fail totally silently.
        if topic_path(repo_path).exists():
            print(f"Legacy single-file layout retired: {topic_path(repo_path)} — "
                  "restore the split layout (.regin/topics/topics/) from git; "
                  "nothing imported")
        elif not quiet:
            print(f"No topic graph under {topic_dir(repo_path)} — nothing to import")
        return

    repo_id = _resolve_repo_id_for_import(repo_path)
    disk_graph, disk_wikis = _read_disk_state(repo_path)
    topic_count = len(disk_graph.get("topics") or {})
    snap = _latest_snapshot_row(repo_id)

    if snap is None:
        # No prior snapshot — drive through load_authoritative_graph so
        # the Phase 0 auto-seed path (which ingests wikis) takes over.
        load_authoritative_graph(str(repo_path))
        print(
            f"Seeded snapshot from {topic_split_dir(repo_path).name}/ (reason=auto_seed, "
            f"{topic_count} topics, {len(disk_wikis)} wikis)"
        )
        return

    if _snap_matches_disk(snap, disk_graph, disk_wikis):
        if not quiet:
            print("Already in sync")
        return

    snap_id = sync_snapshot_from_disk(repo_path, reason=reason)
    if snap_id is None:
        print(f"Import failed: sync_snapshot_from_disk returned None for {repo_path}")
        raise typer.Exit(1)
    print(
        f"Imported snapshot id={snap_id} (reason={reason}, "
        f"{topic_count} topics, {len(disk_wikis)} wikis)"
    )


@topics_app.command(
    "install-hook",
    help="Install git hooks (pre-commit, post-merge, post-checkout) for topic sync",
)
def cmd_topics_install_hook(
    repo: str | None = typer.Option(None, "--repo", help="Repository path"),
) -> None:
    paths = install_topic_hooks(_repo_path(repo))
    for name, path in paths.items():
        print(f"Installed {name} hook: {path}")


@topics_app.command("router-skill", help="Print the generic topic-router skill")
def cmd_topics_router_skill(
    repo: str | None = typer.Option(None, "--repo", help="Repository path"),
) -> None:
    print(generate_topic_router_skill(_repo_path(repo)))


@topics_app.command(
    "rebuild-query-df",
    help="Rebuild the router's query-log term-frequency cache (query_df.json)",
)
def cmd_topics_rebuild_query_df(
    repo: str | None = typer.Option(None, "--repo", help="Repository path"),
) -> None:
    from lib.topics import rebuild_query_df

    count = rebuild_query_df(_repo_path(repo))
    print(f"query_df.json rebuilt from {count} routed prompt(s)")
