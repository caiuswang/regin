"""Topic-proposal tables: runs, topics, feedback, snapshots, audits.

Phase A of the topic-proposal refactor introduces these four tables but
does not yet flip the source of truth — the on-disk graph stays
authoritative until Phase D. Existing accept/merge/replace start
writing `GraphSnapshot` + `TopicAudit(kind="provenance")` rows so
history and provenance are available before any reader migrates.

JSON-encoded columns (`*_json`, `aliases_json`, `refs_json`, etc.)
store payloads as `TEXT` rather than separate normalised tables; the
shapes are append-only inside a single row, never queried across rows.
The plan uses raw `Text` columns + Python json.dumps/loads on the
read/write sides for portability across the SQLite and MySQL dialects
already supported by the engine layer.

Naming notes:
- `metadata_json` rather than `metadata` — `metadata` collides with
  the SQLModel/SQLAlchemy class-level `Base.metadata` attribute.
- `bool` columns are stored as `INTEGER` (0/1) to keep parity with the
  rest of the schema where `is_*` flags are `INTEGER NOT NULL`.
"""

from __future__ import annotations

from typing import Optional

from sqlalchemy import UniqueConstraint, text
from sqlmodel import Column, Field, Integer, String, Text

from lib.orm.base import Base


class ProposalRun(Base, table=True):
    """One row per `/api/repos/<name>/topics/proposals` POST.

    `id` keeps the existing timestamp slug (e.g. `20260519T173635Z`)
    so the URL space and back-compat shims stay stable when the file
    backend goes away in Phase E.
    """

    __tablename__ = "proposal_runs"

    id: str = Field(sa_column=Column("id", String, primary_key=True))
    repo_id: int = Field(
        sa_column=Column("repo_id", Integer, nullable=False, index=True),
    )
    provider: str = Field(sa_column=Column("provider", String, nullable=False))
    scope: str = Field(
        sa_column=Column("scope", String, nullable=False,
                         server_default=text("'all'")),
    )
    state: str = Field(sa_column=Column("state", String, nullable=False))
    agent_id: Optional[str] = Field(default=None,
                                    sa_column=Column("agent_id", String))
    complexity: str = Field(
        sa_column=Column("complexity", String, nullable=False,
                         server_default=text("'standard'")),
    )
    started_at: str = Field(sa_column=Column("started_at", Text, nullable=False))
    completed_at: Optional[str] = Field(default=None,
                                        sa_column=Column("completed_at", Text))
    updated_at: Optional[str] = Field(
        default=None,
        sa_column=Column("updated_at", Text, nullable=False,
                         server_default=text("(datetime('now'))")),
    )
    error: Optional[str] = Field(default=None, sa_column=Column("error", Text))
    error_detail: Optional[str] = Field(default=None,
                                        sa_column=Column("error_detail", Text))
    prompt_template_slugs: str = Field(
        sa_column=Column("prompt_template_slugs", Text, nullable=False,
                         server_default=text("'[]'")),
    )
    evidence_hash: Optional[str] = Field(default=None,
                                         sa_column=Column("evidence_hash", String))
    # `regenerate_scope` captures what the last `/regenerate` call
    # touched: NULL on the initial run, "run" for a full re-draft,
    # "topic" for the Phase B per-topic regenerate.
    regenerate_scope: Optional[str] = Field(default=None,
                                            sa_column=Column("regenerate_scope", String))
    metadata_json: str = Field(
        sa_column=Column("metadata_json", Text, nullable=False,
                         server_default=text("'{}'")),
    )
    topic_request: Optional[str] = Field(default=None,
                                         sa_column=Column("topic_request", Text))


