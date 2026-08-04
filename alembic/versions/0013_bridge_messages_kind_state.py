"""bridge_messages.kind + state — chip lifecycle instead of a time window.

Revision ID: 0013
Revises: 0012
Create Date: 2026-08-04

``bridge_messages`` conflated two axes: the audit trail (every steering-inbox
row) and the /live "queued" chip feed, which served *every* delivered row from
the last 90 seconds. Anything whose body never reappears as a literal user
turn — an AskUserQuestion answer ("selected Yes, auto-accept edits"), a
permission decision, an executed slash command — could only leave the screen
by timer.

``kind`` separates the axes at record time: only ``steer`` rows are ever
chip-eligible; ``answer``/``decision`` rows are audit-only. ``state`` replaces
the window with one-way transitions written by observed events:

    pending → consumed   (transcript queued/processed the body)
            → closed     (session ended, or born closed: answers, decisions,
                          SDK-tier steers whose in-memory queue is authoritative)
            → dismissed  (operator removed the chip)

Historical rows are backfilled ``closed`` — the chip lifecycle only applies
going forward, and resurrecting old rows as pending chips would replay every
past steer onto its trace page. Their ``kind`` backfill is best-effort by body
shape and purely cosmetic (state ``closed`` never renders).

The canonical final shape lives in ``db/schema.sql``; fresh installs run that
directly.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0013"
down_revision: Union[str, Sequence[str], None] = "0012"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("bridge_messages",
                  sa.Column("kind", sa.Text(), nullable=False,
                            server_default="steer"))
    op.add_column("bridge_messages",
                  sa.Column("state", sa.Text(), nullable=False,
                            server_default="pending"))
    op.add_column("bridge_messages", sa.Column("state_at", sa.Text(),
                                               nullable=True))
    op.execute(
        "UPDATE bridge_messages SET kind = CASE"
        " WHEN body LIKE 'selected %' THEN 'answer'"
        " WHEN body LIKE 'allowed the pending request%'"
        "   OR body LIKE 'denied the pending request%' THEN 'decision'"
        " ELSE 'steer' END")
    op.execute("UPDATE bridge_messages SET state = 'closed'")


def downgrade() -> None:
    op.drop_column("bridge_messages", "state_at")
    op.drop_column("bridge_messages", "state")
    op.drop_column("bridge_messages", "kind")
