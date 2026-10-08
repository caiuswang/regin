"""A project's ordered step stages — which steps a feature can open."""

from __future__ import annotations

from typing import Optional

from sqlalchemy import Index, UniqueConstraint, text
from sqlmodel import Column, Field, Integer, String, Text

from lib.orm.base import Base


class ProjectStage(Base, table=True):
    """One step a feature can open. `project_id IS NULL` is the global default
    list, which a project reads until its first write copies it."""

    __tablename__ = "project_stages"
    __table_args__ = (
        UniqueConstraint("project_id", "key",
                         name="uq_project_stages_project_key"),
        # UNIQUE (project_id, key) cannot hold for the global rows: SQLite
        # treats every NULL project_id as distinct.
        Index("uq_project_stages_global_key", "key", unique=True,
              sqlite_where=text("project_id IS NULL")),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    project_id: Optional[int] = Field(
        default=None, sa_column=Column("project_id", Integer))
    key: str = Field(sa_column=Column("key", Text, nullable=False))
    label: str = Field(sa_column=Column("label", Text, nullable=False))
    role: str = Field(sa_column=Column("role", Text, nullable=False))
    position: int = Field(
        default=0, sa_column=Column("position", Integer, nullable=False,
                                    server_default=text("0")))
    prompt_slug: Optional[str] = Field(
        default=None, sa_column=Column("prompt_slug", Text))
    builtin: int = Field(
        default=0, sa_column=Column("builtin", Integer, nullable=False,
                                    server_default=text("0")))
    archived_at: Optional[str] = Field(
        default=None, sa_column=Column("archived_at", Text))
    created_at: Optional[str] = Field(
        default=None,
        sa_column=Column("created_at", String, nullable=False,
                         server_default=text("(datetime('now'))")))
    updated_at: Optional[str] = Field(
        default=None,
        sa_column=Column("updated_at", String, nullable=False,
                         server_default=text("(datetime('now'))")))


__all__ = ["ProjectStage"]
