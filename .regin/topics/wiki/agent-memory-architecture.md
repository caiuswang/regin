# Agent memory architecture

The entry point to regin's cross-session memory subsystem (`lib/memory/`). Read
this before touching any other memory topic: it fixes the seam every other
module plugs into, the DB the store lives on, and the tier/lifecycle vocabulary
the rest of the subsystem speaks.

## The facade

`lib/memory/__init__.py` is a thin module-verb facade — `remember` / `recall` /
`reflect` / `get` / `update` / `forget` / `supersede` / `restore` / `stats`,
plus the tree export/import helpers. It builds one process-wide
`SqliteMemoryStore` wired with a `SkillRouterEmbedding`, and every caller (hooks,
web blueprint, CLI) goes through it rather than constructing a store.

## Ports & adapters seam

`ports.py` declares the four decoupling interfaces the engine depends on and
nothing else — `EmbeddingProvider`, `LLMProvider`, `MemoryStore`, `MemorySink` —
each a `runtime_checkable` Protocol documented to degrade gracefully: no embedder
→ FTS-only recall; no LLM → reflect blind-promotes its working rows and distill
proposes nothing; no sink → no export. The engine modules (`store`, `reflect`,
`distill`) accept these ports by constructor injection and never import concrete
providers.

`adapters.py` is the **edge** — the one place under `lib/memory/` allowed to
import concrete providers (`lib.skills.skill_router`, subprocess commands).
Swapping or removing an adapter is a zero-diff change to the engine. It ships:

- `SkillRouterEmbedding` — an `EmbeddingProvider` over the same SkillRouter
  models that power `pattern_router` / `skill_router`. It exposes the bare
  `embed`, plus two optional extensions the store discovers via `getattr`:
  `embed_queries` (SkillRouter is an asymmetric bi-encoder, so queries get an
  instruction prefix via `skill_router.format_query` that documents don't) and
  `rerank` (the cross-encoder returning raw logit differences over
  `{name, description, body}` candidates). A minimal symmetric embedder that
  offers only `embed` still satisfies the port. The adapter degrades to
  disabled (`model_id` is `None`, `embed` returns `None`) when torch/transformers
  are missing or `settings.agent_memory.dense_enabled` is off.
- `ExternalAgentLLM` — an `LLMProvider` over a configured external command,
  mirroring the topic-proposal external-agent harness: the prompt goes to
  stdin, stdout comes back. It resolves an agent from
  `settings.topic_proposal_external_agents`, honoring a per-surface binding via
  `lib.prompts.surface_agent`; with none configured `complete` returns `None`
  and callers fall back to heuristics. `extra_args` are appended to the agent's
  argv — the hook for granting an agentic caller its read-only tools
  (`--allowedTools …`), and a `surface_id` tags the spawned trace (via a
  `REGIN_LLM_SURFACE` env stamp) as an `llm-stage` run rather than an
  interactive session. `complete` blocks and reads stdout; `spawn_spec` returns
  a `SpawnSpec` — the same argv / cwd / surface bundle for launching the agent
  *detached*, for a caller that reports back over its own side channel instead
  of waiting on the result inline.
- Five resolvers bind a surface to that LLM: `resolve_distiller` grants the
  read-only `trace dump` / `trace span` tools (`distill_allowed_tools`) so the
  distiller can self-fetch a session's spans; `resolve_dreamer` grants the
  read-only memory tools (`dream_allowed_tools`) so `reflect`'s single dream
  stage can pull evidence beyond its bounded pack; `resolve_topic_classifier`
  and `resolve_retitler` grant no tools (plain-text-in, JSON-out reasoning for
  `memory link-topics` and one-line-rule titling in `memory retitle`);
  `resolve_proposal_reviewer` grants `Read,Glob,Grep` so the review LLM verifies
  a draft against the current refs itself.

## Scoping

`scoping.py` stamps `global` or `repo:<name>` onto writes and recall — a value
the engine only equality-filters. Distill resolves a session's own repo scope
through `session_repos`; recall widens a `repo:<name>` query to
`["global", scope]` so shared lessons still surface.

## The self-initializing DB

`models.py` declares its own `memory_metadata`, so `regin init` / `rebuild` and
Alembic never see the memory tables. `engine.py` is a self-initializing third
engine (reusing `lib/orm/engine._build_engine`) on `db/regin_memory.db` that runs
`create_all` plus the FTS5 + index DDL and idempotent migrations on first
checkout. The single mutable `memories` table carries both tiers via a `tier`
column (`working` / `episodic`, default `working`) alongside importance,
veracity, `recall_count`, provenance (`source_trace_id` / `source_span_id` /
`source_agent_id`), and lifecycle stamps. Its side tables are
`memory_embeddings`, `memory_validations`, `memory_pair_checks` (the dream
stage's judged-pair ledger), `injection_events`, `topic_injections`,
`topic_route_decisions`, `memory_edges`, `memory_topics`,
`memory_topic_members`, `memory_authoritative_topics`, `topic_wiki_recalls`,
`referent_session_df`, and `topic_exemplars`.

Unlike the append-only `session_spans` store, memory is **mutable by design** —
rows are updated, superseded, retired, and deleted in place, because that
lifecycle is the whole point of curation.

## MCP entrypoint

`mcp_server.py` is the long-lived MCP entrypoint exposing the deliberate
deeper-pull surface: `recall`, the coarse-to-fine tree-nav tools
`index_root` / `index_expand` / `index_fetch`, and a `gate` tool. See
**memory-auto-injection** for how it is used.

## How it connects

This is the hub. Capture (**memory-distillation-capture**) writes rows the
consolidation cycle (**memory-consolidation-reflect**) curates and the read
stack (**memory-recall-pipeline**) ranks; injection (**memory-auto-injection**)
delivers them. The store also holds the signed-exemplar
(**memory-exemplar-rescore**) and topic-route feedback
(**memory-topic-route-feedback**) machinery.