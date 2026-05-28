"""User-managed prompt templates injectable into LLM/agent flows."""

from __future__ import annotations

from typing import Optional

from sqlalchemy import text
from sqlmodel import Column, Field, Integer, String, Text

from lib.orm.base import Base


class PromptTemplate(Base, table=True):
    __tablename__ = "prompt_templates"

    id: Optional[int] = Field(default=None, primary_key=True)
    slug: str = Field(sa_column=Column("slug", String, nullable=False, unique=True))
    label: str = Field(sa_column=Column("label", String, nullable=False))
    description: Optional[str] = Field(default=None, sa_column=Column("description", Text))
    body: str = Field(sa_column=Column("body", Text, nullable=False))
    # JSON array of provider ids the template is compatible with; "[]" = all providers.
    applies_to: str = Field(
        sa_column=Column("applies_to", Text, nullable=False, server_default=text("'[]'")),
    )
    # JSON array of provider ids that should auto-select this template.
    default_for_providers: str = Field(
        sa_column=Column("default_for_providers", Text, nullable=False, server_default=text("'[]'")),
    )
    builtin: int = Field(
        sa_column=Column("builtin", Integer, nullable=False, server_default=text("0")),
    )
    created_at: Optional[str] = Field(
        default=None,
        sa_column=Column("created_at", Text, nullable=False, server_default=text("(datetime('now'))")),
    )
    updated_at: Optional[str] = Field(
        default=None,
        sa_column=Column("updated_at", Text, nullable=False, server_default=text("(datetime('now'))")),
    )


__all__ = ["PromptTemplate"]
