"""User + audit-log SQLModel tables.

Column shapes match `db/schema.sql` so existing SQLite databases work
with these models as-is (no ALTER needed). `created_at` / `last_login`
are kept as `str` rather than `datetime` because the SQLite column type
is TEXT with a `datetime('now')` default — the raw-sqlite layer has
always returned ISO-8601 strings, and tests pin that shape. A future
migration can promote these to typed `datetime` once the UI is ready.
"""

from __future__ import annotations

from typing import Optional

from sqlalchemy import text
from sqlmodel import Column, Field, Integer, SQLModel, String, Text

from lib.orm.base import Base


class User(Base, table=True):
    """Row in the `users` table. Matches schema.sql verbatim."""

    __tablename__ = "users"

    id: Optional[int] = Field(default=None, primary_key=True)
    username: str = Field(sa_column=Column("username", String, nullable=False, unique=True))
    display_name: str = Field(sa_column=Column("display_name", String, nullable=False))
    email: Optional[str] = Field(default=None, sa_column=Column("email", String))
    password_hash: str = Field(sa_column=Column("password_hash", String, nullable=False))
    role: str = Field(
        sa_column=Column("role", String, nullable=False,
                         server_default=text("'editor'"))
    )
    created_at: Optional[str] = Field(
        default=None,
        sa_column=Column("created_at", Text, nullable=False,
                         server_default=text("(datetime('now'))")),
    )
    last_login: Optional[str] = Field(default=None,
                                      sa_column=Column("last_login", Text))


class AuditLog(Base, table=True):
    """Row in the `audit_log` table. Matches schema.sql verbatim."""

    __tablename__ = "audit_log"

    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: Optional[int] = Field(
        default=None,
        sa_column=Column("user_id", Integer),
    )
    username: str = Field(sa_column=Column("username", String, nullable=False))
    action: str = Field(sa_column=Column("action", String, nullable=False))
    target: str = Field(sa_column=Column("target", String, nullable=False))
    detail: Optional[str] = Field(default=None, sa_column=Column("detail", Text))
    created_at: Optional[str] = Field(
        default=None,
        sa_column=Column("created_at", Text, nullable=False,
                         server_default=text("(datetime('now'))")),
    )


# Eager-reference so `lib.orm.base.metadata` picks them up if a caller
# imports `lib.orm` without drilling into `.models`.
_ = SQLModel  # silence "imported but unused" for the base re-export.


__all__ = ["User", "AuditLog"]
