"""`regin topics ...` - maintain repo-local topic graphs."""

from __future__ import annotations

from pathlib import Path
import json

import typer

from lib.settings import settings
from lib.topics.wiki import generate_wiki
from lib.topics import (
    TopicGraphError,
    bootstrap,
    delete_topic,
    generate_topic_router_skill,
    install_topic_hooks,
    load_graph,
    load_local_graph,
    promote_all_topics,
    promote_topic,
    route_topic,
    scan,
    validate,
)


topics_app = typer.Typer(
    name="topics",
    help="Maintain repo-local curated topic graphs",
    no_args_is_help=True,
)


def _registered_repo_by_name(repo: str) -> Path | None:
    """Resolve a bare repo name (e.g. `regin`) against `settings.repo_paths`,
    returning the registered tree only if it carries a `.regin/` directory."""
    for candidate in settings.repo_paths or []:
        candidate_path = Path(candidate).expanduser().resolve()
        if candidate_path.name == repo and (candidate_path / ".regin").is_dir():
            return candidate_path
    return None


def _repo_path(repo: str | None) -> Path:
    """Resolve `--repo` to an absolute repo root, with a useful error when
    the argument doesn't look like a real repo. Accepts either a path
    (absolute or relative) or a registered repo name from
    `settings.repo_paths` — the common typo `--repo regin` would
    otherwise resolve relative to cwd and silently miss the right tree.
    """
    if not repo:
        return Path(settings.project_root).resolve()

    resolved = Path(repo).resolve()
    if (resolved / ".regin").is_dir():
        return resolved

    # Name lookup against settings.repo_paths takes precedence over a bare
    # existing directory so the common typo `--repo regin` (which would
    # resolve cwd-relative) still finds the registered tree.
    named = _registered_repo_by_name(repo)
    if named is not None:
        return named

    # Fall back to any existing directory: `bootstrap` creates `.regin/` (so it
    # must accept a tree that doesn't have one yet) and `import` must no-op
    # gracefully on an un-bootstrapped repo from the post-merge git hook. The
    # other subcommands surface a missing graph as their own error downstream.
    if resolved.is_dir():
        return resolved

    known = [str(Path(p).expanduser().resolve()) for p in (settings.repo_paths or [])]
    hint = (
        "\n  registered repos: " + ", ".join(known) if known
        else "\n  (no repo_paths registered — pass an absolute path)"
    )
    raise typer.BadParameter(
        f"--repo {repo!r} doesn't resolve to an existing repo directory"
        f" (tried {resolved}).{hint}"
    )


@topics_app.command("bootstrap", help="Create .regin/topics/topic.json for a repo")
def cmd_topics_bootstrap(
    repo: str | None = typer.Option(None, "--repo", help="Repository path"),
    seeds: bool = typer.Option(False, "--seeds", help="Create a few starter topics"),
    force: bool = typer.Option(False, "--force", help="Overwrite existing topic files"),
) -> None:
    try:
        paths = bootstrap(_repo_path(repo), seeds=seeds, force=force)
    except TopicGraphError as exc:
        print(f"Topic bootstrap failed: {exc}")
        raise typer.Exit(1)
    print(f"Topic graph created: {paths['topic']}")


@topics_app.command("scan", help="Refresh refs on approved topics from current files")
def cmd_topics_scan(
    repo: str | None = typer.Option(None, "--repo", help="Repository path"),
    staged: bool = typer.Option(False, "--staged", help="Scan staged file names only"),
) -> None:
    try:
        result = scan(_repo_path(repo), staged=staged)
    except TopicGraphError as exc:
        print(f"Topic scan failed: {exc}")
        raise typer.Exit(1)
    print(f"Updated topics: {len(result['updated_topics'])}")
    print(f"Covered refs: {result['covered_ref_count']}")


