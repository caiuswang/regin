"""prompt_template_agent_binding — bind a goal-prompt skeleton to an agent.

Revision ID: 0006
Revises: 0005
Create Date: 2026-07-03

Adds one nullable column to ``prompt_templates`` so a ``skeleton`` row (a full
external-agent goal prompt) can be *bound* to a specific external agent:

- ``agent`` TEXT NULL — a key in ``settings.topic_proposal_external_agents``.
  NULL = no binding: the dispatch falls back to the surface's default agent, so
  existing rows keep today's exact behavior.

The canonical shape lives in ``db/schema.sql``; fresh installs run that directly.
This migration only widens the table for existing installs.
"""

from __future__ import annotations

from alembic import op
from sqlalchemy import inspect

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def _has_column(table: str, column: str) -> bool:
    cols = {c["name"] for c in inspect(op.get_bind()).get_columns(table)}
    return column in cols


def upgrade() -> None:
    if not _has_column("prompt_templates", "agent"):
        op.execute("ALTER TABLE prompt_templates ADD COLUMN agent TEXT")


def downgrade() -> None:
    # SQLite cannot drop columns in place; rebuild the table without ``agent``.
    op.execute("DROP INDEX IF EXISTS ix_prompt_templates_kind")
    op.execute("DROP INDEX IF EXISTS ix_prompt_templates_slug")
    op.execute("""
        CREATE TABLE prompt_templates_old (
            id                      INTEGER PRIMARY KEY AUTOINCREMENT,
            slug                    TEXT NOT NULL UNIQUE,
            label                   TEXT NOT NULL,
            description             TEXT,
            body                    TEXT NOT NULL,
            kind                    TEXT NOT NULL DEFAULT 'fragment',
            variables               TEXT NOT NULL DEFAULT '[]',
            applies_to              TEXT NOT NULL DEFAULT '[]',
            default_for_providers   TEXT NOT NULL DEFAULT '[]',
            builtin                 INTEGER NOT NULL DEFAULT 0,
            created_at              TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at              TEXT NOT NULL DEFAULT (datetime('now'))
        )
    """)
    op.execute("""
        INSERT INTO prompt_templates_old
            (id, slug, label, description, body, kind, variables, applies_to,
             default_for_providers, builtin, created_at, updated_at)
        SELECT
            id, slug, label, description, body, kind, variables, applies_to,
            default_for_providers, builtin, created_at, updated_at
        FROM prompt_templates
    """)
    op.execute("DROP TABLE prompt_templates")
    op.execute("ALTER TABLE prompt_templates_old RENAME TO prompt_templates")
    op.execute("CREATE INDEX IF NOT EXISTS ix_prompt_templates_slug ON prompt_templates(slug)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_prompt_templates_kind ON prompt_templates(kind)")
