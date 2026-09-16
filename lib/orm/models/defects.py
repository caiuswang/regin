"""One defect: something a feature got wrong.

Its own table rather than a `kind='defect'` memory because a defect carries
lifecycle axes a memory has none of — open/closed, a round counter, attributed
or not. Folding those into tags would put two axes on one column. The *lesson*
a defect yields when it closes is still a memory.

A NULL `feature_key` is **unattributed**, and that is a first-class state, not
a gap to be filled by a guess: a wrong parent inflates one feature's rounds and
exonerates another, and the result is corrupted data that looks fine.
"""

from __future__ import annotations

from typing import Optional

from sqlmodel import Column, Field, Index, Integer, Text, UniqueConstraint, text

from lib.orm.base import Base

STATUS_OPEN = "open"
STATUS_CLOSED = "closed"

SYMPTOM_MAX = 4000
FEATURE_KEY_MAX = 200


class Defect(Base, table=True):
    """One reported defect and its round counter."""

    __tablename__ = "defects"
    __table_args__ = (
        UniqueConstraint("feature_key", "symptom_key",
                         name="uq_defects_feature_symptom"),
        # An unattributed defect dedups within its session. A partial index
        # rather than a Python check because its sibling above already is one:
        # two rules of the same kind enforced at two different layers is how
        # one of them quietly stops holding. NULL never collides in SQLite, so
        # a row with neither feature nor session stays distinct — which is
        # correct, there is nothing for it to collide with.
        Index("uq_defects_session_symptom", "session_id", "symptom_key",
              unique=True, sqlite_where=text("feature_key IS NULL")),
        Index("idx_defects_fix_item", "fix_provider", "fix_external_key"),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    feature_key: Optional[str] = Field(
        default=None, sa_column=Column("feature_key", Text))
    symptom: str = Field(sa_column=Column("symptom", Text, nullable=False))
    # The dedup axis, kept separate from `symptom` so the reporter's own
    # wording survives verbatim while matching stays insensitive to case and
    # whitespace.
    symptom_key: str = Field(
        sa_column=Column("symptom_key", Text, nullable=False))
    session_id: Optional[str] = Field(
        default=None, sa_column=Column("session_id", Text))
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
    # The tracker item that will close this defect. Two plain columns, not a
    # join table and not a cached snapshot: `regin defects list` is an
    # on-demand read over a handful of rows, so a cache would buy nothing and
    # carry the staleness. At most one per defect; one item may serve many.
    # regin never writes the defect's status from it or its state from the
    # defect — `defects.status` asks *is this bug still real*, a tracker item
    # asks *is this work still scheduled*.
    fix_provider: Optional[str] = Field(
        default=None, sa_column=Column("fix_provider", Text))
    fix_external_key: Optional[str] = Field(
        default=None, sa_column=Column("fix_external_key", Text))
    # The tracker's raw `state_category` for `feature_key` at the instant that
    # key was FIRST set — write-once. An escape is `== 'completed'`. The raw
    # category rather than the derived stage name, so a later change to how
    # regin reads a category cannot silently reinterpret history. NULL means we
    # never looked; the string `'unknown'` means we looked and got a word we do
    # not carry. Neither is folded into the count.
    blamed_against_state: Optional[str] = Field(
        default=None, sa_column=Column("blamed_against_state", Text))

