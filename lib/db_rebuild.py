"""Rebuild the file-derived cache tables of the SQLite database.

Only the tables whose rows are derived from files are rebuilt:
  - pattern_docs, patterns_fts, pattern_embeddings  (patterns/*/SKILL.md)
  - tags, doc_tags                                   (config/tags.yaml)
  - repos, branches                                  (settings.repo_paths)

`pattern_docs`, `doc_tags`, `tags` and the FTS / vector tables are dropped
and re-created from their `db/schema.sql` definitions. `repos` and
`branches` are reconciled in place rather than dropped: sessions, features,
tasks and topic audits refer to `repos.id`, and a drop would renumber it.
Every other table — sessions, spans, features, tasks, defects, users, API
keys, `alembic_version` — is left untouched.
"""

import glob
import hashlib
import os
import sqlite3

import yaml

from lib.activity_log import get_activity_logger
from lib.settings import settings
from lib.orm.engine import get_connection, load_schema_sql

_log = get_activity_logger("rebuild")

#: Dropped and re-created from schema.sql. FTS shadow tables and the
#: `pattern_docs` delete trigger go with their owning table.
CACHE_TABLES = ("tags", "pattern_docs", "doc_tags", "pattern_embeddings",
                "patterns_fts")


def rebuild_from_files() -> dict:
    """Drop and re-create the cache tables, then re-populate them from files.

    Returns a stats dict the caller can render. Progress is also written
    to the activity log under feature=rebuild.
    """
    stats: dict = {
        "tags_seeded": None,
        "patterns_rebuilt": 0,
        "patterns_skipped": [],
        "repos": None,
    }

    conn = get_connection()
    try:
        _recreate_cache_tables(conn)
        _seed_tags(conn, stats)
        _rebuild_patterns(conn, stats)
        conn.commit()
    finally:
        conn.close()
    _rediscover_repos(stats)

    _log.write("rebuild_complete", **{
        "patterns_rebuilt": stats["patterns_rebuilt"],
        "tags_seeded": stats["tags_seeded"],
    })
    return stats


def _cache_table_ddl() -> list[str]:
    """schema.sql's CREATE statements for the cache tables and their indexes
    and triggers, in schema.sql's order."""
    reference = sqlite3.connect(":memory:")
    try:
        reference.executescript(load_schema_sql())
        placeholders = ", ".join("?" * len(CACHE_TABLES))
        return [r[0] for r in reference.execute(
            "SELECT sql FROM sqlite_master WHERE sql IS NOT NULL "
            f"AND tbl_name IN ({placeholders}) ORDER BY rowid",
            CACHE_TABLES).fetchall()]
    finally:
        reference.close()


def _recreate_cache_tables(conn):
    """Drop the cache tables and re-create them from schema.sql."""
    ddl = _cache_table_ddl()
    # Off for the drop: with enforcement on, dropping `pattern_docs` runs an
    # implicit DELETE that cascades through its foreign keys.
    conn.execute("PRAGMA foreign_keys = OFF")
    try:
        for table in reversed(CACHE_TABLES):
            conn.execute(f"DROP TABLE IF EXISTS {table}")
        for statement in ddl:
            conn.execute(statement)
        conn.commit()
    finally:
        conn.execute("PRAGMA foreign_keys = ON")
    _log.write("cache_tables_recreated", tables=len(CACHE_TABLES))


def _seed_tags(conn, stats):
    """Populate the tags table from config/tags.yaml."""
    tags_path = str(settings.tags_path)
    if not os.path.exists(tags_path):
        _log.write("tags_skipped", reason="no_tags_yaml", path=tags_path)
        return

    with open(tags_path) as f:
        tag_data = yaml.safe_load(f)

    count = 0
    for category, names in (tag_data or {}).items():
        if isinstance(names, list):
            for name in names:
                conn.execute(
                    "INSERT OR IGNORE INTO tags (name, category) VALUES (?, ?)",
                    (name, category),
                )
                count += 1
    conn.commit()
    stats["tags_seeded"] = count
    _log.write("tags_seeded", count=count)


def _parse_frontmatter(filepath):
    """Extract YAML frontmatter from a SKILL.md file."""
    try:
        with open(filepath, 'r') as f:
            content = f.read()
    except (FileNotFoundError, UnicodeDecodeError):
        return None

    if not content.startswith('---'):
        return None

    parts = content.split('---', 2)
    if len(parts) < 3:
        return None

    try:
        return yaml.safe_load(parts[1])
    except yaml.YAMLError:
        return None


def _content_hash(filepath):
    """Compute SHA-256 hash of file content."""
    try:
        with open(filepath, 'rb') as f:
            return hashlib.sha256(f.read()).hexdigest()
    except FileNotFoundError:
        return None


def _rebuild_patterns(conn, stats):
    """Scan patterns/*/SKILL.md and rebuild pattern_docs + doc_tags."""
    pattern_glob = os.path.join(str(settings.patterns_dir), '*/SKILL.md')
    skill_files = sorted(glob.glob(pattern_glob))

    count = 0
    for skill_path in skill_files:
        pattern_dir = os.path.dirname(skill_path)
        slug = os.path.basename(pattern_dir)

        if slug.startswith('_'):
            continue

        fm = _parse_frontmatter(skill_path)
        if not fm:
            stats["patterns_skipped"].append(slug)
            _log.write("pattern_skipped", slug=slug, reason="no_frontmatter")
            continue

        title = fm.get('title', slug.replace('-', ' ').title())
        c_hash = _content_hash(skill_path)
        rel_path = os.path.relpath(skill_path, str(settings.project_root))

        conn.execute(
            """INSERT INTO pattern_docs
                (slug, title, file_path, category, content_hash)
            VALUES (?, ?, ?, ?, ?)""",
            (slug, title, rel_path, 'procedure', c_hash),
        )

        count += 1

    conn.commit()
    stats["patterns_rebuilt"] = count
    _log.write("patterns_rebuilt", count=count, skipped=len(stats["patterns_skipped"]))


def _rediscover_repos(stats):
    """Re-run repo discovery to populate repos + branches tables."""
    try:
        from lib.sync.repo_discovery import scan_repos, register_repos
        repos = scan_repos()
        repo_stats = register_repos(repos)
        stats["repos"] = {
            "discovered": len(repos),
            "added": repo_stats["added"],
            "updated": repo_stats["updated"],
        }
        _log.write("repos_discovered", **stats["repos"])
    except Exception as exc:
        stats["repos"] = {"error": str(exc)}
        _log.write("repos_skipped", error=str(exc))