class ProposalTopic(Base, table=True):
    """One row per topic inside a proposal run.

    `topic_id` is the proposer-chosen id (may collide with an approved
    topic — Replace handles that). `accepted_topic_id`/`merged_topic_id`
    record where the topic landed if any review action was taken.
    """

    __tablename__ = "proposal_topics"

    id: Optional[int] = Field(default=None, primary_key=True)
    run_id: str = Field(
        sa_column=Column("run_id", String, nullable=False, index=True),
    )
    topic_id: str = Field(sa_column=Column("topic_id", String, nullable=False))
    label: str = Field(sa_column=Column("label", String, nullable=False))
    intent: str = Field(
        sa_column=Column("intent", Text, nullable=False, server_default=text("''")),
    )
    status: str = Field(
        sa_column=Column("status", String, nullable=False,
                         server_default=text("'active'")),
    )
    aliases_json: str = Field(
        sa_column=Column("aliases_json", Text, nullable=False,
                         server_default=text("'[]'")),
    )
    refs_json: str = Field(
        sa_column=Column("refs_json", Text, nullable=False,
                         server_default=text("'[]'")),
    )
    edges_json: str = Field(
        sa_column=Column("edges_json", Text, nullable=False,
                         server_default=text("'[]'")),
    )
    commands_json: str = Field(
        sa_column=Column("commands_json", Text, nullable=False,
                         server_default=text("'[]'")),
    )
    include_globs_json: str = Field(
        sa_column=Column("include_globs_json", Text, nullable=False,
                         server_default=text("'[]'")),
    )
    exclude_globs_json: str = Field(
        sa_column=Column("exclude_globs_json", Text, nullable=False,
                         server_default=text("'[]'")),
    )
    evidence_paths_json: str = Field(
        sa_column=Column("evidence_paths_json", Text, nullable=False,
                         server_default=text("'[]'")),
    )
    parent_id: Optional[str] = Field(default=None,
                                     sa_column=Column("parent_id", String))
    blurb: str = Field(
        sa_column=Column("blurb", Text, nullable=False,
                         server_default=text("''")),
    )
    # The topic's own wiki page body (its `.regin/topics/wiki/<id>.md`).
    # Authored per-topic by the drafting agent so each topic carries its
    # own narrative instead of the whole run sharing one combined doc.
    wiki_md: str = Field(
        sa_column=Column("wiki_md", Text, nullable=False,
                         server_default=text("''")),
    )
    source: Optional[str] = Field(default=None,
                                  sa_column=Column("source", String))
    review_status: Optional[str] = Field(default=None,
                                         sa_column=Column("review_status", String))
    accepted_topic_id: Optional[str] = Field(default=None,
                                             sa_column=Column("accepted_topic_id", String))
    accepted_at: Optional[str] = Field(default=None,
                                       sa_column=Column("accepted_at", Text))
    merged_topic_id: Optional[str] = Field(default=None,
                                           sa_column=Column("merged_topic_id", String))
    merged_at: Optional[str] = Field(default=None,
                                     sa_column=Column("merged_at", Text))
    ignored_at: Optional[str] = Field(default=None,
                                      sa_column=Column("ignored_at", Text))
    downgraded_from: Optional[str] = Field(default=None,
                                           sa_column=Column("downgraded_from", String))
    downgraded_at: Optional[str] = Field(default=None,
                                         sa_column=Column("downgraded_at", Text))
    replaced_existing: int = Field(
        sa_column=Column("replaced_existing", Integer, nullable=False,
                         server_default=text("0")),
    )


class ProposalRevision(Base, table=True):
    """Append-only revision history for one proposal run."""

    __tablename__ = "proposal_revisions"

    id: Optional[int] = Field(default=None, primary_key=True)
    run_id: str = Field(
        sa_column=Column("run_id", String, nullable=False, index=True),
    )
    revision_number: int = Field(
        sa_column=Column("revision_number", Integer, nullable=False),
    )
    parent_revision_id: Optional[int] = Field(
        default=None,
        sa_column=Column("parent_revision_id", Integer),
    )
    kind: str = Field(
        sa_column=Column("kind", String, nullable=False,
                         server_default=text("'generated'")),
    )
    wiki_md: str = Field(
        sa_column=Column("wiki_md", Text, nullable=False,
                         server_default=text("''")),
    )
    is_latest: int = Field(
        sa_column=Column("is_latest", Integer, nullable=False,
                         server_default=text("1"), index=True),
    )
    created_at: str = Field(sa_column=Column("created_at", Text, nullable=False))
    updated_at: str = Field(sa_column=Column("updated_at", Text, nullable=False))
    metadata_json: str = Field(
        sa_column=Column("metadata_json", Text, nullable=False,
                         server_default=text("'{}'")),
    )


