"""Pattern + tag + deployment tables."""

from __future__ import annotations

from typing import Optional

from sqlalchemy import LargeBinary, text
from sqlmodel import Column, Field, Integer, String, Text

from lib.orm.base import Base


class PatternDoc(Base, table=True):
    __tablename__ = "pattern_docs"

    id: Optional[int] = Field(default=None, primary_key=True)
    slug: str = Field(sa_column=Column("slug", String, nullable=False, unique=True))
    title: str = Field(sa_column=Column("title", String, nullable=False))
    file_path: str = Field(sa_column=Column("file_path", String, nullable=False))
    category: str = Field(sa_column=Column("category", String, nullable=False))
    content_hash: Optional[str] = Field(default=None, sa_column=Column("content_hash", Text))
    # Mirrors the SKILL.md frontmatter `description` field. Stored in the DB
    # so the patterns list endpoint doesn't have to open every SKILL.md to
    # render descriptions. Kept in sync by `_sync_doc_from_frontmatter` on
    # content save and by `api_save_pattern_description`.
    description: Optional[str] = Field(default=None, sa_column=Column("description", Text))
    # 'pattern' for user-authored pattern docs (the default), 'wiki' for
    # per-topic approved-wiki pages indexed by lib.patterns.wiki_indexer.
    source_kind: str = Field(
        default="pattern",
        sa_column=Column("source_kind", String, nullable=False,
                         server_default=text("'pattern'")),
    )
    # Set for wiki rows so route(repo=...) can scope results. Null for
    # patterns (which are global).
    repo_id: Optional[int] = Field(default=None, sa_column=Column("repo_id", Integer))
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


class Tag(Base, table=True):
    __tablename__ = "tags"

    id: Optional[int] = Field(default=None, primary_key=True)
    name: str = Field(sa_column=Column("name", String, nullable=False, unique=True))
    category: str = Field(sa_column=Column("category", String, nullable=False))
    description: Optional[str] = Field(default=None, sa_column=Column("description", Text))


class DocTag(Base, table=True):
    """Association row: pattern_docs ↔ tags (many-to-many)."""

    __tablename__ = "doc_tags"

    doc_id: int = Field(
        sa_column=Column("doc_id", Integer, primary_key=True, nullable=False),
    )
    tag_id: int = Field(
        sa_column=Column("tag_id", Integer, primary_key=True, nullable=False),
    )


class PatternDeployment(Base, table=True):
    __tablename__ = "pattern_deployments"

    id: Optional[int] = Field(default=None, primary_key=True)
    pattern_slug: str = Field(sa_column=Column("pattern_slug", String, nullable=False))
    scope: str = Field(sa_column=Column("scope", String, nullable=False))
    project_id: Optional[int] = Field(default=None,
                                      sa_column=Column("project_id", Integer))
    # Provider id (claude/codex/kimi/generic) that owns this deployment row.
    # NULL means "active provider at the time of migration" for backward
    # compatibility; new rows always set this explicitly.
    provider: Optional[str] = Field(default=None,
                                    sa_column=Column("provider", String))
    deployed_path: str = Field(sa_column=Column("deployed_path", String, nullable=False))
    deployed_at: Optional[str] = Field(
        default=None,
        sa_column=Column("deployed_at", Text, nullable=False,
                         server_default=text("(datetime('now'))")),
    )
    deployed_by: Optional[int] = Field(default=None,
                                       sa_column=Column("deployed_by", Integer))


class PatternEmbedding(Base, table=True):
    """SkillRouter-style dense vector for a pattern body (experimental)."""

    __tablename__ = "pattern_embeddings"

    pattern_id: int = Field(
        sa_column=Column("pattern_id", Integer, primary_key=True, nullable=False),
    )
    content_hash: str = Field(sa_column=Column("content_hash", String, nullable=False))
    model_id: str = Field(sa_column=Column("model_id", String, nullable=False))
    dim: int = Field(sa_column=Column("dim", Integer, nullable=False))
    vector: bytes = Field(sa_column=Column("vector", LargeBinary, nullable=False))
    updated_at: Optional[str] = Field(
        default=None,
        sa_column=Column("updated_at", Text, nullable=False,
                         server_default=text("(datetime('now'))")),
    )


__all__ = ["PatternDoc", "Tag", "DocTag", "PatternDeployment", "PatternEmbedding"]