@topics_app.command(
    "list",
    help="Show topic ids by layer: shared (topic.json) vs local overlay",
)
def cmd_topics_list(
    repo: str | None = typer.Option(None, "--repo", help="Repository path or registered name"),
    local_only: bool = typer.Option(False, "--local", help="Show only the local overlay (added + tombstoned)"),
) -> None:
    """Print topic ids grouped by where they live, so the user knows what's
    eligible for `topics promote` without having to open the JSON files."""
    try:
        path = _repo_path(repo)
        base_topics = sorted((load_graph(path).get("topics") or {}).keys())
        overlay = load_local_graph(path)
    except TopicGraphError as exc:
        print(f"Topic list failed: {exc}")
        raise typer.Exit(1)
    overlay_topics = sorted((overlay.get("topics") or {}).keys())
    deleted_topics = sorted(overlay.get("deleted_topics") or [])

    if not local_only:
        print(f"Shared ({len(base_topics)}) — in topic.json, travel via git:")
        for tid in base_topics:
            print(f"  {tid}")
    print(f"Local-added ({len(overlay_topics)}) — in topic.local.json, eligible for `topics promote`:")
    for tid in overlay_topics:
        print(f"  {tid}")
    print(f"Local-deleted ({len(deleted_topics)}) — tombstones, will remove from topic.json on promote:")
    for tid in deleted_topics:
        print(f"  {tid}")


def _print_promote_all(result: dict) -> None:
    """Render the `topics promote --all` summary."""
    added, removed = result["added"], result["removed"]
    if not added and not removed:
        print("Nothing to promote: local overlay has no pending changes.")
        return
    if added:
        print(f"Added to topic.json ({len(added)}): {', '.join(added)}")
    if removed:
        print(f"Removed from topic.json ({len(removed)}): {', '.join(removed)}")
    print("Cleared the overlay. Commit .regin/topics/ (+ wikis) to share it.")


@topics_app.command(
    "promote",
    help="Promote local-overlay topic changes into the git-tracked topic.json",
)
def cmd_topics_promote(
    topic_id: str | None = typer.Argument(
        None, help="Topic id present in topic.local.json (omit with --all)"
    ),
    repo: str | None = typer.Option(None, "--repo", help="Repository path"),
    promote_all: bool = typer.Option(
        False, "--all",
        help="Promote every overlay-added topic and tombstoned deletion at once",
    ),
) -> None:
    """Move an approved/edited topic out of the machine-local overlay and
    into the shared base `topic.json` so it travels via git. The topic is
    already live locally; this only makes it shareable. Pass `--all` to
    promote every pending overlay change in one pass instead of an id.
    """
    if promote_all == (topic_id is not None):
        raise typer.BadParameter("pass a topic id or --all (exactly one)")

    try:
        if promote_all:
            _print_promote_all(promote_all_topics(_repo_path(repo)))
            return
        single = promote_topic(_repo_path(repo), topic_id)
    except TopicGraphError as exc:
        print(f"Promote failed: {exc}")
        raise typer.Exit(1)

    where = "added to" if single["action"] == "added" else "removed from"
    print(f"Promoted '{single['topic_id']}': {where} topic.json; cleared from overlay.")
    print("Commit .regin/topics/topic.json (+ its wiki) to share it.")


@topics_app.command(
    "delete",
    help="Permanently delete an approved topic (graph + wiki); prunes inbound edges",
)
def cmd_topics_delete(
    topic_id: str = typer.Argument(..., help="Topic id to delete"),
    repo: str | None = typer.Option(None, "--repo", help="Repository path"),
) -> None:
    """Hard-delete an approved topic from the graph and remove its wiki.

    Use `downgrade` instead to move a topic back to a reviewable draft.
    """
    try:
        result = delete_topic(_repo_path(repo), topic_id)
    except TopicGraphError as exc:
        print(f"Delete failed: {exc}")
        raise typer.Exit(1)
    extras = []
    if result["pruned_edges"]:
        extras.append(f"pruned {result['pruned_edges']} inbound edge(s)")
    if result["wiki_removed"]:
        extras.append("removed wiki")
    suffix = f" ({'; '.join(extras)})" if extras else ""
    print(f"Deleted '{result['topic_id']}'{suffix}.")
    print("Commit .regin/topics/ to share the removal.")


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


