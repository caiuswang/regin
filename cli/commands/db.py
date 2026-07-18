"""init / rebuild / tags / search — database-backed commands."""

from __future__ import annotations

import json
import os
import re
import shutil
import sqlite3
from typing import Optional

import typer

from cli.deps import require_db
import lib.orm.engine as _engine
from lib.orm.engine import init_db as _init_db, db_exists
from lib.providers import get_active_provider
from lib.settings import settings

_PROVIDER = get_active_provider()
CLAUDE_SETTINGS_PATH = str(_PROVIDER.hook_settings_path())
HOOK_MANAGER_CONFIG_PATH = str(_PROVIDER.hook_manager_config_path())
_HOOK_MANAGER_CMD_RE = re.compile(r'(^|\s)-m\s+hook_manager(?:\s|$)')


def _remove_path(path: str) -> bool:
    """Delete a file or directory if it exists."""
    if os.path.isdir(path):
        shutil.rmtree(path)
        return True
    if os.path.isfile(path):
        os.remove(path)
        return True
    return False


def _prune_empty_parents(path: str, depth: int = 2) -> None:
    """Best-effort cleanup of empty parent dirs like `.claude/skills/`."""
    current = path
    for _ in range(depth):
        if not current or not os.path.isdir(current):
            return
        try:
            os.rmdir(current)
        except OSError:
            return
        current = os.path.dirname(current)


def _load_recorded_deployment_paths() -> list[str]:
    """Read deployment directories from the current SQLite DB, if any."""
    if not os.path.exists(_engine.DB_PATH):
        return []

    try:
        conn = sqlite3.connect(_engine.DB_PATH)
    except sqlite3.Error:
        return []

    try:
        has_table = conn.execute(
            "SELECT 1 FROM sqlite_master "
            "WHERE type='table' AND name='pattern_deployments'"
        ).fetchone()
        if not has_table:
            return []
        rows = conn.execute(
            "SELECT deployed_path FROM pattern_deployments "
            "WHERE deployed_path IS NOT NULL AND deployed_path != ''"
        ).fetchall()
        return [row[0] for row in rows if row[0]]
    except sqlite3.Error:
        return []
    finally:
        conn.close()


def _clear_recorded_deployments() -> int:
    """Remove tracked deployment directories before wiping the DB."""
    removed = 0
    seen: set[str] = set()
    for deployed_path in _load_recorded_deployment_paths():
        if deployed_path in seen:
            continue
        seen.add(deployed_path)
        if _remove_path(deployed_path):
            removed += 1
            _prune_empty_parents(os.path.dirname(deployed_path))
    return removed


def _clear_jwt_secret() -> bool:
    """Delete the cached JWT secret so old tokens stop working."""
    from lib import auth
    return _remove_path(auth._SECRET_PATH)


def _is_hook_manager_command(command: str) -> bool:
    return bool(isinstance(command, str) and _HOOK_MANAGER_CMD_RE.search(command))


def _clear_hook_manager_settings() -> int:
    """Remove hook_manager dispatcher commands from ~/.claude/settings.json."""
    try:
        with open(CLAUDE_SETTINGS_PATH, 'r') as f:
            settings_json = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return 0

    hooks = settings_json.get('hooks')
    if not isinstance(hooks, dict):
        return 0

    removed = 0
    for event_name in list(hooks.keys()):
        entries = hooks[event_name]
        if not isinstance(entries, list):
            continue

        filtered_entries = []
        for entry in entries:
            entry_hooks = entry.get('hooks', [])
            if not isinstance(entry_hooks, list):
                filtered_entries.append(entry)
                continue

            kept_hooks = []
            for hook in entry_hooks:
                if _is_hook_manager_command(hook.get('command', '')):
                    removed += 1
                else:
                    kept_hooks.append(hook)

            if kept_hooks:
                next_entry = dict(entry)
                next_entry['hooks'] = kept_hooks
                filtered_entries.append(next_entry)

        if filtered_entries:
            hooks[event_name] = filtered_entries
        else:
            del hooks[event_name]

    if removed == 0:
        return 0

    if not hooks:
        settings_json.pop('hooks', None)

    with open(CLAUDE_SETTINGS_PATH, 'w') as f:
        json.dump(settings_json, f, indent=2)
    return removed


def _clear_hook_manager_state() -> tuple[bool, int]:
    """Delete hook_manager toggle state and routed settings entries."""
    config_removed = _remove_path(HOOK_MANAGER_CONFIG_PATH)
    routed_entries_removed = _clear_hook_manager_settings()
    return config_removed, routed_entries_removed


def _reset_shared_auth_tables() -> None:
    """Drop and recreate the shared-mode auth tables."""
    from lib.mysql_db import get_mysql_connection, init_mysql

    conn = get_mysql_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("DROP TABLE IF EXISTS audit_log")
            cur.execute("DROP TABLE IF EXISTS users")
        conn.commit()
    finally:
        conn.close()

    init_mysql()


