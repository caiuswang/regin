"""A project keeps its own ordered list of step stages; a task names its stage.

`project_stages` is the list of steps a feature can open. `project_id IS NULL`
rows are the global default — what a feature with no project reads, and what a
project copies on its first write. `role` (spec | build) is what a step means to
the ledger, so it becomes the task's `kind`; the stage key is a second axis and
lands in `agent_tasks.stage_key`, never inside `kind`.

The default stages keep the keys `spec` and `build`, so an existing step task's
`external_id = feature:<id>:spec|build` still names the same row; only its
`stage_key` is backfilled. The global spec stage's prompt includes the existing
`task-run-spec` surface rather than copying its text, so a spec run says what it
said before and either row stays the place to edit it.

Revision ID: 0038
Revises: 0037
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0038"
down_revision: Union[str, Sequence[str], None] = "0037"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_STAGE_VARIABLES = (
    '[{"name": "feature_named", "description": "The feature as the prompt '
    'names it.", "example": "#80 (Agent SDK runs)", "required": false}, '
    '{"name": "spec_cmd", "description": "The literal `regin feature spec` '
    'write command for this feature.", "example": "", "required": false}, '
    '{"name": "stage_label", "description": "The stage this step belongs to.",'
    ' "example": "Spec", "required": false}]')


def _have(table: str) -> set[str]:
    return {c["name"] for c in sa.inspect(op.get_bind()).get_columns(table)}


def _tables() -> set[str]:
    return set(sa.inspect(op.get_bind()).get_table_names())


def upgrade() -> None:
    if "project_stages" not in _tables():
        op.execute("""
            CREATE TABLE project_stages (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                project_id   INTEGER,
                key          TEXT NOT NULL,
                label        TEXT NOT NULL,
                role         TEXT NOT NULL,
                position     INTEGER NOT NULL DEFAULT 0,
                prompt_slug  TEXT,
                builtin      INTEGER NOT NULL DEFAULT 0,
                archived_at  TEXT,
                created_at   TEXT NOT NULL DEFAULT (datetime('now')),
                updated_at   TEXT NOT NULL DEFAULT (datetime('now')),
                CONSTRAINT uq_project_stages_project_key UNIQUE (project_id, key)
            )""")
        op.execute("CREATE UNIQUE INDEX IF NOT EXISTS "
                   "uq_project_stages_global_key ON project_stages(key) "
                   "WHERE project_id IS NULL")
    if "stage_key" not in _have("agent_tasks"):
        op.add_column("agent_tasks",
                      sa.Column("stage_key", sa.Text(), nullable=True))
    for key in ("spec", "build"):
        op.execute(sa.text(
            "UPDATE agent_tasks SET stage_key = :key "
            "WHERE stage_key IS NULL AND external_id LIKE :shape"
        ).bindparams(key=key, shape=f"feature:%:{key}"))
    op.execute(
        "INSERT OR IGNORE INTO prompt_templates "
        "(slug, label, description, body, kind, variables, builtin) VALUES "
        "('stage-global-spec', 'Stage — Spec', 'The prompt a spec step run "
        "is told (default stages).', '{{include:task-run-spec}}', 'stage', "
        f"'{_STAGE_VARIABLES}', 1), "
        "('stage-global-build', 'Stage — Build', 'The prompt a build step run "
        "is told (default stages).', '', 'stage', "
        f"'{_STAGE_VARIABLES}', 1)")
    op.execute(
        "INSERT OR IGNORE INTO project_stages "
        "(project_id, key, label, role, position, prompt_slug, builtin) VALUES "
        "(NULL, 'spec', 'Spec', 'spec', 0, 'stage-global-spec', 1), "
        "(NULL, 'build', 'Build', 'build', 1, 'stage-global-build', 1)")


def downgrade() -> None:
    if "stage_key" in _have("agent_tasks"):
        op.drop_column("agent_tasks", "stage_key")
    if "project_stages" in _tables():
        op.execute("DROP INDEX IF EXISTS uq_project_stages_global_key")
        op.drop_table("project_stages")
    op.execute("DELETE FROM prompt_templates WHERE kind = 'stage'")
