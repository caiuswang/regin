"""Repository + branch tracking tables."""

from __future__ import annotations

from typing import Optional

from sqlalchemy import text
from sqlmodel import Column, Field, Integer, String, Text

from lib.orm.base import Base


class Repo(Base, table=True):
    __tablename__ = "repos"

    id: Optional[int] = Field(default=None, primary_key=True)
    name: str = Field(sa_column=Column("name", String, nullable=False, unique=True))
    path: str = Field(sa_column=Column("path", String, nullable=False))
    description: Optional[str] = Field(default=None, sa_column=Column("description", Text))
    is_active: int = Field(
        sa_column=Column("is_active", Integer, nullable=False,
                         server_default=text("1")),
    )
    default_branch: str = Field(
        sa_column=Column("default_branch", String, nullable=False,
                         server_default=text("'main'")),
    )
    output_language: Optional[str] = Field(
        default=None, sa_column=Column("output_language", Text))
    created_at: Optional[str] = Field(
        default=None,
        sa_column=Column("created_at", Text, nullable=False,
                         server_default=text("(datetime('now'))")),
    )
    updated_at: Optional[str] = Field(
        default=None,
        sa_column=Column("updated_at", Text, nullable=False,
                         server_default=text("(datetime('now'))")),
    )


class Branch(Base, table=True):
    __tablename__ = "branches"

    id: Optional[int] = Field(default=None, primary_key=True)
    repo_id: int = Field(sa_column=Column("repo_id", Integer, nullable=False))
    name: str = Field(sa_column=Column("name", String, nullable=False))
    is_tracked: int = Field(
        sa_column=Column("is_tracked", Integer, nullable=False,
                         server_default=text("1")),
    )
    created_at: Optional[str] = Field(
        default=None,
        sa_column=Column("created_at", Text, nullable=False,
                         server_default=text("(datetime('now'))")),
    )


__all__ = ["Repo", "Branch"]


class Workspace(Base, table=True):
    """A root directory grouping the registered repos beneath it.

    Membership is not stored: a repo belongs to a workspace when its path is
    the root or lies under it, so a repo registered later joins on its own.
    """

    __tablename__ = "workspaces"
    __table_args__ = {"sqlite_autoincrement": True}

    id: Optional[int] = Field(default=None, primary_key=True)
    name: str = Field(sa_column=Column("name", Text, nullable=False, unique=True))
    root_path: str = Field(
        sa_column=Column("root_path", Text, nullable=False, unique=True))
    created_at: Optional[str] = Field(
        default=None,
        sa_column=Column("created_at", Text, nullable=False,
                         server_default=text("(datetime('now'))")),
    )