def _remove_primary_db() -> None:
    """Delete the SQLite DB and sidecar files after disposing pooled handles."""
    from lib.orm import dispose_engine

    dispose_engine()
    for suffix in ("", "-wal", "-shm", "-journal"):
        # Call-time lookup: a module-level `from … import DB_PATH` would bind
        # the real path before `tmp_db` patches it, pointing this deletion at
        # the developer's production database.
        _remove_path(_engine.DB_PATH + suffix)


def _force_reset_state() -> tuple[int, bool, bool, int]:
    """Clear persisted local state before reinitializing the DB."""
    removed_deployments = _clear_recorded_deployments()
    _remove_primary_db()
    if settings.mode == "shared":
        _reset_shared_auth_tables()
    cleared_jwt_secret = _clear_jwt_secret()
    cleared_hook_config, removed_hook_routes = _clear_hook_manager_state()
    return (
        removed_deployments,
        cleared_jwt_secret,
        cleared_hook_config,
        removed_hook_routes,
    )


# ── init ──────────────────────────────────────────────────────

def cmd_init(
    force: bool = typer.Option(
        False, "--force",
        help="Reinitialize from scratch and clear local state",
    ),
) -> None:
    if db_exists() and not force:
        print(f"Database already exists at {_engine.DB_PATH}")
        print("Use --force to reinitialize.")
        return

    removed_deployments = 0
    cleared_jwt_secret = False
    cleared_hook_config = False
    removed_hook_routes = 0
    if force:
        (
            removed_deployments,
            cleared_jwt_secret,
            cleared_hook_config,
            removed_hook_routes,
        ) = _force_reset_state()

    _init_db()

    # The fresh DB is built from schema.sql, which is kept equal to the alembic
    # head — enroll it in the revision chain so `regin migrate` upgrades (not
    # replays) it later.
    from lib.db_migrate import stamp_head
    stamp_head()

    # Seed a builtin skeleton row for every registered external-agent prompt
    # surface (bodies live in the Python registry, not schema.sql). Idempotent.
    from lib.prompt_templates import seed_builtin_skeletons
    seed_builtin_skeletons()

    # `_index/` holds auto-generated tag/repo indexes. Per-procedure
    # directories are created lazily by `sync` when a guide is written.
    os.makedirs(os.path.join(str(settings.patterns_dir), '_index'), exist_ok=True)

    if force:
        print("Cleared existing local state before initialization.")
        if removed_deployments:
            print(f"Removed {removed_deployments} deployed skill director"
                  f"{'y' if removed_deployments == 1 else 'ies'}.")
        if cleared_jwt_secret:
            print("Removed JWT signing secret; existing login tokens are now invalid.")
        if cleared_hook_config:
            print("Removed hook-manager toggle state.")
        if removed_hook_routes:
            print(f"Removed {removed_hook_routes} hook_manager routing entr"
                  f"{'y' if removed_hook_routes == 1 else 'ies'} from Claude settings.")

    print(f"Initialized database at {_engine.DB_PATH}")
    print(f"Pattern directory ready at {settings.patterns_dir}")


# ── rebuild ───────────────────────────────────────────────────

def _render_rebuild_stats(stats: dict) -> None:
    for table, n in stats["backed_up"].items():
        typer.echo(f"  backed up {n} rows from {table}")
    typer.echo("  schema re-created")
    for table, n in stats["restored"].items():
        typer.echo(f"  restored {n} rows to {table}")
    if stats["tags_seeded"] is None:
        typer.echo("  no config/tags.yaml found — leaving tags empty")
    else:
        typer.echo(f"  seeded {stats['tags_seeded']} tags from tags.yaml")
    for slug in stats["patterns_skipped"]:
        typer.echo(f"  skipping {slug}: no frontmatter")
    typer.echo(f"  rebuilt {stats['patterns_rebuilt']} pattern docs from SKILL.md files")
    _render_repo_stats(stats.get("repos"))


def _render_repo_stats(repos: dict | None) -> None:
    if not repos:
        return
    if "error" in repos:
        typer.echo(f"  repo discovery skipped: {repos['error']}")
        return
    typer.echo(
        f"  discovered {repos['discovered']} repos: "
        f"{repos['added']} added, {repos['updated']} updated"
    )


def cmd_rebuild(
    clean: bool = typer.Option(
        False, "--clean",
        help="Drop local-only tables too (experiments, triggers, users)",
    ),
) -> None:
    from lib.db_rebuild import rebuild_from_files
    typer.echo("Rebuilding database from files...")
    stats = rebuild_from_files(preserve_local=not clean)
    _render_rebuild_stats(stats)
    # Rebuild re-creates the schema from schema.sql (== alembic head); re-stamp
    # so the rebuilt DB stays enrolled in the revision chain.
    from lib.db_migrate import stamp_head
    stamp_head()
    typer.echo("Done.")


