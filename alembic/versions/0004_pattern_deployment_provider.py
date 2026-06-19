"""pattern_deployment_provider — track which agent provider owns a deployment.

Revision ID: 0004
Revises: 0003
Create Date: 2026-06-16

Adds a nullable ``provider`` column to ``pattern_deployments`` and updates the
unique constraint from (pattern_slug, scope, project_id) to include ``provider``.
This lets regin deploy the same skill to multiple agent providers in the same
project (e.g. ``.claude/skills/`` and ``.kimi-code/skills/``).

The canonical shape lives in ``db/schema.sql``; fresh installs run that directly.
"""

from __future__ import annotations

from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # SQLite can add columns but cannot drop/recreate the unique constraint in
    # place, so we rebuild the table.
    op.execute("""
        CREATE TABLE IF NOT EXISTS pattern_deployments_new (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            pattern_slug    TEXT NOT NULL,
            scope           TEXT NOT NULL,
            project_id      INTEGER,
            provider        TEXT,
            deployed_path   TEXT NOT NULL,
            deployed_at     TEXT NOT NULL DEFAULT (datetime('now')),
            deployed_by     INTEGER,
            UNIQUE(pattern_slug, scope, project_id, provider)
        )
    """)
    op.execute("""
        INSERT INTO pattern_deployments_new
            (id, pattern_slug, scope, project_id, provider, deployed_path, deployed_at, deployed_by)
        SELECT
            id, pattern_slug, scope, project_id, NULL, deployed_path, deployed_at, deployed_by
        FROM pattern_deployments
    """)
    op.execute("DROP TABLE pattern_deployments")
    op.execute("ALTER TABLE pattern_deployments_new RENAME TO pattern_deployments")
    # Backfill the owning provider for rows carried over from the pre-provider
    # schema. Their on-disk location identifies the provider; rows with no
    # recognizable segment default to claude (the only provider that existed
    # before multi-provider support). Leaving these NULL makes the per-provider
    # deployment scan re-report them as "untracked" and provider-scoped
    # removes/re-deploys miss them.
    op.execute("""
        UPDATE pattern_deployments SET provider = CASE
            WHEN deployed_path LIKE '%/.codex/%'     THEN 'codex'
            WHEN deployed_path LIKE '%/.kimi-code/%' THEN 'kimi'
            WHEN deployed_path LIKE '%/.agent/%'     THEN 'generic'
            ELSE 'claude'
        END
        WHERE provider IS NULL
    """)
    op.execute("CREATE INDEX IF NOT EXISTS idx_pattern_deployments_pattern "
               "ON pattern_deployments(pattern_slug)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_pattern_deployments_project "
               "ON pattern_deployments(project_id)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_pattern_deployments_scope "
               "ON pattern_deployments(scope)")


def downgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS pattern_deployments_old (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            pattern_slug    TEXT NOT NULL,
            scope           TEXT NOT NULL,
            project_id      INTEGER,
            deployed_path   TEXT NOT NULL,
            deployed_at     TEXT NOT NULL DEFAULT (datetime('now')),
            deployed_by     INTEGER,
            UNIQUE(pattern_slug, scope, project_id)
        )
    """)
    op.execute("""
        INSERT INTO pattern_deployments_old
            (id, pattern_slug, scope, project_id, deployed_path, deployed_at, deployed_by)
        SELECT
            id, pattern_slug, scope, project_id, deployed_path, deployed_at, deployed_by
        FROM pattern_deployments
    """)
    op.execute("DROP TABLE pattern_deployments")
    op.execute("ALTER TABLE pattern_deployments_old RENAME TO pattern_deployments")
    op.execute("CREATE INDEX IF NOT EXISTS idx_pattern_deployments_pattern "
               "ON pattern_deployments(pattern_slug)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_pattern_deployments_project "
               "ON pattern_deployments(project_id)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_pattern_deployments_scope "
               "ON pattern_deployments(scope)")