class ProposalRevisionTopic(Base, table=True):
    """Topic payload snapshot for one proposal revision."""

    __tablename__ = "proposal_revision_topics"

    id: Optional[int] = Field(default=None, primary_key=True)
    revision_id: int = Field(
        sa_column=Column("revision_id", Integer, nullable=False, index=True),
    )
    topic_id: str = Field(sa_column=Column("topic_id", String, nullable=False))
    label: str = Field(sa_column=Column("label", String, nullable=False))
    intent: str = Field(
        sa_column=Column("intent", Text, nullable=False, server_default=text("''")),
    )
    status: str = Field(
        sa_column=Column("status", String, nullable=False,
                         server_default=text("'active'")),
    )
    aliases_json: str = Field(
        sa_column=Column("aliases_json", Text, nullable=False,
                         server_default=text("'[]'")),
    )
    refs_json: str = Field(
        sa_column=Column("refs_json", Text, nullable=False,
                         server_default=text("'[]'")),
    )
    edges_json: str = Field(
        sa_column=Column("edges_json", Text, nullable=False,
                         server_default=text("'[]'")),
    )
    commands_json: str = Field(
        sa_column=Column("commands_json", Text, nullable=False,
                         server_default=text("'[]'")),
    )
    include_globs_json: str = Field(
        sa_column=Column("include_globs_json", Text, nullable=False,
                         server_default=text("'[]'")),
    )
    exclude_globs_json: str = Field(
        sa_column=Column("exclude_globs_json", Text, nullable=False,
                         server_default=text("'[]'")),
    )
    evidence_paths_json: str = Field(
        sa_column=Column("evidence_paths_json", Text, nullable=False,
                         server_default=text("'[]'")),
    )
    parent_id: Optional[str] = Field(default=None,
                                     sa_column=Column("parent_id", String))
    blurb: str = Field(
        sa_column=Column("blurb", Text, nullable=False,
                         server_default=text("''")),
    )
    # Per-topic wiki page body — see ProposalTopic.wiki_md.
    wiki_md: str = Field(
        sa_column=Column("wiki_md", Text, nullable=False,
                         server_default=text("''")),
    )
    source: Optional[str] = Field(default=None,
                                  sa_column=Column("source", String))
    review_status: Optional[str] = Field(default=None,
                                         sa_column=Column("review_status", String))
    accepted_topic_id: Optional[str] = Field(default=None,
                                             sa_column=Column("accepted_topic_id", String))
    accepted_at: Optional[str] = Field(default=None,
                                       sa_column=Column("accepted_at", Text))
    merged_topic_id: Optional[str] = Field(default=None,
                                           sa_column=Column("merged_topic_id", String))
    merged_at: Optional[str] = Field(default=None,
                                     sa_column=Column("merged_at", Text))
    ignored_at: Optional[str] = Field(default=None,
                                      sa_column=Column("ignored_at", Text))
    downgraded_from: Optional[str] = Field(default=None,
                                           sa_column=Column("downgraded_from", String))
    downgraded_at: Optional[str] = Field(default=None,
                                         sa_column=Column("downgraded_at", Text))
    replaced_existing: int = Field(
        sa_column=Column("replaced_existing", Integer, nullable=False,
                         server_default=text("0")),
    )