# ── migrate ───────────────────────────────────────────────────

def cmd_migrate() -> None:
    """Bring an existing DB up to the current schema via alembic.

    Fresh installs are already at head (`regin init` stamps them); this
    applies any alembic version files newer than an existing DB's stamp, or
    one-time enrolls a pre-wiring DB into the revision chain. Safe to run
    repeatedly; an up-to-date DB is a no-op.
    """
    if not db_exists():
        typer.echo(f"No database at {_engine.DB_PATH}. Run `regin init` first.")
        raise typer.Exit(1)
    from lib.db_migrate import run_migrate
    action = run_migrate()
    if action == "upgraded":
        typer.echo(f"Applied pending migrations to {_engine.DB_PATH}")
    else:
        typer.echo(
            f"Enrolled existing DB into the alembic chain (already at head): {_engine.DB_PATH}"
        )


# ── tags ──────────────────────────────────────────────────────

@require_db
def cmd_tags(
    add: Optional[str] = typer.Option(None, "--add", help="Add a new tag"),
    category: Optional[str] = typer.Option(None, "--category", help="Tag category (layer|tech|domain|concept)"),
    description: Optional[str] = typer.Option(None, "--description", help="Tag description"),
    index: bool = typer.Option(False, "--index", help="Generate tag/repo index files"),
) -> None:
    from lib.tags.tag_manager import list_tags, add_tag
    from lib.tags.tag_index import generate_tag_index, generate_repo_index

    if index:
        tag_path = generate_tag_index()
        repo_path = generate_repo_index()
        print(f"Generated tag index: {tag_path}")
        print(f"Generated repo index: {repo_path}")
        return

    if add:
        if not category:
            print("--category is required when adding a tag.")
            raise typer.Exit(1)
        add_tag(add, category, description)
        print(f"Added tag '{add}' (category={category})")
    else:
        tags = list_tags(category=category)
        current_cat = None
        for tag in tags:
            if tag['category'] != current_cat:
                current_cat = tag['category']
                print(f"\n[{current_cat}]")
            count = tag.get('doc_count', 0)
            print(f"  {tag['name']:25s}  ({count} patterns)")


# ── search ────────────────────────────────────────────────────

@require_db
def cmd_search(
    query: str = typer.Argument(..., help="Search query"),
    tag: Optional[str] = typer.Option(None, "--tag", help="Filter by tag"),
    category: Optional[str] = typer.Option(None, "--category", help="Filter by category"),
    dense: bool = typer.Option(
        False, "--dense",
        help="EXPERIMENTAL. Route through the SkillRouter dense pipeline instead of substring search",
    ),
    top_k: int = typer.Option(5, "--top-k", help="(dense only) Number of results to return"),
    no_rerank: bool = typer.Option(False, "--no-rerank", help="(dense only) Skip the cross-encoder rerank stage"),
) -> None:
    import sys
    if dense:
        if tag or category:
            print("--tag/--category are not applied to dense search; they are ignored.",
                  file=sys.stderr)
        from lib.patterns import pattern_router
        from lib.skills import skill_router
        try:
            results = pattern_router.route(query, top_k=top_k, rerank=not no_rerank)
        except skill_router.DependencyError as exc:
            print(str(exc), file=sys.stderr)
            raise typer.Exit(2)
        if not results:
            print("No patterns indexed — run `regin pattern embed` first.")
            return
        print(f"Found {len(results)} pattern(s) [dense, {results[0]['score_kind']}]:\n")
        for r in results:
            print(f"  {r['score']:+.4f}  [{r['category']}] {r['title']}")
            print(f"    slug: {r['slug']}")
            print(f"    file: {r['file_path']}")
            print()
        return

    from lib.search import search_patterns
    results = search_patterns(query, tag=tag, category=category)
    if not results:
        print("No patterns found.")
        return

    print(f"Found {len(results)} pattern(s):\n")
    for r in results:
        tags_str = ', '.join(r.get('tags', []))
        print(f"  [{r['category']}] {r['title']}")
        print(f"    tags: {tags_str}")
        print(f"    file: {r['file_path']}")
        print()


def register(app: typer.Typer) -> None:
    app.command("init", help="Initialize database and directories")(cmd_init)
    app.command("rebuild", help="Rebuild DB from git-tracked files")(cmd_rebuild)
    app.command(
        "migrate",
        help="Reconcile an existing DB with the current code's schema",
    )(cmd_migrate)
    app.command("tags", help="Manage tags")(cmd_tags)
    app.command("search", help="Search patterns")(cmd_search)
