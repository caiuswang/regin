"""A feature, its lifecycle, and which session built it (regin:82).

A feature is a **row with a primary key**, not a string that five tables
happened to agree on. Before this, membership of the population was a union of
`session_features.feature_key`, `defects.feature_key`,
`agent_task_links.external_key`, the tasks something named as parent, and a
top-level task somebody pressed Run on — so four surfaces answered "what counts
as a feature" four different ways, and a feature could be shipped without
appearing in the list at all.

**The lifecycle is an append-only event log.** `rounds`, `claimed_at`, `stage`
and the escape verdict are all queries over `feature_events`, so a counter and
the fact it counts have no way to drift: undoing a ship is a `restarted` row,
never a column reset, and the history that produced a number survives the
number changing.

`SessionFeature` keeps the shape it had — a join table rather than a column on
`sessions`, because a column cannot hold two features and sessions that name
two already exist. **An unattributed session is the absence of a row**, never a
sentinel: nothing infers an attribution from a cwd, a branch name or a clock.
"""

from __future__ import annotations

from typing import Optional

from sqlalchemy import Index
from sqlmodel import (
    Column, Field, ForeignKey, Integer, LargeBinary, String, Text,
    UniqueConstraint, text,
)

from lib.orm.base import Base

DECLARED = "declared"
RUN = "run"
RECALLED = "recalled"
#: `run` is derived at read time from `run → task → feature` rather than
#: written here — the run is the live assertion, and copying it into a row
#: would make the ledger a stale second opinion. The value is part of the
#: column's domain because a ledger row carries one of the three.
PROVENANCES = (DECLARED, RUN, RECALLED)

ORIGIN_NATIVE = "native"

TITLE_MAX = 200

# ── the lifecycle vocabulary ──────────────────────────────────
#
# Defined on the model rather than in `lib/features/events.py` so that
# `lib/defects/escapes.py` can compare against `SHIPPED` without importing the
# module that imports it.

SPECCED = "specced"
BUILD_STARTED = "build_started"
SHIPPED = "shipped"
RESTARTED = "restarted"
ABANDONED = "abandoned"
KINDS: tuple[str, ...] = (SPECCED, BUILD_STARTED, SHIPPED, RESTARTED,
                          ABANDONED)

#: Who declared an event. `operator` is a human act; a run and a remote sync
#: sign themselves `run:<id>` and `sync:<provider>` so every state change is
#: attributable to something that happened.
OPERATOR = "operator"


class Feature(Base, table=True):
    """One feature: what is being delivered, and where it is tracked.

    `external_ref` carries **no referential integrity requirement**. A remote
    issue somebody deleted leaves this row perfectly valid — it is a record of
    what was built here, not a mirror of somebody else's database.

    It absorbs the whole of the old `issue_cache` table, which held
    `(provider, external_key, snapshot_json, synced_at)`: title, url,
    `sync_error` and `synced_at` each get a column, and the state history that
    was the only other thing read out of the snapshot now lives in
    `feature_events`.
    """

    __tablename__ = "features"
    __table_args__ = (
        UniqueConstraint("origin", "external_ref",
                         name="uq_features_origin_ref"),
        {"sqlite_autoincrement": True})

    id: Optional[int] = Field(default=None, primary_key=True)
    #: Highest image `idx` ever issued to this row. Survives a detach, so a
    #: freed index is never handed to different bytes.
    image_high_water: int = Field(
        default=0,
        sa_column=Column("image_high_water", Integer, nullable=False,
                         server_default=text("0")))
    title: str = Field(sa_column=Column("title", Text, nullable=False))
    body: Optional[str] = Field(default=None, sa_column=Column("body", Text))
    #: Extensible rather than a boolean: the provider registry decides the
    #: legal values, and a third tracker must not need a column added.
    origin: str = Field(
        default=ORIGIN_NATIVE,
        sa_column=Column("origin", String, nullable=False,
                         server_default=text("'native'")))
    external_ref: Optional[str] = Field(
        default=None, sa_column=Column("external_ref", String))
    external_url: Optional[str] = Field(
        default=None, sa_column=Column("external_url", Text))
    #: The last reason a fetch failed. It never clears `external_ref`: a
    #: network blip does not undo an operator's assertion about where this
    #: feature is tracked.
    sync_error: Optional[str] = Field(
        default=None, sa_column=Column("sync_error", Text))
    #: NULL forever on a native feature — there is no remote to be as-of.
    synced_at: Optional[str] = Field(
        default=None, sa_column=Column("synced_at", String))
    created_at: Optional[str] = Field(
        default=None,
        sa_column=Column("created_at", String, nullable=False,
                         server_default=text("(datetime('now'))")))
    #: The registered repo this feature belongs to. Declared, never inferred:
    #: nothing derives it from `session_repos`, a task's project, a suspect
    #: path or a cwd, so `None` reads as "in no project" — a scope you can ask
    #: for — rather than "we have not worked it out yet".
    project_id: Optional[int] = Field(
        default=None, sa_column=Column("project_id", Integer, index=True))
    #: The integration branch, operator-set. `None` defers to the
    #: `f{id}/integration` convention, resolved against git on read.
    branch: Optional[str] = Field(
        default=None, sa_column=Column("branch", Text))


