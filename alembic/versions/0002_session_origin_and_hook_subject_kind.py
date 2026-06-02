"""release schema update — session origin + hook subject_kind + span source.

Revision ID: 0002
Revises: 0001
Create Date: 2026-05-31

This single revision carries a deployed v0.1.0 database forward to the
v0.2.0 release in one step. It folds together three orthogonal schema
changes so the DB is updated exactly once per release:

1. ``sessions.origin`` — split the overloaded ``agent_type``.
   ``agent_type`` used to double as both the launching agent's vendor
   ('claude' | 'codex') and a marker for captured dynamic-workflow runs
   (the literal 'workflow'). Those are orthogonal axes, so we add
   ``sessions.origin`` — what KIND of row this is ('session' for a real
   interactive agent session, 'workflow' for a captured run, with room
   for future synthetic kinds) — and backfill the old overloaded rows:
   any row tagged agent_type='workflow' becomes origin='workflow' with
   agent_type reset to 'claude' (the Workflow tool is a Claude Code
   feature, so the vendor is always Claude).

2. ``payload_schema_drift.subject_kind`` — extend payload schema-drift
   from tools to hook events. ``payload_schema_drift`` previously only
   tracked PostToolUse tool-call payloads. We add an orthogonal
   ``subject_kind`` axis ('tool' | 'hook_event') so the same drift
   machinery can also cover Claude Code hook events (PreToolUse, Stop,
   SessionStart, ...). The row-uniqueness constraint widens from the
   5-tuple (agent, tool_name, drift_kind, field_path, claude_version) to
   the 6-tuple that also includes ``subject_kind``. SQLite cannot
   ``ALTER TABLE ... DROP CONSTRAINT``, so the table is rebuilt by hand
   (full copy). Existing rows backfill to ``subject_kind='tool'`` via the
   column ``server_default``, preserving the existing tool path
   byte-for-byte.

3. ``session_spans.source`` — which capture source wrote the row: 'hook'
   (live hook events: tool timing, permissions, skill reads, the
   in-flight prompt placeholder) or 'transcript' (the transcript scan:
   prompt anchors, assistant_response/thinking, local commands). Lands
   alongside the capture refactor that makes ``session_spans``
   append-only — the two sources no longer mutate each other's rows at
   ingest; both coexist and a pure serve-time merge
   (``lib/trace/merge.py``) selects winners. ``source`` is the
   audit/debug discriminator for that split (the merge keys on
   span_id/name, so it is not load-bearing for correctness). Backfill:
   transcript-minted rows carry deterministic id prefixes (``prompt-``,
   ``resp-``, ``think-``, ``cmd-``) → 'transcript'; everything else
   defaults to 'hook'.

The canonical final shape lives in ``db/schema.sql``; fresh installs run
that directly and never touch this revision.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0002"
down_revision: Union[str, Sequence[str], None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# --- payload_schema_drift rebuild helpers ----------------------------------

# Shared column list (excluding subject_kind) for INSERT…SELECT copies.
# subject_kind backfills to its column DEFAULT ('tool') on the way in.
_CARRIED_COLS = (
    "id, agent, tool_name, drift_kind, field_path, expected, sample_value, "
    "sample_payload_sha, claude_version, first_seen, last_seen, "
    "occurrence_count, status"
)

# Canonical target DDL (matches db/schema.sql): subject_kind sits after
# agent and the unique constraint is the 6-tuple.
_TARGET_DDL = """
CREATE TABLE payload_schema_drift_new (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    agent               TEXT NOT NULL DEFAULT 'claude',
    subject_kind        TEXT NOT NULL DEFAULT 'tool',
    tool_name           TEXT NOT NULL,
    drift_kind          TEXT NOT NULL,
    field_path          TEXT NOT NULL,
    expected            TEXT,
    sample_value        TEXT NOT NULL,
    sample_payload_sha  TEXT,
    claude_version      TEXT,
    first_seen          TEXT NOT NULL DEFAULT (datetime('now')),
    last_seen           TEXT NOT NULL DEFAULT (datetime('now')),
    occurrence_count    INTEGER NOT NULL DEFAULT 1,
    status              TEXT NOT NULL DEFAULT 'pending',
    CONSTRAINT uq_payload_schema_drift_key
        UNIQUE (agent, subject_kind, tool_name, drift_kind, field_path, claude_version)
)
"""

# 5-tuple table for the downgrade path (no subject_kind).
_OLD_DDL = """
CREATE TABLE payload_schema_drift_new (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    agent               TEXT NOT NULL DEFAULT 'claude',
    tool_name           TEXT NOT NULL,
    drift_kind          TEXT NOT NULL,
    field_path          TEXT NOT NULL,
    expected            TEXT,
    sample_value        TEXT NOT NULL,
    sample_payload_sha  TEXT,
    claude_version      TEXT,
    first_seen          TEXT NOT NULL DEFAULT (datetime('now')),
    last_seen           TEXT NOT NULL DEFAULT (datetime('now')),
    occurrence_count    INTEGER NOT NULL DEFAULT 1,
    status              TEXT NOT NULL DEFAULT 'pending',
    CONSTRAINT uq_payload_schema_drift_key
        UNIQUE (agent, tool_name, drift_kind, field_path, claude_version)
)
"""

_SECONDARY_INDEXES = (
    "CREATE INDEX IF NOT EXISTS ix_payload_schema_drift_tool "
    "ON payload_schema_drift(tool_name)",
    "CREATE INDEX IF NOT EXISTS ix_payload_schema_drift_status "
    "ON payload_schema_drift(status)",
    "CREATE INDEX IF NOT EXISTS ix_payload_schema_drift_agent "
    "ON payload_schema_drift(agent)",
)


def _rebuild_table(create_ddl: str, copy_cols: str) -> None:
    """Reflection-free SQLite table swap.

    SQLite reflection drops inline ``CONSTRAINT`` names (they come back as
    ``name=None``), so Alembic's ``batch_alter_table`` cannot match
    ``drop_constraint('uq_payload_schema_drift_key')``. We rebuild the
    table by hand instead: create the target shape, copy rows, swap names,
    recreate the secondary indexes the rebuild dropped.
    """
    op.execute("DROP TABLE IF EXISTS payload_schema_drift_new")
    op.execute(create_ddl)
    op.execute(
        f"INSERT INTO payload_schema_drift_new ({copy_cols}) "
        f"SELECT {copy_cols} FROM payload_schema_drift"
    )
    op.execute("DROP TABLE payload_schema_drift")
    op.execute(
        "ALTER TABLE payload_schema_drift_new RENAME TO payload_schema_drift"
    )
    for stmt in _SECONDARY_INDEXES:
        op.execute(stmt)


def upgrade() -> None:
    """Add the three release columns: ``sessions.origin``,
    ``payload_schema_drift.subject_kind``, ``session_spans.source``."""
    # 1. sessions.origin axis + backfill the old overloaded workflow rows.
    op.add_column(
        "sessions",
        sa.Column("origin", sa.Text(), server_default="session"),
    )
    op.execute(
        "UPDATE sessions SET origin='workflow', agent_type='claude' "
        "WHERE agent_type='workflow'"
    )

    # 2. payload_schema_drift.subject_kind axis + widen the unique
    #    constraint to the 6-tuple.
    _rebuild_table(_TARGET_DDL, _CARRIED_COLS)
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_payload_schema_drift_kind "
        "ON payload_schema_drift(subject_kind)"
    )

    # 3. session_spans.source axis + backfill transcript-derived rows.
    op.add_column(
        "session_spans",
        sa.Column("source", sa.Text(), nullable=False, server_default="hook"),
    )
    op.execute(
        "UPDATE session_spans SET source='transcript' WHERE "
        "span_id LIKE 'prompt-%' OR span_id LIKE 'resp-%' OR "
        "span_id LIKE 'think-%' OR span_id LIKE 'cmd-%'"
    )


def downgrade() -> None:
    """Reverse all three axes (the agent_type backfill is not reversed)."""
    # 3. drop the session_spans.source column.
    op.drop_column("session_spans", "source")

    # 2. revert payload_schema_drift to the 5-tuple constraint.
    op.execute("DROP INDEX IF EXISTS ix_payload_schema_drift_kind")
    _rebuild_table(_OLD_DDL, _CARRIED_COLS)

    # 1. drop the sessions.origin column.
    op.drop_column("sessions", "origin")