class GraphSnapshot(Base, table=True):
    """Versioned snapshot of the approved graph for a repo.

    From Phase D, the row with `is_latest=1` IS the live graph; until
    then it's history-only and the on-disk graph stays authoritative.
    `apply_diff` enforces a single `is_latest=1` row per repo by flipping
    the prior one in the same transaction that inserts the new one.
    """

    __tablename__ = "graph_snapshots"

    id: Optional[int] = Field(default=None, primary_key=True)
    repo_id: int = Field(
        sa_column=Column("repo_id", Integer, nullable=False, index=True),
    )
    taken_at: str = Field(sa_column=Column("taken_at", Text, nullable=False))
    reason: str = Field(sa_column=Column("reason", String, nullable=False))
    triggering_run_id: Optional[str] = Field(default=None,
                                             sa_column=Column("triggering_run_id", String))
    triggering_proposal_topic_id: Optional[int] = Field(
        default=None,
        sa_column=Column("triggering_proposal_topic_id", Integer),
    )
    graph_json: str = Field(sa_column=Column("graph_json", Text, nullable=False))
    wiki_pages_json: str = Field(
        sa_column=Column("wiki_pages_json", Text, nullable=False,
                         server_default=text("'{}'")),
    )
    diff_summary_json: str = Field(
        sa_column=Column("diff_summary_json", Text, nullable=False,
                         server_default=text("'{}'")),
    )
    pinned: int = Field(
        sa_column=Column("pinned", Integer, nullable=False,
                         server_default=text("0")),
    )
    is_latest: int = Field(
        sa_column=Column("is_latest", Integer, nullable=False,
                         server_default=text("0"), index=True),
    )


class TopicAudit(Base, table=True):
    """Two-mode log: audit issues + per-operation provenance.

    `kind="audit"` rows are recomputed/replaced on demand by
    `audit_graph()`; `kind="provenance"` rows are append-only history
    tied to a specific `apply_diff` call. Codes are stable strings so
    the bulk-fix tool can match by code without parsing messages.
    """

    __tablename__ = "topic_audits"

    id: Optional[int] = Field(default=None, primary_key=True)
    repo_id: int = Field(
        sa_column=Column("repo_id", Integer, nullable=False, index=True),
    )
    kind: str = Field(sa_column=Column("kind", String, nullable=False))
    recorded_at: str = Field(
        sa_column=Column("recorded_at", Text, nullable=False,
                         server_default=text("(datetime('now'))")),
    )
    severity: str = Field(sa_column=Column("severity", String, nullable=False))
    code: str = Field(sa_column=Column("code", String, nullable=False))
    message: str = Field(sa_column=Column("message", Text, nullable=False))
    topic_ids_json: str = Field(
        sa_column=Column("topic_ids_json", Text, nullable=False,
                         server_default=text("'[]'")),
    )
    paths_json: str = Field(
        sa_column=Column("paths_json", Text, nullable=False,
                         server_default=text("'[]'")),
    )
    aliases_json: str = Field(
        sa_column=Column("aliases_json", Text, nullable=False,
                         server_default=text("'[]'")),
    )
    triggering_run_id: Optional[str] = Field(default=None,
                                             sa_column=Column("triggering_run_id", String))
    triggering_proposal_topic_id: Optional[int] = Field(
        default=None,
        sa_column=Column("triggering_proposal_topic_id", Integer),
    )
    snapshot_id: Optional[int] = Field(default=None,
                                       sa_column=Column("snapshot_id", Integer))
    fix_action: Optional[str] = Field(default=None,
                                      sa_column=Column("fix_action", String))


