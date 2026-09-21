"""One defect: something a feature got wrong.

Its own table rather than a `kind='defect'` memory because a defect carries
lifecycle axes a memory has none of — open/closed, a round counter, attributed
or not. Folding those into tags would put two axes on one column. The *lesson*
a defect yields when it closes is still a memory.

A NULL `feature_id` is **unattributed**, and that is a first-class state, not a
gap to be filled by a guess: a wrong parent inflates one feature's rounds and
exonerates another, and the result is corrupted data that looks fine.

`blamed_event_id` is the escape verdict, and it is a **pointer at an immutable
row** rather than a cached word. Its predecessor was a nullable
`blamed_against_state` string that could be absent (nobody looked), stale, or
hold a vocabulary regin did not carry — three readings for one column. An
event cannot go stale, so "did this feature have shipped when we blamed it"
is one comparison with no gaps to count.
"""

from __future__ import annotations

from typing import Optional

from sqlmodel import (
    Column, Field, ForeignKey, Index, Integer, LargeBinary, String, Text,
    UniqueConstraint, text,
)

from lib.orm.base import Base

STATUS_OPEN = "open"
STATUS_CLOSED = "closed"

#: Still in the triage queue is the ABSENCE of a verdict, never a word — so a
#: defect opened before triage existed is in the queue without a backfill.
TRIAGE_DECLINED = "declined"
TRIAGE_SNOOZED = "snoozed"

SYMPTOM_MAX = 4000


class Defect(Base, table=True):
    """One reported defect and its round counter."""

    __tablename__ = "defects"
    __table_args__ = (
        UniqueConstraint("feature_id", "symptom_key",
                         name="uq_defects_feature_symptom"),
        # An unattributed defect dedups within its session. A partial index
        # rather than a Python check because its sibling above already is one:
        # two rules of the same kind enforced at two different layers is how
        # one of them quietly stops holding. NULL never collides in SQLite, so
        # a row with neither feature nor session stays distinct — which is
        # correct, there is nothing for it to collide with.
        Index("uq_defects_session_symptom", "reported_by_trace", "symptom_key",
              unique=True, sqlite_where=text("feature_id IS NULL")),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    feature_id: Optional[int] = Field(
        default=None,
        sa_column=Column("feature_id", Integer,
                         ForeignKey("features.id", ondelete="SET NULL"),
                         index=True))
    symptom: str = Field(sa_column=Column("symptom", Text, nullable=False))
    # The dedup axis, kept separate from `symptom` so the reporter's own
    # wording survives verbatim while matching stays insensitive to case and
    # whitespace.
    symptom_key: str = Field(
        sa_column=Column("symptom_key", Text, nullable=False))
    # Who reported it, and never who caused it. The name carries the
    # invariant: the old `session_id` was one rename away from being read as
    # blame, and neither direction is ever derived from the other.
    reported_by_trace: Optional[str] = Field(
        default=None, sa_column=Column("reported_by_trace", Text))
    suspect_paths_json: Optional[str] = Field(
        default=None, sa_column=Column("suspect_paths_json", Text))
    round: int = Field(
        default=1,
        sa_column=Column("round", Integer, nullable=False,
                         server_default=text("1")))
    status: str = Field(
        default=STATUS_OPEN,
        sa_column=Column("status", Text, nullable=False,
                         server_default=text("'open'")))
    missing_check: Optional[str] = Field(
        default=None, sa_column=Column("missing_check", Text))
    opened_at: Optional[str] = Field(
        default=None,
        sa_column=Column("opened_at", Text, nullable=False,
                         server_default=text("(datetime('now'))")))
    updated_at: Optional[str] = Field(
        default=None,
        sa_column=Column("updated_at", Text, nullable=False,
                         server_default=text("(datetime('now'))")))
    closed_at: Optional[str] = Field(
        default=None, sa_column=Column("closed_at", Text))
    # The task that will close this defect. A foreign key rather than the
    # `(fix_provider, fix_external_key)` pair it replaces: every tracker item
    # regin can point at is now a row in `agent_tasks`, so the loop closes in
    # the schema instead of in a string comparison nobody could join on.
    # regin never writes the defect's status from it or its state from the
    # defect — `defects.status` asks *is this bug still real*, a task asks
    # *is this work still scheduled*.
    fix_task_id: Optional[int] = Field(
        default=None,
        sa_column=Column("fix_task_id", Integer, index=True))
    # The feature's last event at the instant blame was assigned. An escape is
    # `blamed_event.kind == 'shipped'`. Re-attributing to another feature
    # re-points it; the feature shipping afterwards does not, because the
    # event this names cannot change.
    blamed_event_id: Optional[int] = Field(
        default=None,
        sa_column=Column("blamed_event_id", Integer,
                         ForeignKey("feature_events.id", ondelete="SET NULL")))
    # The triage queue's verdict, and a second axis on purpose: `status` asks
    # whether the bug is still real, `triage_state` whether anyone has routed
    # it. A declined defect and a fixed defect are both closed, and only one
    # of them was ever looked at — one column could not say which.
    triage_state: Optional[str] = Field(
        default=None, sa_column=Column("triage_state", Text, index=True))
    triage_reason: Optional[str] = Field(
        default=None, sa_column=Column("triage_reason", Text))
    # `activity_seq` at the instant of a snooze. The snooze ends when the two
    # differ — an event, never a deadline.
    snooze_watermark: Optional[int] = Field(
        default=None, sa_column=Column("snooze_watermark", Integer))
    # Writes to this row, counted. `updated_at` cannot serve as the activity
    # marker: it has second resolution, so two writes inside one second are
    # indistinguishable and "did anything happen" would answer no while
    # something had.
    activity_seq: int = Field(
        default=0,
        sa_column=Column("activity_seq", Integer, nullable=False,
                         server_default=text("0")))


class DefectImage(Base, table=True):
    """A screenshot attached to a defect report.

    The third `ImageSet` owner, and the only one whose bytes are evidence
    rather than input: a task's and a feature's images ride an agent's prompt,
    while these are what the reporter saw. Nothing delivers them to a model —
    the fix task cut from the defect carries its own.

    A separate table for the reason the other two are separate: one owner per
    column, so "whose image is this" is never a join away from ambiguous.
    `idx` is a per-defect order, allocated by the writer and never reissued
    once freed.
    """

    __tablename__ = "defect_images"
    __table_args__ = (UniqueConstraint("defect_id", "idx",
                                       name="uq_defect_images_idx"),)

    id: Optional[int] = Field(default=None, primary_key=True)
    defect_id: int = Field(
        sa_column=Column("defect_id", Integer,
                         ForeignKey("defects.id", ondelete="CASCADE"),
                         nullable=False, index=True))
    idx: int = Field(sa_column=Column("idx", Integer, nullable=False))
    media_type: str = Field(
        sa_column=Column("media_type", String, nullable=False))
    bytes_: bytes = Field(
        sa_column=Column("bytes", LargeBinary, nullable=False))
    byte_size: int = Field(
        sa_column=Column("byte_size", Integer, nullable=False))
    sha256: str = Field(sa_column=Column("sha256", String, nullable=False))
    created_at: str = Field(
        sa_column=Column("created_at", String, nullable=False,
                         server_default=text("(datetime('now'))")))
