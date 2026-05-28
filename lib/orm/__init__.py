"""SQLAlchemy / SQLModel layer.

`lib/db.py` is still the current home of the raw sqlite3 connection
helpers (`get_connection`, `init_db`, `db_exists`).
This package adds the SQLAlchemy `engine` + `SessionLocal` factory used
by SQLModel-typed code written in Phase B.2+. Both layers coexist
during the migration; the raw helpers get decommissioned at the end of
Phase B (the `lib/db.py` → `lib/db/` rename).

Typical usage in new code:

    from lib.orm import SessionLocal
    from lib.orm.models import User

    with SessionLocal() as session:
        user = session.get(User, user_id)
        ...

All SQLModel models register on `lib.orm.base.Base.metadata` so Alembic
can pick them up for autogenerate.
"""

from __future__ import annotations

from lib.orm.engine import (
    AuthSessionLocal,
    SessionLocal,
    dispose_engine,
    get_auth_engine,
    get_engine,
)

# Importing `models` eagerly registers every SQLModel class on
# `lib.orm.base.metadata`, which is the single MetaData Alembic targets.
from lib.orm import models  # noqa: F401

__all__ = [
    "SessionLocal", "AuthSessionLocal",
    "get_engine", "get_auth_engine",
    "dispose_engine",
]
