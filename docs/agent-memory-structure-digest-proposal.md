# Proposal: a maintained structure-digest layer for agent memory

> Status: **historical — built, then removed.** The digest stage shipped as
> reflect()'s opt-in `digest_enabled` generation slice, but the briefings were
> write-only (never injected or recalled), so the stage and its settings
> (`digest_enabled`, `digest_min_new_cards`, `digest_max_age_days`,
> `digest_max_sources`) were removed in the reflect-v4 "dream" redesign;
> reflect now retires any leftover `kind='digest'` rows. This document is
> kept as the design record.

> Original status: **proposal, not built.** Borrows the one architectural idea worth taking
> from TencentCloud's TencentDB-Agent-Memory after reading its source
> (`~/typescript-project/TencentDB-Agent-Memory`). Everything else in that system
> regin already matches or exceeds; this is the single delta.

## The idea in one line

regin's memory has an **evidence layer** (individual `memories` rows, searched
per-query) but no **structure layer** — a compact, auto-maintained narrative of
"what sessions in this scope have learned," regenerated on a cadence and injected
as *standing* context rather than retrieved per prompt.

## What TencentDB does (ground truth from its source)

Its "semantic pyramid" is half SQLite, half filesystem:

- **L0 conversation / L1 atomic facts** → SQLite (`vec0` vectors + FTS5), searched
  with BM25 + vector + RRF (k=60). This is regin's `memories` + recall, and regin's
  is strictly richer (cross-encoder rerank, MMR, quality/recency, exemplar rescore).
- **L2 scenario blocks / L3 persona** → **LLM-generated Markdown documents**
  (`scene_blocks/*.md`, `persona.md`), regenerated on a cadence (L1→+90s→L2→L3,
  plus an hourly per-session guarantee). These are *not* retrieval records — they are
  a maintained narrative the agent reads wholesale.

regin has **no analog to L2/L3**. Its synthesis cards (below) are the raw material,
but they live in the store and are pulled per-query, never rolled into a standing doc.

(For contrast, the things regin should *not* copy: TencentDB has no reinforcement
signal at all — no access counts, no recall_count, no usage-weighted ranking — and
its forgetting methods `deleteL1Expired`/`deleteL0Expired` exist but are wired to
nothing. regin's engagement scoring + exemplar polarity + multi-factor decay are a
real lead. Its dedup *is* the Mem0 store/update/merge/skip pattern, run
synchronously at write time — a separate idea, tracked elsewhere.)

## What regin already has (the building blocks)

- `lib/memory/reflect.py::_synthesize` (lines ~485–639) clusters related episodic
  rows (cosine band `[_SYNTHESIS_FLOOR=0.55, dedup_threshold)`) and asks the LLM to
  abstract ONE higher-order rule per cluster — a **synthesis card** written as an
  episodic `kind="lesson"` memory tagged `synthesis`.
- `store.create_topic(name, summary, summary_memory_id, scope, member_ids)` groups
  the cluster under a `MemoryTopic` (`models.py`) whose `summary_memory_id` points at
  the card. Topics already carry a `scope` (`global` | `repo:<name>`).
- `hook_manager/handlers/memory_recall.py::handle` injects a per-query
  `<recalled_experience>` block via `POST /api/memory/recall`, falling back to
  in-process FTS when `regin serve` is down.
- The Claude Code session `MEMORY.md` is the *hand-curated* "capped pinned cache"
  (CLAUDE.md). The digest below is its **auto-maintained, store-derived sibling**.

So the gap is narrow: regin produces the cards but never rolls them up into a
scope-level document, and never injects anything as standing context.

## Proposed design

### 1. Generation — a new reflect stage `_synthesize_digest(scope)`

Runs last in `reflect()`, after `_synthesize` (so it sees fresh cards), once per
active scope. For a scope it gathers the inputs already ranked by the store:

- all active synthesis cards in scope (tag `synthesis`), plus
- the top-N highest-`importance`, most-recalled episodic memories in scope.

It asks the LLM for a compact narrative (~400–800 chars, structured: "conventions /
gotchas / decisions" or similar), then persists it. Two storage options:

- **(a) reuse the store**: write the digest as a singleton memory per scope —
  `kind="digest"`, `tags=["digest"]`, `status="active"` — and supersede the prior
  digest for that scope (chains via `superseded_by`, same mechanism as everything
  else). Zero schema change. Recommended.
- **(b) a `memory_digests` table** keyed by scope. Cleaner read, but adds schema +
  the `db/schema.sql` drift gotcha (CLAUDE.md). Skip unless (a) proves awkward.

**Cadence guard** (mirrors TencentDB's "regenerate only when enough changed"):
regenerate a scope's digest only when ≥K new `synthesized` validations exist since
its last regeneration, or it is older than `digest_max_age_days`. Gate the whole
stage behind `settings.agent_memory.digest_enabled` (default off), and skip without
an LLM — same pattern as `synthesis_enabled`.

### 2. Injection — a standing `<memory_digest>` block

A small addition to (or a sibling handler of) `memory_recall.py`: on an eligible
prompt, resolve the active repo scope and inject the maintained digest for that
scope as a standing `<memory_digest>` block, *separate from* and *in addition to*
the per-query `<recalled_experience>` block.

Key property: the digest needs **no embedding/recall** — it is a direct row read by
scope, so it works even when `regin serve` (and thus the embedder) is down, where
per-query dense recall degrades to FTS. It is cheap, deterministic, always-on
context — the auto-generated complement to `MEMORY.md`.

Reinforcement: like `<recalled_experience>`, injecting the digest must NOT reinforce
its source cards (`reinforce=False`) — standing context is not a deliberate pull.

## Why this is the right delta

- It is **additive and low-risk**: new opt-in reflect stage + new inject block,
  reusing `_synthesize`'s output, `create_topic`'s scope, the supersede chain, and
  the existing inject handler. No change to recall ranking or the schema (option a).
- It closes the *only* axis where TencentDB is ahead of regin.
- It fits regin's own stated model: a "capped pinned cache" of durable, cross-cutting
  facts — but generated from the store and refreshed automatically, instead of
  maintained by hand.

## Open questions before building

1. **Digest vs. MEMORY.md overlap** — do they fight? The digest is store-derived and
   scope-narrow; MEMORY.md is hand-curated and cross-cutting. Likely complementary,
   but the injected token budget for both together needs a cap.
2. **Per-scope cost** — regenerating a narrative per scope per reflect run is an LLM
   call each; the cadence guard must keep this bounded (only on material change).
3. **Drill-down refs** — TencentDB keeps `node_id` back-pointers from the narrative to
   raw evidence. regin's analog is the cards' `source_trace_id`/`source_span_id`.
   Worth surfacing the contributing memory ids in the digest so an agent can pull the
   underlying cards via the `recall` MCP tool.
4. **Eval** — does a standing digest measurably help vs. per-query recall alone, or
   just add tokens? Needs an A/B on engagement before defaulting on.
