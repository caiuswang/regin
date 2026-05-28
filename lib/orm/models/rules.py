"""GritQL rule-trigger log + experiments."""

from __future__ import annotations

from typing import Optional

from sqlalchemy import text
from sqlmodel import Column, Field, Integer, String, Text

from lib.orm.base import Base


class RuleTrigger(Base, table=True):
    __tablename__ = "rule_triggers"

    id: Optional[int] = Field(default=None, primary_key=True)
    rule_id: str = Field(sa_column=Column("rule_id", String, nullable=False))
    file_path: str = Field(sa_column=Column("file_path", String, nullable=False))
    repo: Optional[str] = Field(default=None, sa_column=Column("repo", String))
    match_count: int = Field(
        sa_column=Column("match_count", Integer, nullable=False,
                         server_default=text("0")),
    )
    triggered: int = Field(
        sa_column=Column("triggered", Integer, nullable=False,
                         server_default=text("0")),
    )
    severity: Optional[str] = Field(default=None, sa_column=Column("severity", String))
    guide: Optional[str] = Field(default=None, sa_column=Column("guide", String))
    summary: Optional[str] = Field(default=None, sa_column=Column("summary", Text))
    source: Optional[str] = Field(default=None, sa_column=Column("source", String))
    session_id: Optional[str] = Field(default=None, sa_column=Column("session_id", String))
    span_id: Optional[str] = Field(default=None, sa_column=Column("span_id", String))
    experiment_id: Optional[int] = Field(default=None,
                                         sa_column=Column("experiment_id", Integer))
    suppressed: int = Field(
        sa_column=Column("suppressed", Integer, nullable=False,
                         server_default=text("0")),
    )
    checked_at: Optional[str] = Field(
        default=None,
        sa_column=Column("checked_at", Text, nullable=False,
                         server_default=text("(datetime('now'))")),
    )


class Experiment(Base, table=True):
    __tablename__ = "experiments"

    id: Optional[int] = Field(default=None, primary_key=True)
    pattern_slug: str = Field(sa_column=Column("pattern_slug", String, nullable=False))
    name: str = Field(sa_column=Column("name", String, nullable=False))
    conceal_spec: str = Field(sa_column=Column("conceal_spec", Text, nullable=False))
    active: int = Field(
        sa_column=Column("active", Integer, nullable=False,
                         server_default=text("0")),
    )
    created_at: Optional[str] = Field(
        default=None,
        sa_column=Column("created_at", Text, nullable=False,
                         server_default=text("(datetime('now'))")),
    )
    activated_at: Optional[str] = Field(default=None,
                                        sa_column=Column("activated_at", Text))


class RuleTriggerSuppression(Base, table=True):
    """One marked-as-noise annotation per `RuleTrigger` row.

    The unique constraint on `rule_trigger_id` ensures a single event
    can be suppressed at most once. The denormalized
    `rule_triggers.suppressed` boolean is kept in sync via the
    suppress/unsuppress endpoints so the hot-path aggregate queries
    can filter without joining this table.
    """

    __tablename__ = "rule_trigger_suppressions"

    id: Optional[int] = Field(default=None, primary_key=True)
    rule_trigger_id: int = Field(
        sa_column=Column("rule_trigger_id", Integer, nullable=False, unique=True),
    )
    suppressed_by_id: int = Field(
        sa_column=Column("suppressed_by_id", Integer, nullable=False),
    )
    suppressed_by_username: str = Field(
        sa_column=Column("suppressed_by_username", String, nullable=False),
    )
    suppressed_at: Optional[str] = Field(
        default=None,
        sa_column=Column("suppressed_at", Text, nullable=False,
                         server_default=text("(datetime('now'))")),
    )
    reason: Optional[str] = Field(default=None, sa_column=Column("reason", Text))


__all__ = ["RuleTrigger", "RuleTriggerSuppression", "Experiment"]