class ProposalFeedbackThread(Base, table=True):
    """One review thread attached to a proposal run.

    Anchors are optional and stored as JSON so the current run-based
    workflow can support GitHub-style sidebar comments now without
    waiting for the larger thread/revision schema split.
    """

    __tablename__ = "proposal_feedback_threads"

    id: Optional[int] = Field(default=None, primary_key=True)
    run_id: str = Field(
        sa_column=Column("run_id", String, nullable=False, index=True),
    )
    revision_id: Optional[int] = Field(
        default=None,
        sa_column=Column("revision_id", Integer, index=True),
    )
    proposal_topic_id: Optional[str] = Field(
        default=None,
        sa_column=Column("proposal_topic_id", String, index=True),
    )
    kind: str = Field(
        sa_column=Column("kind", String, nullable=False,
                         server_default=text("'comment'")),
    )
    anchor_kind: str = Field(
        sa_column=Column("anchor_kind", String, nullable=False,
                         server_default=text("'general'")),
    )
    anchor_json: str = Field(
        sa_column=Column("anchor_json", Text, nullable=False,
                         server_default=text("'{}'")),
    )
    quoted_text: Optional[str] = Field(default=None,
                                       sa_column=Column("quoted_text", Text))
    resolution_state: str = Field(
        sa_column=Column("resolution_state", String, nullable=False,
                         server_default=text("'open'")),
    )
    addressed_in_revision_id: Optional[int] = Field(
        default=None,
        sa_column=Column("addressed_in_revision_id", Integer),
    )
    created_by: str = Field(
        sa_column=Column("created_by", String, nullable=False,
                         server_default=text("'user'")),
    )
    created_at: str = Field(sa_column=Column("created_at", Text, nullable=False))
    updated_at: str = Field(sa_column=Column("updated_at", Text, nullable=False))
    metadata_json: str = Field(
        sa_column=Column("metadata_json", Text, nullable=False,
                         server_default=text("'{}'")),
    )


class ProposalFeedbackComment(Base, table=True):
    """One message inside a proposal feedback thread."""

    __tablename__ = "proposal_feedback_comments"

    id: Optional[int] = Field(default=None, primary_key=True)
    feedback_thread_id: int = Field(
        sa_column=Column("feedback_thread_id", Integer, nullable=False, index=True),
    )
    author_kind: str = Field(
        sa_column=Column("author_kind", String, nullable=False,
                         server_default=text("'user'")),
    )
    body: str = Field(sa_column=Column("body", Text, nullable=False))
    created_at: str = Field(sa_column=Column("created_at", Text, nullable=False))
    updated_at: str = Field(sa_column=Column("updated_at", Text, nullable=False))
    metadata_json: str = Field(
        sa_column=Column("metadata_json", Text, nullable=False,
                         server_default=text("'{}'")),
    )


class TopicRefDigest(Base, table=True):
    """A captured fingerprint of one topic ref file at wiki-write time.

    The substrate Phase 3 reads to decide a topic's wiki narrative has
    drifted from the code under it: a `sha256` of the ref file's content
    (always) plus an optional embedding (when an embedder is supplied at
    capture). One row per `(repo_id, topic_id, path)` — re-capturing an
    unchanged file is an idempotent upsert (the hash is stable), so the
    table tracks the live tree without churn.

    Lives in the ORM DB alongside `graph_snapshots`/`proposal_*` (NOT the
    separate memory DB), so it must stay mirrored across the schema
    authorities: this model and `db/schema.sql` — plus a new alembic
    revision under `alembic/versions/` when a column is added.
    """

    __tablename__ = "topic_ref_digests"
    __table_args__ = (
        UniqueConstraint("repo_id", "topic_id", "path",
                         name="uq_topic_ref_digest"),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    repo_id: int = Field(
        sa_column=Column("repo_id", Integer, nullable=False, index=True),
    )
    topic_id: str = Field(
        sa_column=Column("topic_id", String, nullable=False, index=True),
    )
    path: str = Field(sa_column=Column("path", String, nullable=False))
    role: Optional[str] = Field(default=None,
                                sa_column=Column("role", String))
    content_hash: str = Field(
        sa_column=Column("content_hash", String, nullable=False))
    embedding_json: Optional[str] = Field(
        default=None, sa_column=Column("embedding_json", Text))
    embedding_model_id: Optional[str] = Field(
        default=None, sa_column=Column("embedding_model_id", String))
    captured_at: str = Field(
        sa_column=Column("captured_at", Text, nullable=False))


__all__ = [
    "ProposalRun", "ProposalTopic",
    "ProposalRevision", "ProposalRevisionTopic",
    "ProposalFeedbackThread", "ProposalFeedbackComment",
    "GraphSnapshot", "TopicAudit", "TopicRefDigest",
]
