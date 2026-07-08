"""SQLModel models for the agent-memory engine.

These models declare their **own** `MemoryBase` / `MetaData`, deliberately
separate from `lib.orm.base.Base`. The shared regin metadata is what
`create_all` and Alembic walk — keeping memory tables off it is what makes
the memory DB self-initializing (`create_all(memory_engine)` builds only
memory tables) and keeps them out of `db/schema.sql`, so `regin init` /
`rebuild` can never wipe accumulated experience.

A single `memories` table carries both tiers via the `tier` column
(mnemopi's two physical tables collapsed into one + a state column; the
two-tier *concept* — raw `working` rows consolidated into `episodic` by
`reflect()` — is preserved). Vectors live in the `memory_embeddings` side
table so the hot row stays lean; corrections land in `memory_validations`.
Lexical recall runs over the `memories_fts` FTS5 virtual table, created by
raw DDL in `lib.memory.engine` (FTS5 can't be declared via SQLModel).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from sqlalchemy import Float, MetaData, text
from sqlmodel import Column, Field, Integer, LargeBinary, SQLModel, String, Text


class MemoryBase(SQLModel):
    """Base for memory tables only. Its metadata is the unit the memory
    engine's `create_all` targets — never merged into regin's.

    The explicit `MetaData()` assignment is load-bearing: subclassing
    SQLModel does NOT fork the metadata — without this line every memory
    table would register on the global `SQLModel.metadata` that
    `lib.orm.base.Base` shares, and `create_all(memory_engine)` would
    build all of regin's tables into the memory file (and Alembic would
    see memory tables). Covered by
    `tests/memory/test_store.py::test_memory_tables_stay_off_regin_metadata`.
    """

    metadata = MetaData()


memory_metadata = MemoryBase.metadata


MEMORY_TIERS: tuple[str, ...] = ("working", "episodic")
# The first five align with the user file-memory taxonomy so a future
# MemorySink export is lossless across the two vocabularies. `digest` is
# regin-internal and outside that taxonomy: a single maintained per-scope
# briefing written by reflect()'s digest stage, excluded from similarity
# recall (it is standing context, read by scope) — see _eligible_rows.
MEMORY_KINDS: tuple[str, ...] = (
    "lesson", "gotcha", "preference", "fact", "procedure", "digest",
)
# `proposed` extends the design's active|retired pair: implicit captures
# (post-session distill) land as proposals awaiting human approval rather
# than silent writes, mirroring the topic-proposal flow.
MEMORY_STATUSES: tuple[str, ...] = ("active", "proposed", "retired")
VERACITY_VALUES: tuple[str, ...] = ("true", "false", "unknown")

DEFAULT_KIND = "lesson"
DEFAULT_TIER = "working"
DEFAULT_STATUS = "active"

# Provenance tags stamped at write time. `DISTILL_TAG` marks rows produced
# by the post-session distiller; `SEND_TO_USER_TAG` marks `send_to_user(
# type=lesson)` captures. Both write paths stamp the session id as
# `source_trace_id`, so the distill idempotency guard counts only rows
# bearing `DISTILL_TAG` — otherwise a session that merely emitted a lesson
# would be falsely flagged as already distilled.
DISTILL_TAG = "distill"
SEND_TO_USER_TAG = "send_to_user"


class Memory(MemoryBase, table=True):
    """One unit of cross-session experience."""

    __tablename__ = "memories"

    id: str = Field(sa_column=Column("id", String, primary_key=True))
    tier: str = Field(
        sa_column=Column("tier", String, nullable=False,
                         server_default=text("'working'")))
    kind: str = Field(
        sa_column=Column("kind", String, nullable=False,
                         server_default=text("'lesson'")))
    title: Optional[str] = Field(default=None, sa_column=Column("title", Text))
    body: str = Field(sa_column=Column("body", Text, nullable=False))
    # 'global' or 'repo:<name>' — assigned by the scoping wrapper, never
    # interpreted by the engine beyond equality filtering.
    scope: str = Field(
        sa_column=Column("scope", String, nullable=False,
                         server_default=text("'global'")))
    tags: Optional[str] = Field(default=None, sa_column=Column("tags", Text))
    importance: float = Field(
        default=0.5,
        sa_column=Column("importance", Float, nullable=False,
                         server_default=text("0.5")))
    veracity: str = Field(
        sa_column=Column("veracity", String, nullable=False,
                         server_default=text("'unknown'")))

    # Provenance: deep-links back into the trace that taught this.
    source_trace_id: Optional[str] = Field(
        default=None, sa_column=Column("source_trace_id", String))
    source_span_id: Optional[str] = Field(
        default=None, sa_column=Column("source_span_id", String))
    source_agent_id: Optional[str] = Field(
        default=None, sa_column=Column("source_agent_id", String))

    recall_count: int = Field(
        default=0,
        sa_column=Column("recall_count", Integer, nullable=False,
                         server_default=text("0")))
    last_recalled: Optional[str] = Field(
        default=None, sa_column=Column("last_recalled", Text))
    valid_until: Optional[str] = Field(
        default=None, sa_column=Column("valid_until", Text))
    superseded_by: Optional[str] = Field(
        default=None, sa_column=Column("superseded_by", String))
    status: str = Field(
        sa_column=Column("status", String, nullable=False,
                         server_default=text("'active'")))
    is_test: int = Field(
        default=0,
        sa_column=Column("is_test", Integer, nullable=False,
                         server_default=text("0")))
    consolidated_at: Optional[str] = Field(
        default=None, sa_column=Column("consolidated_at", Text))
    created_at: str = Field(
        sa_column=Column("created_at", Text, nullable=False))
    updated_at: str = Field(
        sa_column=Column("updated_at", Text, nullable=False))


class MemoryEmbedding(MemoryBase, table=True):
    """Dense vector for one memory; absent rows degrade recall to FTS."""

    __tablename__ = "memory_embeddings"

    memory_id: str = Field(
        sa_column=Column("memory_id", String, primary_key=True))
    model_id: str = Field(
        sa_column=Column("model_id", String, nullable=False))
    dim: int = Field(sa_column=Column("dim", Integer, nullable=False))
    vector: bytes = Field(
        sa_column=Column("vector", LargeBinary, nullable=False))
    content_hash: str = Field(
        sa_column=Column("content_hash", String, nullable=False))
    updated_at: str = Field(
        sa_column=Column("updated_at", Text, nullable=False))


class MemoryValidation(MemoryBase, table=True):
    """Correction log behind veracity / supersede / approval decisions."""

    __tablename__ = "memory_validations"

    id: Optional[int] = Field(default=None, primary_key=True)
    memory_id: str = Field(
        sa_column=Column("memory_id", String, nullable=False, index=True))
    # Who judged: 'user', 'reflect', 'distill', an agent id, …
    validator: str = Field(
        sa_column=Column("validator", String, nullable=False))
    # What happened: 'approved', 'retired', 'superseded', 'merged',
    # 'veracity_true', 'veracity_false', …
    action: str = Field(sa_column=Column("action", String, nullable=False))
    note: Optional[str] = Field(default=None, sa_column=Column("note", Text))
    created_at: str = Field(
        sa_column=Column("created_at", Text, nullable=False))


class MemoryPairCheck(MemoryBase, table=True):
    """The JUDGED-pair ledger: a canonical memory pair (``a_id`` < ``b_id``)
    a reflect dream actually ruled on, with its verdict. The pack generator
    consults it so a judged pair is never re-bought; a pair merely offered
    but left unjudged carries no row and re-presents next run — retry by
    design, not a leak.

    Deliberately NOT stored in ``memory_validations``: that log is trimmed
    to the last few rows per memory at write time, so any marker kept there
    self-evicts and the dream would re-buy the same judgments forever.
    Rows are GC'd when either memory stops resolving to an active row
    (``Store.prune_pair_checks``) and cascade-deleted with the memory in
    ``Store.forget``."""

    __tablename__ = "memory_pair_checks"

    a_id: str = Field(sa_column=Column("a_id", String, primary_key=True))
    b_id: str = Field(sa_column=Column("b_id", String, primary_key=True))
    verdict: str = Field(
        sa_column=Column("verdict", String, nullable=False))
    checked_at: str = Field(
        sa_column=Column("checked_at", Text, nullable=False))


class InjectionEvent(MemoryBase, table=True):
    """One auto-injected memory in one session.

    The UserPromptSubmit recall hook is a fresh short-lived process per
    prompt, so it has no in-memory record of what it injected earlier in
    the same session. This table is that record, and answers two
    cross-prompt questions: which memories were already injected this
    session (same-session dedup), and whether a memory has been reinforced
    for re-surfacing yet (`reinforced_at` — so a memory that keeps matching
    later prompts is reinforced at most once per session). Keyed by
    (session_id, memory_id)."""

    __tablename__ = "injection_events"

    session_id: str = Field(
        sa_column=Column("session_id", String, primary_key=True))
    memory_id: str = Field(
        sa_column=Column("memory_id", String, primary_key=True))
    injected_at: str = Field(
        sa_column=Column("injected_at", Text, nullable=False))
    # The recall query (user prompt text) this memory was injected on, kept for
    # provenance and engagement-feedback scoring of the inject. NULL for rows
    # written before the column existed / when the hook had no query to record.
    query: Optional[str] = Field(
        default=None, sa_column=Column("query", Text))
    reinforced_at: Optional[str] = Field(
        default=None, sa_column=Column("reinforced_at", Text))
    # Engagement verdict, stamped once per event by `feedback` (grade-time or
    # the reflect-time pending sweep): 1 = a referent of this memory appeared
    # in a tool span that fired after it was injected; 0 = injected but
    # ignored; NULL = not yet scored. Lives on the (uncapped) event row rather
    # than the capped validation log so `Store.engagement_counts` can compute
    # an accurate per-memory engaged-rate. `scored_at` makes the sweep
    # idempotent — a scored event is never re-judged.
    engaged: Optional[int] = Field(
        default=None, sa_column=Column("engaged", Integer))
    # Pre-idf match bit, stamped alongside `engaged`: 1 = at least one of the
    # memory's referents appeared in a post-injection span (the work touched
    # what the memory named, even if only via a corpus-common referent);
    # 0 = nothing matched; NULL = unscored / abstained. Lets the decay gate
    # tell a *soft* ignore (engaged=0, matched=1 — generic contact, no idf
    # credit, but not evidence of uselessness) from a *hard* ignore
    # (engaged=0, matched=0 — the memory's referents never showed up at all).
    matched: Optional[int] = Field(
        default=None, sa_column=Column("matched", Integer))
    scored_at: Optional[str] = Field(
        default=None, sa_column=Column("scored_at", Text))


class TopicInjection(MemoryBase, table=True):
    """One auto-injected `<topic_context>` banner in one session — the
    topic-routing analog of `InjectionEvent`.

    The recall hook routes a prompt through the authoritative topic graph and
    prepends a pointer-only banner, but until this table it forgot it had done
    so, leaving topic routing with *no* feedback loop. This row is that record.
    The `InjectedRelated` grade aspect (an LLM judge on whether the injected
    context actually fit the user's goal) stamps `relevance` once per session;
    aggregated per topic, a route that recurringly earns `fail` is withheld by
    the recall hook's `_route_topic` suppression gate — the topic analog of the
    chronically-ignored memory decay. Keyed by (session_id, topic_id)."""

    __tablename__ = "topic_injections"

    session_id: str = Field(
        sa_column=Column("session_id", String, primary_key=True))
    topic_id: str = Field(
        sa_column=Column("topic_id", String, primary_key=True))
    # The recall query (user prompt) this banner was routed on, kept so a
    # `fail` verdict can become a topic negative exemplar (`TopicNegative`).
    # NULL for rows written before the column existed.
    query: Optional[str] = Field(
        default=None, sa_column=Column("query", Text))
    injected_at: str = Field(
        sa_column=Column("injected_at", Text, nullable=False))
    # Relevance verdict from the `InjectedRelated` grade aspect, stamped once
    # per session by the grader: 'satisfied' | 'needs_revision' | 'fail', or
    # NULL until a grade carrying that aspect runs. `scored_at` makes the stamp
    # idempotent — a scored injection is never re-judged.
    relevance: Optional[str] = Field(
        default=None, sa_column=Column("relevance", Text))
    scored_at: Optional[str] = Field(
        default=None, sa_column=Column("scored_at", Text))


class TopicRouteDecision(MemoryBase, table=True):
    """A human's standing routing decision for one topic — the gate that makes
    suppression human-in-the-loop, like the `proposed → approved` flow every
    other memory write goes through.

    The fail-rate threshold (`topic_relevance_*` settings) only *proposes* a
    suppression — a topic over the bar shows as `proposed` but keeps routing.
    Withholding happens only when a human writes a `suppressed` decision here;
    `allowed` pins a route on (rejecting a proposal or vetoing a future one),
    and *no row* means `auto` (routes, re-proposable). Keyed by topic_id."""

    __tablename__ = "topic_route_decisions"

    topic_id: str = Field(
        sa_column=Column("topic_id", String, primary_key=True))
    # 'suppressed' = human-approved withholding; 'allowed' = human-pinned on.
    decision: str = Field(
        sa_column=Column("decision", Text, nullable=False))
    note: Optional[str] = Field(default=None, sa_column=Column("note", Text))
    decided_at: str = Field(
        sa_column=Column("decided_at", Text, nullable=False))


class MemoryEdge(MemoryBase, table=True):
    """An associative link between two memories.

    Harvested by ``reflect()`` from the embedding-cosine neighbour graph it
    already computes for synthesis clustering and otherwise throws away. A
    ``related`` edge is undirected and stored canonically (``src_id`` < ``dst_id``)
    so a pair yields exactly one row; reflect rebuilds the whole ``related``
    set every pass, so the graph never drifts from the live embeddings.
    Persisting it lets the curate UI render structure and lets recall expand
    one hop without recomputing cosine per request."""

    __tablename__ = "memory_edges"

    id: Optional[int] = Field(default=None, primary_key=True)
    src_id: str = Field(
        sa_column=Column("src_id", String, nullable=False, index=True))
    dst_id: str = Field(
        sa_column=Column("dst_id", String, nullable=False, index=True))
    kind: str = Field(
        sa_column=Column("kind", String, nullable=False,
                         server_default=text("'related'")))
    weight: float = Field(
        default=0.0,
        sa_column=Column("weight", Float, nullable=False,
                         server_default=text("0.0")))
    created_at: str = Field(
        sa_column=Column("created_at", Text, nullable=False))
    updated_at: str = Field(
        sa_column=Column("updated_at", Text, nullable=False))


class MemoryTopic(MemoryBase, table=True):
    """A named cluster of related memories — the grouping the flat
    ``memories`` table lacks.

    Created by ``reflect()``'s synthesis step: when it abstracts a higher-order
    rule from a cosine cluster, the cluster becomes a topic node named by the
    LLM, with the synthesised rule kept as its ``summary_memory_id`` card.
    Members are tracked in ``memory_topic_members`` (many-to-many)."""

    __tablename__ = "memory_topics"

    id: str = Field(sa_column=Column("id", String, primary_key=True))
    name: str = Field(sa_column=Column("name", Text, nullable=False))
    summary: Optional[str] = Field(
        default=None, sa_column=Column("summary", Text))
    summary_memory_id: Optional[str] = Field(
        default=None, sa_column=Column("summary_memory_id", String))
    scope: str = Field(
        sa_column=Column("scope", String, nullable=False,
                         server_default=text("'global'")))
    member_count: int = Field(
        default=0,
        sa_column=Column("member_count", Integer, nullable=False,
                         server_default=text("0")))
    status: str = Field(
        sa_column=Column("status", String, nullable=False,
                         server_default=text("'active'")))
    created_at: str = Field(
        sa_column=Column("created_at", Text, nullable=False))
    updated_at: str = Field(
        sa_column=Column("updated_at", Text, nullable=False))


class MemoryTopicMember(MemoryBase, table=True):
    """One memory's membership in one topic (a memory can join several)."""

    __tablename__ = "memory_topic_members"

    topic_id: str = Field(
        sa_column=Column("topic_id", String, primary_key=True))
    memory_id: str = Field(
        sa_column=Column("memory_id", String, primary_key=True))
    added_at: str = Field(
        sa_column=Column("added_at", Text, nullable=False))


class MemoryAuthoritativeTopic(MemoryBase, table=True):
    """A memory's link to a node in the *authoritative* topic graph
    (``.regin/topics/topic.json``), keyed by that node's string id.

    Distinct from ``MemoryTopicMember``: that ties a memory to an emergent
    ``memory_topics`` cluster (minted by ``reflect()``); this ties it to a
    human-approved topic node. The two graphs live in different SQLite DBs,
    so the link is by **string id**, not a foreign key. ``source`` records
    how the link was made: ``'manual'`` (curated), ``'route'`` (keyword
    match at recall/capture time), or ``'reflect'`` (synthesis proposal
    accepted)."""

    __tablename__ = "memory_authoritative_topics"

    memory_id: str = Field(
        sa_column=Column("memory_id", String, primary_key=True))
    topic_node_id: str = Field(
        sa_column=Column("topic_node_id", String, primary_key=True))
    source: str = Field(
        sa_column=Column("source", String, nullable=False,
                         server_default=text("'manual'")))
    added_at: str = Field(
        sa_column=Column("added_at", Text, nullable=False))


class TopicWikiRecall(MemoryBase, table=True):
    """Usage counter for a per-topic wiki (`.regin/topics/wiki/<id>.md`),
    keyed by the authoritative topic node's string id — the wiki analog of
    `Memory.recall_count`. Lives in the memory DB (not `topic.json`, which
    proposals rewrite wholesale) and bridges to the topics graph by string
    id, like `MemoryAuthoritativeTopic`.

    `signal` keeps the two non-interchangeable events orthogonal:
    ``'exposure'`` (index_fetch surfaced the path) vs ``'read'`` (the agent
    actually Read the file, reconstructed from the trace). v0 writes only
    ``'exposure'`` — honestly labeled, since a fetch is not a read."""

    __tablename__ = "topic_wiki_recalls"

    topic_id: str = Field(
        sa_column=Column("topic_id", String, primary_key=True))
    signal: str = Field(
        sa_column=Column("signal", String, primary_key=True,
                         server_default=text("'exposure'")))
    recall_count: int = Field(
        sa_column=Column("recall_count", Integer, nullable=False,
                         server_default=text("0")))
    last_recalled: Optional[str] = Field(
        default=None, sa_column=Column("last_recalled", Text))


class ReferentSessionDF(MemoryBase, table=True):
    """Cached session-span document frequency for one memory referent: how
    many distinct sessions have at least one tool span whose text contains
    `referent`. Backs the idf-weighted engagement verdict (`lib.memory.feedback`).

    The corpus that matters for engagement is *session ubiquity*, not memory
    ubiquity: a referent like `cli/regin.py` shows up in most sessions whether
    or not the memory steered them, so its reappearance downstream is weak
    evidence; a referent like `_find_state_evidence` appears only when the work
    truly touched it. Computing this means scanning the (large) trace DB, so it
    is precomputed by `feedback.rebuild_session_referent_df` (on the reflect
    sweep) and only read at scoring time. `corpus_sessions` is the N at compute
    time, repeated on every row so the reader gets it without a second query."""

    __tablename__ = "referent_session_df"

    referent: str = Field(
        sa_column=Column("referent", String, primary_key=True))
    df: int = Field(sa_column=Column("df", Integer, nullable=False))
    corpus_sessions: int = Field(
        sa_column=Column("corpus_sessions", Integer, nullable=False))
    computed_at: str = Field(
        sa_column=Column("computed_at", Text, nullable=False))


class TopicExemplar(MemoryBase, table=True):
    """A signed *query exemplar* for one authoritative topic route: the
    embedding of a prompt on which the topic banner was injected, tagged with
    whether the route was relevant (`InjectedRelated`).

    The contextual, query-local half of topic-route ranking. A `fail`-graded
    prompt becomes a negative (`polarity = -1`): at route time
    `store.topic_route_suppressed` withholds the banner when the incoming
    query's max cosine to this topic's negatives clears
    `agent_memory.topic_negative_suppress_sim`. A `pass`-graded or human-curated
    prompt becomes a positive (`polarity = +1`) that *protects* the route — a
    query closer to a positive than to any negative is never suppressed, the
    query-local complement to the standing human `allowed` pin
    (`TopicRouteDecision`). Keyed by topic_id + model + polarity; each polarity
    capped at `negative_max_per_memory`. Computed server-side (the warm
    embedder) since the recall hook is model-free."""

    __tablename__ = "topic_exemplars"

    id: Optional[int] = Field(default=None, primary_key=True)
    topic_id: str = Field(
        sa_column=Column("topic_id", String, nullable=False, index=True))
    polarity: int = Field(sa_column=Column("polarity", Integer, nullable=False))
    source: str = Field(
        sa_column=Column("source", String, nullable=False, server_default="auto"))
    # The routed prompt this exemplar was built from, kept so the case is
    # inspectable (the panel shows what you labeled) and individually revertable
    # (delete one row by id). The vector is derived from it; NULL for
    # pre-column rows.
    query: Optional[str] = Field(default=None, sa_column=Column("query", Text))
    model_id: str = Field(
        sa_column=Column("model_id", String, nullable=False))
    dim: int = Field(sa_column=Column("dim", Integer, nullable=False))
    vector: bytes = Field(
        sa_column=Column("vector", LargeBinary, nullable=False))
    source_session: Optional[str] = Field(
        default=None, sa_column=Column("source_session", String))
    created_at: str = Field(
        sa_column=Column("created_at", Text, nullable=False))


# Back-compat alias for the pre-unification class name (negatives-only).
# Resolves to the polarity-tagged table above; callers that only ever wrote
# negatives keep working unchanged.
TopicNegative = TopicExemplar


@dataclass
class MemoryInput:
    """Write-side payload for `remember` / `supersede`."""

    body: str
    kind: str = DEFAULT_KIND
    title: Optional[str] = None
    scope: str = "global"
    tags: list[str] = field(default_factory=list)
    importance: float = 0.5
    veracity: str = "unknown"
    status: str = DEFAULT_STATUS
    tier: str = DEFAULT_TIER
    source_trace_id: Optional[str] = None
    source_span_id: Optional[str] = None
    source_agent_id: Optional[str] = None
    is_test: bool = False


@dataclass
class MemoryHit:
    """One recall result. `score_kind` mirrors pattern_router: 'rerank'
    is a sigmoid-calibrated cross-encoder confidence in (0, 1); 'rrf' and
    'fts' are rank-fusion scores only meaningful for ordering."""

    memory: dict
    score: float
    score_kind: str


__all__ = [
    "MemoryBase", "memory_metadata",
    "Memory", "MemoryEmbedding", "MemoryValidation", "MemoryPairCheck",
    "InjectionEvent",
    "MemoryEdge", "MemoryTopic", "MemoryTopicMember",
    "MemoryAuthoritativeTopic", "ReferentSessionDF",
    "TopicNegative",
    "MemoryInput", "MemoryHit",
    "MEMORY_TIERS", "MEMORY_KINDS", "MEMORY_STATUSES", "VERACITY_VALUES",
    "DEFAULT_KIND", "DEFAULT_TIER", "DEFAULT_STATUS",
]
