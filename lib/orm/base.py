"""Shared SQLModel base class.

SQLModel unifies SQLAlchemy ORM models with Pydantic validation models
in a single class hierarchy. Every table-backed class in
`lib/orm/models/*` should subclass `Base` so that Alembic's autogenerate
sees it on the shared metadata.

Pydantic-only request/response schemas (i.e. `table=False`) can also
subclass this, or use `pydantic.BaseModel` directly — the `metadata`
attribute is only populated for table-backed subclasses.
"""

from __future__ import annotations

from sqlmodel import SQLModel


class Base(SQLModel):
    """Project-wide SQLModel base. All tables register on
    `Base.metadata` — this is the single MetaData Alembic targets."""


metadata = Base.metadata


__all__ = ["Base", "metadata"]
