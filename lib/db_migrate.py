"""Alembic-driven schema migration for the primary SQLite DB.

regin's baseline schema lives in `db/schema.sql` (applied by `regin init`);
alembic revision `0001` is a no-op anchor and `0002+` are incremental ALTERs
layered on that baseline. This module wires alembic into the lifecycle so the
version files are the single source for post-baseline schema changes — nothing
is hand-maintained a second time:

- `stamp_head()` marks a freshly built (schema.sql == head) DB as current;
  `regin init` / `regin rebuild` call it so every new DB is versioned.
- `run_migrate()` backs `regin migrate`: it upgrades a versioned DB to head,
  or one-time enrolls a pre-wiring DB (built from schema.sql, already at head)
  into the revision chain.

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


def run_migrate() -> str:
    """Bring the primary DB up to head; return the action taken.

    A DB already in the revision chain is upgraded — applying any version
    files newer than its stamp. A DB with no `alembic_version` predates the
    alembic wiring: it was built from `db/schema.sql` (== head) and kept
    current by the now-retired boot self-heal, so its shape *is* head. It is
    stamped into the chain rather than replaying baseline ALTERs that would
    collide with columns schema.sql already created. Every DB created after
    this change is stamped by `regin init`, so it never reaches this branch
    behind head.
    """
    from alembic import command
    cfg = _config()
    if _is_stamped():
        command.upgrade(cfg, "head")
        return "upgraded"
    command.stamp(cfg, "head")
    return "stamped"
