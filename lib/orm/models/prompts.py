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
    # Row kind: "fragment" (an injectable snippet) or "skeleton" (a full
    # external-agent goal prompt with {{variable}} slots). Seeded skeleton rows
    # mirror a registered surface id in their slug.
    kind: str = Field(
        sa_column=Column("kind", String, nullable=False, server_default=text("'fragment'")),
    )
    # JSON array of {name, description, example, required} the body interpolates
    # — drives the editor's variable palette. "[]" for plain fragments.
    variables: str = Field(
        sa_column=Column("variables", Text, nullable=False, server_default=text("'[]'")),
    )
    # JSON array of provider ids the template is compatible with; "[]" = all providers.
    applies_to: str = Field(
        sa_column=Column("applies_to", Text, nullable=False, server_default=text("'[]'")),
    )
    # JSON array of provider ids that should auto-select this template.
    default_for_providers: str = Field(
        sa_column=Column("default_for_providers", Text, nullable=False, server_default=text("'[]'")),
    )
    # For skeleton rows: JSON array of custom session-tag slugs a run spawned
    # from this surface self-applies (as source='auto'). The editable override
    # of `PromptSurface.tags`; read at ingest by `_stamp_llm_stage_origins`.
    # Mirror this column in db/schema.sql + a new alembic revision.
    tags: str = Field(
        sa_column=Column("tags", Text, nullable=False, server_default=text("'[]'")),
    )
    # For skeleton rows: the external agent this goal-prompt is *bound* to — a
    # key in `settings.topic_proposal_external_agents`. NULL = no binding, so the
    # dispatch falls back to the surface's default agent (never dropped — see the
    # NULL-provider gotcha). Ignored for fragments.
    agent: Optional[str] = Field(default=None, sa_column=Column("agent", String))
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
