"""Schema-drift findings for PostToolUse payloads.

One row per (tool_name, drift_kind, field_path, claude_version) tuple.
Same-key re-observations bump `last_seen` + `occurrence_count` instead
of inserting a duplicate. Reviewed and acted on in the WebUI.
"""

from __future__ import annotations

from typing import Optional

from sqlalchemy import UniqueConstraint, text
from sqlmodel import Column, Field, Integer, String, Text

from lib.orm.base import Base


class PayloadSchemaDrift(Base, table=True):
    __tablename__ = "payload_schema_drift"
    __table_args__ = (
        UniqueConstraint(
            "agent", "tool_name", "drift_kind", "field_path", "claude_version",
            name="uq_payload_schema_drift_key",
        ),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    agent: str = Field(
        sa_column=Column(
            "agent", String, nullable=False, index=True,
            server_default=text("'claude'"),
        ),
    )
    tool_name: str = Field(
        sa_column=Column("tool_name", String, nullable=False, index=True),
    )
    drift_kind: str = Field(
        sa_column=Column("drift_kind", String, nullable=False),
    )
    field_path: str = Field(
        sa_column=Column("field_path", String, nullable=False),
    )
    expected: Optional[str] = Field(default=None, sa_column=Column("expected", Text))
    sample_value: str = Field(sa_column=Column("sample_value", Text, nullable=False))
    sample_payload_sha: Optional[str] = Field(
        default=None,
        sa_column=Column("sample_payload_sha", String),
    )
    claude_version: Optional[str] = Field(
        default=None,
        sa_column=Column("claude_version", String),
    )
    first_seen: str = Field(
        sa_column=Column(
            "first_seen", Text, nullable=False,
            server_default=text("(datetime('now'))"),
        ),
    )
    last_seen: str = Field(
        sa_column=Column(
            "last_seen", Text, nullable=False,
            server_default=text("(datetime('now'))"),
        ),
    )
    occurrence_count: int = Field(
        sa_column=Column(
            "occurrence_count", Integer, nullable=False,
            server_default=text("1"),
        ),
    )
    status: str = Field(
        sa_column=Column(
            "status", String, nullable=False,
            server_default=text("'pending'"),
        ),
    )
