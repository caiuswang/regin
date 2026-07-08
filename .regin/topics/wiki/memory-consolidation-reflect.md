# Memory consolidation (reflect)

`reflect()` in `lib/memory/reflect.py` is the offline "sleep cycle" that turns
raw `working` rows into curated `episodic` knowledge and keeps the store from
drifting. It is idempotent — a second pass over an already-consolidated store
finds nothing to do — and `dry_run=True` builds the evidence pack and calls the
LLM but applies nothing, so the would-be plan lands in `result.actions`. It
never *requires* a model: an embedder sharpens similarity, an LLM unlocks the
generative judgment, and absent both every surviving working row is still
deduped and blind-promoted.

## The four stages (`reflect`)

**1. Mechanical pre-pass** (no model). `_dedup_merge` collapses near-identical
pairs at or above the dedup threshold (`dedup_cosine_threshold` when an embedder
is injected, else the deterministic `dedup_text_threshold`); `_keeper_of` keeps
the episodic row, then the more-recalled, then the older, retiring the loser
with `superseded_by` pointing at the keeper. `_score_pending` runs the pre-decay
engagement sweep (`rebuild_session_referent_df` + `score_pending_sessions`, plus
the topic router's query-df refresh) so decay reads a dense signal. Legacy
`kind='digest'` rows are retired (`_retire_legacy_digests`); consolidation has no
separate digest stage.

**2. Dream** — the one agentic LLM stage per run (`_dream`). A single
`llm.complete` receives a bounded evidence pack (`_build_dream_pack`, capped at
`_DREAM_PACK_CAP`): every pending working row with its top co-retrieval
neighbours (pulled through the store's own `recall`, i.e. what the runtime would
actually surface next to it, not a raw cosine band) plus up to
`contradiction_budget` suspect episodic pairs (same-scope rows sharing a
concrete repo file path, not yet judged — the `memory_pair_checks` ledger tracks
judged pairs so an offered-but-unjudged pair re-presents next run). The model
returns one JSON plan through the editable `memory-reflect-dream` surface:
promote/hold/drop/merge per working row, contradict/obsolete/distinct per pair,
and optional synthesize actions. `_apply_dream_plan` applies it
deterministically with per-action validation — the model-claimed older/newer
order is never trusted (`created_at` decides), a circular merge is rejected,
destructive verdicts (drop/merge) apply only under `promote_allow_retire` and
otherwise degrade to hold, and any invalid action is skipped and counted
(`dream_skipped`). A synthesize action writes a new episodic row whose
importance is the *median* of its ≥ 3 distinct same-scope episodic sources (an
abstraction can't outrank its evidence by construction) and routes it through
`_record_synthesis_topic`. Working rows the plan never mentions blind-promote;
rows it addressed invalidly are held, never blind-promoted. No LLM,
`dream_enabled` off, or an unparseable plan → every surviving working row is
blind-promoted, so a model outage never blocks consolidation; rows beyond
`_DREAM_MAX_WORKING` defer untouched to the next run.

**3. Lifecycle decay** (no model). `_forget_stale` retires episodic rows aged
past `forget_after_days` that were never *deliberately* recalled
(`recall_count == 0`) — the negative half of the usefulness loop (speculative
auto-inject doesn't reinforce). `_decay_chronically_ignored` docks
`_IGNORED_DECAY_STEP` importance (floored at `_IGNORED_DECAY_FLOOR`, never
retired) from rows that earned no positive signal; `_decay_reason` gates it in
order — hard spares (`_decay_spared`: a deliberate recall, a `_POSITIVE_ACTIONS`
validation, or already at the floor); the dense engagement *rate*
(`_engagement_spare`, reading the engaged / soft-ignored / hard-ignored split,
where soft ignores get benefit of the doubt); then the pre-rate count thresholds
`ignored` / `injected` (`_threshold_decay_reason`), each self-limiting or gated
once per memory. `_flag_stale_references` (opt-in `verify_stale_refs`)
regex-extracts concrete repo paths a memory names, rewrites any git-renamed path
in place first (`_rename_follow`, gated by
`topic_evolution.mechanical_autoapply`), and flags only the genuinely-deleted
residual with a `stale_ref` validation and a `veracity` demote true→unknown
(never retired — a regex is a heuristic).

**4. Embed + edges** (embedder only). `_embed_episodic` gives active rows of
*both* tiers vectors (content-hash-skipped when unchanged) so a fresh
working-tier lesson is dense-visible without waiting for promotion.
`_harvest_edges` (`edges_enabled`) rebuilds the whole `related` edge set from
`cosine_pairs` in `[edge_floor, dedup_threshold)` (near-identical pairs are
merged, not linked), capped per node (`_cap_edges_per_node` /
`edge_max_per_node`). Runs after embed so freshly written synthesis/promotion
rows are linkable.

## By-products

The dream stage is agentic: `resolve_dreamer` (see
**agent-memory-architecture**) grants it the read-only memory tools to pull
evidence beyond the bounded pack. Its synthesis proposals land in the same human
review queue as topic proposals via `topic_attach.py`
(`maybe_propose_authoritative` when `reflect_proposes_authoritative_topics`,
else an orphan `memory_topic` under `topics_enabled`). The `related`-edge graph
and emergent `memory_topics` feed the curate UI's relationship views and
recall's edge expansion. The positive counterpart to this cycle's decay half is
**memory-engagement-feedback**; the rows it consolidates are born in
**memory-distillation-capture**.