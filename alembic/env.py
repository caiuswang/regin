"""Alembic migration environment.

Wires the engine + target metadata from `lib.orm` so `alembic revision
--autogenerate` sees the SQLModel classes registered under
`lib/orm/models/`.

The URL comes from `lib.orm.engine._resolve_primary_url()` — always the
project-local SQLite file, matching the raw-sqlite layer in `lib/db.py`.
Override in ad-hoc runs with `-x url=<override>` or the ALEMBIC_URL env.
"""

from __future__ import annotations

import os
from logging.config import fileConfig

from sqlalchemy import create_engine
from sqlalchemy.pool import NullPool

from alembic import context

from lib.orm.base import metadata as target_metadata
from lib.orm.engine import _resolve_primary_url


# Alembic Config object — hands out values from alembic.ini.
config = context.config

# Route Alembic's own logging through the .ini config when present.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)


def _effective_url() -> str:
    """Pick the URL from (in order): CLI `-x url=`, env ALEMBIC_URL,
    then `lib.orm`'s primary URL."""
    xargs = context.get_x_argument(as_dictionary=True)
    if xargs.get("url"):
        return xargs["url"]
    env = os.environ.get("ALEMBIC_URL")
    if env:
        return env
    return _resolve_primary_url()


def run_migrations_offline() -> None:
    """Generate SQL without connecting to a DB. Useful for review."""
    context.configure(
        url=_effective_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=True,  # SQLite ALTER support.
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Apply migrations against a live connection. Uses NullPool so the
    Alembic run doesn't leave stale pooled connections around."""
    url = _effective_url()
    connect_args: dict = {}
    if url.startswith("sqlite"):
        connect_args["check_same_thread"] = False
    engine = create_engine(url, poolclass=NullPool, connect_args=connect_args, future=True)
    with engine.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            render_as_batch=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
