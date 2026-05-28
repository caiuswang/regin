"""Rebuild the SQLite database from git-tracked files.

The DB is a derived cache — all authoritative data lives in:
  - patterns/*/SKILL.md          (procedure guides with YAML frontmatter)
  - .grit/rules.json             (GritQL rule index)
  - config/tags.yaml             (shared tag definitions)
  - config/settings.json         (shared settings)

Tables rebuilt: repos, branches, pattern_docs, tags, doc_tags.
Tables preserved (local-only): experiments, rule_triggers, users, audit_log.
"""

import glob
import hashlib
import os

import yaml

from lib.activity_log import get_activity_logger
from lib.settings import settings
from lib.orm.engine import get_connection, load_schema_sql

_log = get_activity_logger("rebuild")

# Tables that hold local-only runtime data and should survive a rebuild.
# In standalone mode, users and audit_log live in SQLite and must be preserved.
_LOCAL_TABLES = {'experiments', 'rule_triggers', 'users', 'audit_log', 'pattern_deployments'}


def rebuild_from_files(preserve_local: bool = True) -> dict:
    """Drop shared tables, re-create schema, and re-populate from files.

    Returns a stats dict the caller can render. Progress is also written
    to the activity log under feature=rebuild.
    """
    stats: dict = {
        "backed_up": {},
        "restored": {},
        "tags_seeded": None,
        "patterns_rebuilt": 0,
        "patterns_skipped": [],
        "repos": None,
    }

    conn = get_connection()
    try:
        local_backup = {}
        if preserve_local:
            local_backup = _backup_local_tables(conn, stats)

        _recreate_schema(conn)

        if local_backup:
            _restore_local_tables(conn, local_backup, stats)

        _seed_tags(conn, stats)
        _rebuild_patterns(conn, stats)
        _rediscover_repos(stats)

        conn.commit()
    finally:
        conn.close()

    _log.write("rebuild_complete", **{
        "patterns_rebuilt": stats["patterns_rebuilt"],
        "tags_seeded": stats["tags_seeded"],
    })
    return stats


def _backup_local_tables(conn, stats):
    """Save rows from local-only tables before schema reset."""
    backup = {}
    for table in _LOCAL_TABLES:
        try:
            rows = conn.execute(f"SELECT * FROM {table}").fetchall()
            if rows:
                cols = rows[0].keys()
                backup[table] = {'cols': list(cols), 'rows': [dict(r) for r in rows]}
                stats["backed_up"][table] = len(rows)
                _log.write("table_backed_up", table=table, rows=len(rows))
        except Exception:
            pass  # table may not exist yet
    return backup


def _restore_local_tables(conn, backup, stats):
    """Re-insert rows into local-only tables after schema reset."""
    for table, data in backup.items():
        cols = data['cols']
        rows = data['rows']
        if not rows:
            continue
        placeholders = ', '.join(['?'] * len(cols))
        col_names = ', '.join(cols)
        for row in rows:
            vals = [row.get(c) for c in cols]
            try:
                conn.execute(
                    f"INSERT OR IGNORE INTO {table} ({col_names}) VALUES ({placeholders})",
                    vals,
                )
            except Exception:
                pass  # skip rows that violate new schema constraints
        stats["restored"][table] = len(rows)
        _log.write("table_restored", table=table, rows=len(rows))


def _recreate_schema(conn):
    """Drop all tables and re-run schema.sql."""
    conn.execute("PRAGMA foreign_keys = OFF")
    tables = [r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name != 'sqlite_sequence'"
    ).fetchall()]
    for table in tables:
        conn.execute(f"DROP TABLE IF EXISTS {table}")
    conn.commit()
    conn.execute("PRAGMA foreign_keys = ON")

    schema_sql = load_schema_sql()
    conn.executescript(schema_sql)
    conn.commit()
    _log.write("schema_recreated", dropped=len(tables))


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
