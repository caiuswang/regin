"""prompt_template_kind_variables — dynamic composable prompt templates.

Revision ID: 0005
Revises: 0004
Create Date: 2026-07-02

Adds two columns to ``prompt_templates`` so a row can be a full external-agent
goal ``skeleton`` (with ``{{variable}}`` slots) as well as an injectable
``fragment``:

- ``kind``      TEXT NOT NULL DEFAULT 'fragment'  — 'fragment' | 'skeleton'.
- ``variables`` TEXT NOT NULL DEFAULT '[]'        — JSON palette of the vars the
                                                     body interpolates.

The canonical shape lives in ``db/schema.sql``; fresh installs run that directly.
Builtin skeleton rows are seeded from the Python surface registry by
``lib.prompt_templates.seed_builtin_skeletons`` (init/rebuild), not by this
migration, because their bodies live in code — so this migration only widens the
table. Existing rows default to ``kind='fragment'``, preserving today's behavior.
"""

from __future__ import annotations

from alembic import op
from sqlalchemy import inspect

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def _has_column(table: str, column: str) -> bool:
    cols = {c["name"] for c in inspect(op.get_bind()).get_columns(table)}
    return column in cols


def upgrade() -> None:
    if not _has_column("prompt_templates", "kind"):
        op.execute("ALTER TABLE prompt_templates ADD COLUMN kind TEXT NOT NULL DEFAULT 'fragment'")
    if not _has_column("prompt_templates", "variables"):
        op.execute("ALTER TABLE prompt_templates ADD COLUMN variables TEXT NOT NULL DEFAULT '[]'")
    op.execute("CREATE INDEX IF NOT EXISTS ix_prompt_templates_kind ON prompt_templates(kind)")


def downgrade() -> None:
    # SQLite cannot drop columns in place; rebuild the table without them.
    op.execute("DROP INDEX IF EXISTS ix_prompt_templates_kind")
    op.execute("""
        CREATE TABLE prompt_templates_old (
            id                      INTEGER PRIMARY KEY AUTOINCREMENT,
            slug                    TEXT NOT NULL UNIQUE,
            label                   TEXT NOT NULL,
            description             TEXT,
            body                    TEXT NOT NULL,
            applies_to              TEXT NOT NULL DEFAULT '[]',
            default_for_providers   TEXT NOT NULL DEFAULT '[]',
            builtin                 INTEGER NOT NULL DEFAULT 0,
            created_at              TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at              TEXT NOT NULL DEFAULT (datetime('now'))
        )
    """)
    op.execute("""
        INSERT INTO prompt_templates_old
            (id, slug, label, description, body, applies_to, default_for_providers, builtin, created_at, updated_at)
        SELECT
            id, slug, label, description, body, applies_to, default_for_providers, builtin, created_at, updated_at
        FROM prompt_templates
    """)
    op.execute("DROP TABLE prompt_templates")
    op.execute("ALTER TABLE prompt_templates_old RENAME TO prompt_templates")
    op.execute("CREATE INDEX IF NOT EXISTS ix_prompt_templates_slug ON prompt_templates(slug)")
