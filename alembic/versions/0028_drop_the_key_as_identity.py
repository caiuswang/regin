"""Step 6: delete the shape a feature key used to have. No compatibility layer.

Every column and table dropped here held one of the five readings of "what is a
feature". They are not kept as a fallback, and no retired-key registry replaces
them: a compatibility layer would preserve exactly the ambiguity `0027` exists
to remove, and the acceptance for this step is that none of them is reachable.

`agent_tasks.parent_id` goes with them. The two-level grain — a parent IS a
feature, its children are the runs — was the workaround for a feature not
having a table; a task now points at a feature and an attempt is a row in
`agent_task_runs`.

Revision ID: 0028
Revises: 0027
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0028"
down_revision: Union[str, Sequence[str], None] = "0027"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Rebuilt rather than altered, for the reason spelled out on `defects`
    # below: dropping six columns in a batch leaves `feature_id` last, while a
    # fresh `schema.sql` install has it where it is written. Two installs with
    # the same columns in two orders is exactly what the parity test catches.
    op.drop_index("idx_agent_tasks_parent", table_name="agent_tasks")
    op.drop_index("idx_agent_tasks_status", table_name="agent_tasks")
    op.drop_index("idx_agent_tasks_project", table_name="agent_tasks")
    op.drop_index("idx_agent_tasks_created", table_name="agent_tasks")
    op.drop_index("idx_agent_tasks_status_created", table_name="agent_tasks")
    op.drop_index("idx_agent_tasks_priority", table_name="agent_tasks")
    op.drop_index("idx_agent_tasks_feature", table_name="agent_tasks")
    op.execute(sa.text("DROP TABLE IF EXISTS agent_tasks_new"))
    op.execute(sa.text("""
        CREATE TABLE agent_tasks_new (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            title         TEXT NOT NULL,
            body          TEXT,
            status        TEXT NOT NULL DEFAULT 'idea',
            source        TEXT NOT NULL,
            external_id   TEXT,
            tags_json     TEXT,
            links_json    TEXT,
            created_by    INTEGER,
            created_at    TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at    TEXT NOT NULL DEFAULT (datetime('now')),
            project_id    INTEGER,
            priority      TEXT NOT NULL DEFAULT 'none',
            priority_rank INTEGER NOT NULL DEFAULT 4,
            feature_id    INTEGER REFERENCES features(id) ON DELETE SET NULL,
            assignee      INTEGER,
            CONSTRAINT uq_agent_tasks_source_external
                UNIQUE (source, external_id)
        )"""))
    op.execute(sa.text(
        "INSERT INTO agent_tasks_new (id, title, body, status, source,"
        " external_id, tags_json, links_json, created_by, created_at,"
        " updated_at, project_id, priority, priority_rank, feature_id,"
        " assignee)"
        " SELECT id, title, body, status, source, external_id, tags_json,"
        " links_json, created_by, created_at, updated_at, project_id,"
        " priority, priority_rank, feature_id, assignee FROM agent_tasks"))
    op.execute(sa.text("DROP TABLE agent_tasks"))
    op.execute(sa.text("ALTER TABLE agent_tasks_new RENAME TO agent_tasks"))
    op.create_index("idx_agent_tasks_status", "agent_tasks", ["status"])
    op.create_index("idx_agent_tasks_feature", "agent_tasks", ["feature_id"])
    op.create_index("idx_agent_tasks_project", "agent_tasks", ["project_id"])
    op.create_index("idx_agent_tasks_created", "agent_tasks",
                    ["created_at", "id"])
    op.create_index("idx_agent_tasks_status_created", "agent_tasks",
                    ["status", "created_at", "id"])
    op.create_index("idx_agent_tasks_priority", "agent_tasks",
                    ["priority_rank", "created_at", "id"])

    # Rebuilt rather than altered, like `session_features` below. A
    # `batch_alter_table` that drops columns appends the surviving ones in
    # visit order, so a migrated install would end up with `feature_id` last
    # while a fresh `schema.sql` install has it second — same columns, two
    # different `SELECT *` orders, which is exactly the divergence the schema
    # parity test exists to catch.
    op.drop_index("idx_defects_feature", table_name="defects")
    op.drop_index("idx_defects_fix_item", table_name="defects")
    op.drop_index("uq_defects_session_symptom", table_name="defects")
    op.execute(sa.text("DROP TABLE IF EXISTS defects_new"))
    op.execute(sa.text("""
        CREATE TABLE defects_new (
            id                 INTEGER PRIMARY KEY AUTOINCREMENT,
            feature_id         INTEGER REFERENCES features(id)
                               ON DELETE SET NULL,
            symptom            TEXT NOT NULL,
            symptom_key        TEXT NOT NULL,
            reported_by_trace  TEXT,
            suspect_paths_json TEXT,
            round              INTEGER NOT NULL DEFAULT 1,
            status             TEXT NOT NULL DEFAULT 'open',
            missing_check      TEXT,
            opened_at          TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at         TEXT NOT NULL DEFAULT (datetime('now')),
            closed_at          TEXT,
            fix_task_id        INTEGER,
            blamed_event_id    INTEGER REFERENCES feature_events(id)
                               ON DELETE SET NULL,
            CONSTRAINT uq_defects_feature_symptom
                UNIQUE (feature_id, symptom_key)
        )"""))
    op.execute(sa.text(
        "INSERT INTO defects_new (id, feature_id, symptom, symptom_key,"
        " reported_by_trace, suspect_paths_json, round, status, missing_check,"
        " opened_at, updated_at, closed_at, fix_task_id, blamed_event_id)"
        " SELECT id, feature_id, symptom, symptom_key, reported_by_trace,"
        " suspect_paths_json, round, status, missing_check, opened_at,"
        " updated_at, closed_at, fix_task_id, blamed_event_id FROM defects"))
    op.execute(sa.text("DROP TABLE defects"))
    op.execute(sa.text("ALTER TABLE defects_new RENAME TO defects"))
    op.create_index("idx_defects_feature", "defects", ["feature_id"])
    op.create_index("idx_defects_fix_item", "defects", ["fix_task_id"])
    op.execute(sa.text(
        "CREATE UNIQUE INDEX uq_defects_session_symptom"
        " ON defects(reported_by_trace, symptom_key)"
        " WHERE feature_id IS NULL"))

    # The primary key moves, so the table is rebuilt rather than altered.
    op.drop_index("idx_session_features_feature",
                  table_name="session_features")
    op.execute(sa.text("DROP TABLE IF EXISTS session_features_new"))
    op.execute(sa.text("""
        CREATE TABLE session_features_new (
            trace_id   TEXT NOT NULL,
            feature_id INTEGER NOT NULL
                       REFERENCES features(id) ON DELETE CASCADE,
            is_primary INTEGER NOT NULL DEFAULT 0,
            provenance TEXT NOT NULL DEFAULT 'declared',
            round      INTEGER,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            PRIMARY KEY (trace_id, feature_id)
        )"""))
    op.execute(sa.text(
        "INSERT INTO session_features_new"
        " (trace_id, feature_id, is_primary, provenance, round, created_at)"
        " SELECT trace_id, feature_id, is_primary, provenance, round,"
        " created_at FROM session_features WHERE feature_id IS NOT NULL"))
    op.execute(sa.text("DROP TABLE session_features"))
    op.execute(sa.text(
        "ALTER TABLE session_features_new RENAME TO session_features"))
    op.create_index("idx_session_features_feature", "session_features",
                    ["feature_id"])

    op.drop_index("idx_agent_task_links_task", table_name="agent_task_links")
    op.drop_table("agent_task_links")
    op.drop_table("issue_cache")


def downgrade() -> None:
    """Deliberately not reversible.

    The columns this dropped are the *inputs* the new tables were derived
    from, and the derivation is lossy in the two places `0027` records as
    gaps. Rebuilding them would mean inventing the round moments and the blame
    states that were never written down — a downgrade that fabricates history
    is worse than one that refuses.
    """
    raise NotImplementedError(
        "0028 is one-way: the dropped columns cannot be rebuilt without "
        "inventing the history 0027 recorded as gaps")
