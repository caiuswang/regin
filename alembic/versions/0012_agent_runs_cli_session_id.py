"""agent_runs.cli_session_id — the child `claude` session a run is traced as.

Revision ID: 0012
Revises: 0011
Create Date: 2026-08-01

A regin-launched SDK run is traced **twice**. The runner synthesizes spans from
the SDK message stream under its own ``sdk-<hex>`` trace, while the child
``claude`` — which loads the user's settings, hooks included
(``lib/agent_sdk/client.py`` sets ``setting_sources``) — writes a second, richer
trace under the session id it reports for itself. The two carry different
things: only the SDK stream is live (it needs no hook to fire), and only the
hook trace has ``rule.check`` / ``instructions.loaded`` / ``cwd.changed`` /
``turn``.

``registry.register_alias`` already knew the mapping but held it in a
process-local dict, so it died with the server and no reader outside
``lib/agent_sdk`` could see it. Persisting it here is what lets the serve-time
reader union the two into one session.

Nullable by design: the child reports its id on the first message of the first
turn, so a run has none until then, and a run whose child never spoke keeps
none forever. Readers must treat NULL as "not aliased", never as an error.

The canonical final shape lives in ``db/schema.sql``; fresh installs run that
directly.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0012"
down_revision: Union[str, Sequence[str], None] = "0011"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("agent_runs", sa.Column("cli_session_id", sa.Text(),
                                          nullable=True))
    # The read path resolves in both directions — run → child (opening the
    # run's own id) and child → run (opening the id the hooks recorded) — so
    # the reverse lookup needs its own index.
    op.create_index("idx_agent_runs_cli_session_id", "agent_runs",
                    ["cli_session_id"])


def downgrade() -> None:
    op.drop_index("idx_agent_runs_cli_session_id", table_name="agent_runs")
    op.drop_column("agent_runs", "cli_session_id")