def _read_disk_state(repo_path: Path, target: Path) -> tuple[dict, dict[str, str]]:
    """Return (merged graph, wikis) from disk, or exit(1) on read failure.

    The graph is the effective merge of base ``topic.json`` and the local
    ``topic.local.json`` overlay, matching what `load_authoritative_graph`
    captures into a snapshot — so the import sync compares like for like.
    """
    from lib.topics.core import TopicGraphError, load_graph_merged
    from lib.topics.graph_io import _read_wiki_pages_from_disk

    try:
        disk_graph = load_graph_merged(repo_path)
    except (OSError, json.JSONDecodeError, TopicGraphError) as exc:
        print(f"Failed to read {target}: {exc}")
        raise typer.Exit(1)
    return disk_graph, _read_wiki_pages_from_disk(target, disk_graph)


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


@topics_app.command("import", help="Sync disk topic.json + wikis into a new GraphSnapshot")
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

    Idempotent: no-op when the disk `topic.json` + per-topic wikis
    already match the latest snapshot. Use after `git pull` (manually
    or via the post-merge hook installed by `regin topics install-hook`)
    so a teammate's approved topics become routable locally — multi-user
    sharing without a shared database.
    """
    from lib.topics.core import topic_path
    from lib.topics.graph_io import load_authoritative_graph, sync_snapshot_from_disk

    repo_path = _repo_path(repo)
    target = topic_path(repo_path)
    if not target.exists():
        if not quiet:
            print(f"No topic.json at {target} — nothing to import")
        return

    repo_id = _resolve_repo_id_for_import(repo_path)
    disk_graph, disk_wikis = _read_disk_state(repo_path, target)
    topic_count = len(disk_graph.get("topics") or {})
    snap = _latest_snapshot_row(repo_id)

    if snap is None:
        # No prior snapshot — drive through load_authoritative_graph so
        # the Phase 0 auto-seed path (which ingests wikis) takes over.
        load_authoritative_graph(str(repo_path))
        print(
            f"Seeded snapshot from {target.name} (reason=auto_seed, "
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


@topics_app.command("wiki", help="Generate derived wiki files from approved topic.json")
def cmd_topics_wiki(
    repo: str | None = typer.Option(None, "--repo", help="Repository path"),
) -> None:
    try:
        paths = generate_wiki(_repo_path(repo))
    except (TopicGraphError, ValueError) as exc:
        print(f"Topic wiki generation failed: {exc}")
        raise typer.Exit(1)
    print(f"Topic wiki files written: {len(paths)}")
    for path in paths:
        print(path)


def _render_topic_wiki(result: dict) -> str:
    """The routed topic's wiki pages as plain markdown — the content the
    `<topic_context>` pointer promises, surfaced directly. The JSON envelope
    sorts `wiki_pages` below the (often long) `refs` list, so an agent that
    pipes the JSON through `head` never reaches the content; `--wiki` prints
    it content-first instead."""
    pages = result.get("wiki_pages") or []
    if not pages:
        return f"(no wiki pages for topic {result.get('query', '')!r})"
    out = []
    for page in pages:
        path = page.get("path")
        if path:
            out.append(f"<!-- {path} -->")
        out.append(page.get("content", ""))
        if page.get("truncated"):
            out.append("_(wiki page truncated — read the file for the full text)_")
    return "\n\n".join(out).strip()


@topics_app.command("route", help="Resolve a query into approved topic context")
def cmd_topics_route(
    query: str = typer.Argument(..., help="Topic query"),
    repo: str | None = typer.Option(None, "--repo", help="Repository path"),
    wiki: bool = typer.Option(
        False, "--wiki",
        help="Print the topic's wiki markdown (content-first) instead of the "
             "JSON envelope, which buries the wiki below the refs list."),
) -> None:
    try:
        result = route_topic(_repo_path(repo), query)
    except TopicGraphError as exc:
        print(f"Topic route failed: {exc}")
        raise typer.Exit(1)
    if wiki:
        print(_render_topic_wiki(result))
        return
    print(json.dumps(result, indent=2, sort_keys=True))


@topics_app.command("audit", help="Show graph-wide validation issues grouped by code")
def cmd_topics_audit(
    repo: str | None = typer.Option(None, "--repo", help="Repository path"),
) -> None:
    from lib.topics.graph_io import load_authoritative_graph
    from lib.topics.validation import audit_graph
    from lib.topics.bulk_fix import AUTO_FIXABLE_CODES

    repo_path = _repo_path(repo)
    try:
        graph = load_authoritative_graph(repo_path)
    except TopicGraphError as exc:
        print(f"Audit failed: {exc}")
        raise typer.Exit(1)
    issues = audit_graph(graph, repo_path=repo_path)
    by_code: dict[str, list] = {}
    for issue in issues:
        by_code.setdefault(issue.code, []).append({
            "severity": issue.severity,
            "message": issue.message,
            "topic_ids": list(issue.topic_ids),
            "paths": list(issue.paths),
            "aliases": list(issue.aliases),
        })
    print(json.dumps({
        "error_count": sum(1 for i in issues if i.severity == "error"),
        "warning_count": sum(1 for i in issues if i.severity == "warning"),
        "auto_fixable_codes": sorted(AUTO_FIXABLE_CODES),
        "by_code": by_code,
    }, indent=2, sort_keys=True))


@topics_app.command("audit-fix", help="Bulk-fix auto-resolvable audit issues (dead refs, orphan edges)")
def cmd_topics_audit_fix(
    codes: list[str] = typer.Option(
        None, "--code",
        help="Issue codes to fix (repeatable). Defaults to all auto-fixable codes.",
    ),
    repo: str | None = typer.Option(None, "--repo", help="Repository path"),
) -> None:
    from lib.topics.apply import apply_diff
    from lib.topics.bulk_fix import AUTO_FIXABLE_CODES, compose_fix
    from lib.topics.diff import GraphDiff, compute_topic_delta
    from lib.topics.graph_io import load_authoritative_graph
    from lib.topics.snapshots import resolve_or_create_repo
    from lib.topics.validation import audit_graph

    repo_path = _repo_path(repo)
    requested = set(codes) if codes else set(AUTO_FIXABLE_CODES)
    skipped = sorted(requested - AUTO_FIXABLE_CODES)
    actionable = requested & AUTO_FIXABLE_CODES
    if not actionable:
        print(f"No auto-fixable codes selected. Auto-fixable: {sorted(AUTO_FIXABLE_CODES)}")
        if skipped:
            print(f"Skipped (manual): {skipped}")
        return
    graph = load_authoritative_graph(repo_path)
    issues = audit_graph(graph, repo_path=repo_path)
    fixes = compose_fix(graph, issues, codes_to_fix=actionable)
    if not fixes:
        print(json.dumps({"snapshot_ids": [], "fixed_counts": {c: 0 for c in actionable}, "skipped_codes": skipped}, indent=2))
        return
    repo_row = resolve_or_create_repo(str(repo_path))
    snapshot_ids: list[int] = []
    fixed_counts: dict[str, int] = {c: 0 for c in actionable}
    running = json.loads(json.dumps(graph))
    for topic_id, cleaned, before in fixes:
        prospective = json.loads(json.dumps(running))
        prospective.setdefault("topics", {})[topic_id] = cleaned
        delta = compute_topic_delta(topic_id_after=topic_id, kind="replace", before=before, after=cleaned)
        diff = GraphDiff(
            topic_deltas=(delta,), graph_warnings=(), introduced_errors=(),
            valid_strategies_by_topic={topic_id: ("replace",)},
            strategy="replace", target_topic_id=None,
            proposed_topic_id=topic_id, prospective_graph=prospective,
        )
        result = apply_diff(repo_row.id, diff, reason="bulk_fix")
        snapshot_ids.append(result.snapshot_id)
        fixed_counts["graph.dead_ref"] = fixed_counts.get("graph.dead_ref", 0) + len(delta.ref_removes)
        fixed_counts["graph.orphan_edge_target"] = fixed_counts.get("graph.orphan_edge_target", 0) + len(delta.edge_removes)
        running = prospective
    print(json.dumps({
        "snapshot_ids": snapshot_ids,
        "fixed_counts": fixed_counts,
        "skipped_codes": skipped,
    }, indent=2))