class FeatureEvent(Base, table=True):
    """One declared step of a feature's life. Append-only.

    Never UPDATEd and never DELETEd, which is what makes `rounds` recomputable
    and the escape verdict auditable. `at` is for display only: the total order
    is `id`, because two events written in one transaction can share a
    timestamp and a clock-ordered read would make a ship-then-restart depend on
    timestamp precision.
    """

    __tablename__ = "feature_events"
    __table_args__ = {"sqlite_autoincrement": True}

    id: Optional[int] = Field(default=None, primary_key=True)
    feature_id: int = Field(
        sa_column=Column("feature_id", Integer,
                         ForeignKey("features.id", ondelete="CASCADE"),
                         nullable=False, index=True))
    kind: str = Field(sa_column=Column("kind", String, nullable=False))
    #: NOT NULL, so there is no unowned state change.
    declared_by: str = Field(
        sa_column=Column("declared_by", String, nullable=False))
    trace_id: Optional[str] = Field(
        default=None, sa_column=Column("trace_id", String))
    at: Optional[str] = Field(
        default=None,
        sa_column=Column("at", String, nullable=False,
                         server_default=text("(datetime('now'))")))


class SessionFeature(Base, table=True):
    """One session's declaration that it was building one feature."""

    __tablename__ = "session_features"

    trace_id: str = Field(
        sa_column=Column("trace_id", String, primary_key=True))
    feature_id: int = Field(
        sa_column=Column("feature_id", Integer,
                         ForeignKey("features.id", ondelete="CASCADE"),
                         primary_key=True))
    is_primary: int = Field(
        sa_column=Column("is_primary", Integer, nullable=False,
                         server_default=text("0")))
    provenance: str = Field(
        default=DECLARED,
        sa_column=Column("provenance", Text, nullable=False,
                         server_default=text("'declared'")))
    # The round the feature stood at the instant the claim was made. Unlike
    # the tracker-fetched value it replaces, this is always answerable — it is
    # a count of local events — so it is NULL only on a `recalled` row, which
    # has no such instant to snapshot.
    round: Optional[int] = Field(
        default=None, sa_column=Column("round", Integer))
    created_at: Optional[str] = Field(
        default=None,
        sa_column=Column("created_at", Text, nullable=False,
                         server_default=text("(datetime('now'))")))


class FeatureImage(Base, table=True):
    """An image attached to a feature.

    The mirror of `AgentTaskImage` one level up, and kept a separate table
    rather than a nullable owner column on that one for the reason the two
    differ in: a task's image is delivered with that task alone, while a
    feature's is delivered with every slice cut from it. Folding both into one
    table would put two owners on one column and make "whose image is this"
    a join away from ambiguous.

    `idx` is a per-feature display and delivery order, allocated by the
    writer and never reissued once freed.
    """

    __tablename__ = "feature_images"
    __table_args__ = (UniqueConstraint("feature_id", "idx",
                                       name="uq_feature_images_idx"),
                      {"sqlite_autoincrement": True})

    id: Optional[int] = Field(default=None, primary_key=True)
    feature_id: int = Field(
        sa_column=Column("feature_id", Integer, nullable=False, index=True))
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


LINK_FOLLOWS = "follows"
LINK_RELATED = "related"
LINK_KINDS: tuple[str, ...] = (LINK_FOLLOWS, LINK_RELATED)


class FeatureLink(Base, table=True):
    """A peer relation between two features — never a parent/child tree.

    `follows`: `feature_id` is the later phase, `other_id` the earlier one.
    `related` is symmetric, so it is stored once with `feature_id < other_id`.
    """

    __tablename__ = "feature_links"
    __table_args__ = (UniqueConstraint("feature_id", "other_id", "kind",
                                       name="uq_feature_links"),
                      Index("idx_feature_links_other", "other_id"),
                      {"sqlite_autoincrement": True})

    id: Optional[int] = Field(default=None, primary_key=True)
    feature_id: int = Field(
        sa_column=Column("feature_id", Integer,
                         ForeignKey("features.id", ondelete="CASCADE"),
                         nullable=False))
    other_id: int = Field(
        sa_column=Column("other_id", Integer,
                         ForeignKey("features.id", ondelete="CASCADE"),
                         nullable=False))
    kind: str = Field(sa_column=Column("kind", Text, nullable=False))
    created_at: Optional[str] = Field(
        default=None,
        sa_column=Column("created_at", Text, nullable=False,
                         server_default=text("(datetime('now'))")))
