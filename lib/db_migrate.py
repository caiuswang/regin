"""Alembic-driven schema migration for the primary SQLite DB.

regin's baseline schema lives in `db/schema.sql` (applied by `regin init`);
alembic revision `0001` is a no-op anchor and `0002+` are incremental ALTERs
layered on that baseline. This module wires alembic into the lifecycle so the
version files are the single source for post-baseline schema changes — nothing
is hand-maintained a second time:

- `stamp_head()` marks a freshly built (schema.sql == head) DB as current;
  `regin init` calls it so every new DB is versioned.
- `run_migrate()` backs `regin migrate`: it upgrades a versioned DB to head.
  An unversioned DB is stamped only when it already carries every table and
  column `db/schema.sql` declares; otherwise it is refused with the missing
  list, because stamping a DB that is behind head would skip the revisions
  that bring it there.

A new schema change is therefore one alembic revision plus the matching
`db/schema.sql` edit.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
_ALEMBIC_INI = _PROJECT_ROOT / "alembic.ini"


def _config():
    from alembic.config import Config
    # An absolute ini path makes `%(here)s/alembic` (script_location) resolve
    # to the repo regardless of the caller's cwd; env.py picks the DB URL from
    # the live `lib.orm.engine.DB_PATH`, so a monkeypatched test DB works too.
    return Config(str(_ALEMBIC_INI))


def _is_stamped() -> bool:
    """True if the primary DB is enrolled in the revision chain (carries an
    `alembic_version` row). Reads `DB_PATH` live so tests that monkeypatch it
    resolve to the same file alembic will."""
    from lib.orm.engine import DB_PATH
    conn = sqlite3.connect(DB_PATH)
    try:
        has_table = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' "
            "AND name='alembic_version'"
        ).fetchone()
        if not has_table:
            return False
        return bool(
            conn.execute("SELECT 1 FROM alembic_version LIMIT 1").fetchone()
        )
    finally:
        conn.close()


def stamp_head() -> None:
    """Record the primary DB as being at the head revision without running any
    migration. Correct only right after the DB was built from `db/schema.sql`,
    which is kept equal to the alembic head."""
    from alembic import command
    command.stamp(_config(), "head")


class IncompleteUnstampedDB(Exception):
    """An unstamped DB lacks part of `db/schema.sql`, so it cannot be stamped."""

    def __init__(self, missing: list[str]):
        super().__init__(", ".join(missing))
        self.missing = missing


def _table_columns(conn: sqlite3.Connection) -> dict[str, list[str]]:
    rows = conn.execute(
        "SELECT name, sql FROM sqlite_master WHERE type='table' "
        "AND name NOT LIKE 'sqlite_%'").fetchall()
    virtual = [n for n, sql in rows
               if (sql or "").upper().startswith("CREATE VIRTUAL")]
    # FTS shadow tables come and go with their virtual table.
    tables = [n for n, _ in rows
              if not any(n.startswith(f"{v}_") for v in virtual)]
    return {t: [r[1] for r in conn.execute(
        f"PRAGMA table_info('{t}')").fetchall()] for t in tables}


def missing_schema_objects() -> list[str]:
    """Every table and column `db/schema.sql` declares that the primary DB
    lacks, as `table <t>` / `column <t>.<c>`; empty when nothing is missing."""
    from lib.orm.engine import DB_PATH, load_schema_sql
    reference = sqlite3.connect(":memory:")
    try:
        reference.executescript(load_schema_sql())
        expected = _table_columns(reference)
    finally:
        reference.close()
    conn = sqlite3.connect(DB_PATH)
    try:
        actual = _table_columns(conn)
    finally:
        conn.close()
    missing: list[str] = []
    for table, columns in expected.items():
        if table not in actual:
            missing.append(f"table {table}")
            continue
        missing.extend(f"column {table}.{c}" for c in columns
                       if c not in actual[table])
    return missing


def run_migrate() -> str:
    """Bring the primary DB up to head; return the action taken.

    A DB already in the revision chain is upgraded — applying any version
    files newer than its stamp. A DB with no stamp is stamped at head only
    when it already holds every table and column of `db/schema.sql`; one that
    lacks any raises `IncompleteUnstampedDB` without writing to the DB.
    """
    from alembic import command
    cfg = _config()
    if _is_stamped():
        command.upgrade(cfg, "head")
        return "upgraded"
    missing = missing_schema_objects()
    if missing:
        raise IncompleteUnstampedDB(missing)
    command.stamp(cfg, "head")
    return "stamped"
