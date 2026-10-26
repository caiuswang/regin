"""A conversation about something — `comment_threads` and `comments`.

Generic from the first row, with a polymorphic subject. `ProposalFeedbackThread`
is this same shape welded to one subject (a proposal revision, by three FK
columns); widening *it* would have made every one of its readers carry a kind
column that has no second value, so the second subject gets its own pair of
tables and the proposal ones are left untouched.

`subject_id` is `TEXT` because a subject id is opaque here — this table never
resolves it — and no foreign key points at the subject for the same reason. A
task's own delete path removes its threads; `comments.thread_id` cascades.

`resolution_state` answers only *has this conversation been dealt with*.
Whether the ticket is closed is the task's own `status`, and a column carrying
both would be two axes on one.
"""

from __future__ import annotations

from typing import Optional

from sqlmodel import (
    Column, Field, ForeignKey, Integer, LargeBinary, String, Text,
    UniqueConstraint,
)
from sqlalchemy import text

from lib.orm.base import Base

SUBJECT_TASK = "task"

RESOLUTION_OPEN = "open"
RESOLUTION_RESOLVED = "resolved"
RESOLUTION_STATES: tuple[str, ...] = (RESOLUTION_OPEN, RESOLUTION_RESOLVED)

AUTHOR_USER = "user"
AUTHOR_AGENT = "agent"
AUTHOR_KINDS: tuple[str, ...] = (AUTHOR_USER, AUTHOR_AGENT)

COMMENT_BODY_MAX = 8000


class CommentThread(Base, table=True):
    """One conversation, hanging off whatever `(subject_kind, subject_id)`
    names."""

    __tablename__ = "comment_threads"
    __table_args__ = {"sqlite_autoincrement": True}

    id: Optional[int] = Field(default=None, primary_key=True)
    subject_kind: str = Field(
        sa_column=Column("subject_kind", String, nullable=False))
    subject_id: str = Field(
        sa_column=Column("subject_id", String, nullable=False))
    resolution_state: str = Field(
        default=RESOLUTION_OPEN,
        sa_column=Column("resolution_state", String, nullable=False,
                         server_default=text("'open'")))
    # NULL for a thread an agent opened. A provenance label, not a join.
    created_by: Optional[int] = Field(
        default=None, sa_column=Column("created_by", Integer))
    created_at: str = Field(
        sa_column=Column("created_at", String, nullable=False))
    updated_at: str = Field(
        sa_column=Column("updated_at", String, nullable=False))


class Comment(Base, table=True):
    """One message in a flat thread.

    `author_kind` and `author_id` are two axes rather than one nullable
    column: an agent comment carries a kind and a NULL id, which a lone author
    column could not tell apart from a user regin lost track of.
    """

    __tablename__ = "comments"
    __table_args__ = {"sqlite_autoincrement": True}

    id: Optional[int] = Field(default=None, primary_key=True)
    thread_id: int = Field(
        sa_column=Column("thread_id", Integer,
                         ForeignKey("comment_threads.id", ondelete="CASCADE"),
                         nullable=False, index=True))
    author_kind: str = Field(
        default=AUTHOR_USER,
        sa_column=Column("author_kind", String, nullable=False,
                         server_default=text("'user'")))
    author_id: Optional[int] = Field(
        default=None, sa_column=Column("author_id", Integer))
    body: str = Field(sa_column=Column("body", Text, nullable=False))
    #: Highest image `idx` ever issued to this comment. Comments are written
    #: once, so it only ever equals the max of what the post carried — it is
    #: here because `ImageSet` keys its never-reissue rule off the owner row.
    image_high_water: int = Field(
        default=0,
        sa_column=Column("image_high_water", Integer, nullable=False,
                         server_default=text("0")))
    created_at: str = Field(
        sa_column=Column("created_at", String, nullable=False))
    updated_at: str = Field(
        sa_column=Column("updated_at", String, nullable=False))


class CommentImage(Base, table=True):
    """A picture posted with one comment.

    Its own table rather than the task's: `![](image:N)` in a comment resolves
    only among that comment's images, so the owner has to be the comment — on
    the task's table a comment's `image:1` and the task body's `image:1`
    would be one row.
    """

    __tablename__ = "comment_images"
    __table_args__ = (UniqueConstraint("comment_id", "idx",
                                       name="uq_comment_images_idx"),
                      {"sqlite_autoincrement": True})

    id: Optional[int] = Field(default=None, primary_key=True)
    comment_id: int = Field(
        sa_column=Column("comment_id", Integer,
                         ForeignKey("comments.id", ondelete="CASCADE"),
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
