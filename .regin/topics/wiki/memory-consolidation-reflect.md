# Memory consolidation (reflect)

`reflect()` in `lib/memory/reflect.py` is the offline "sleep cycle" that turns
raw `working` rows into curated `episodic` knowledge and keeps the store from
drifting. It is idempotent — a second pass over an already-consolidated store
finds nothing to do — and `dry_run=True` reports what would happen without
writing. It never *requires* a model: an embedder sharpens each stage, an LLM
unlocks the generative ones, and both absent it still dedups and promotes.

## The ordered cycle (`reflect`)

1. **Dedup** (`_dedup_and_judge` → `_merge_pair`). `_pair_similarities` compares
   every working row against the working+episodic pool (embedding cosine when an
   embedder is injected, else a deterministic `difflib` text ratio). Pairs at or
   above the threshold (`dedup_cosine_threshold` / `dedup_text_threshold`)
   collapse: `_keeper_of` keeps the episodic row, then the more-recalled, then
   the older; the loser is retired with `superseded_by` pointing at the keeper.
2. **Contradiction** (`_resolve_contradiction`). Pairs in the gray zone
   `[_GRAY_ZONE_FLOOR = 0.75, threshold)` are put to the LLM
   (`_llm_says_contradiction`, via the editable `memory-contradiction` surface);
   a judged contradiction retires the older row with `veracity='false'`. No LLM
   → the pair is left alone (veracity stays `unknown` rather than guessed).
3. **Promote** (`_promote`). Surviving working rows become `episodic`, stamped
   `consolidated_at`, importance nudged by the reinforcement signal
   (`_reinforced_importance`, a `log1p(recall_count)` boost).
4. **Forget-stale** (`_forget_stale`). Episodic rows aged past
   `forget_after_days` that were never *deliberately* recalled
   (`recall_count == 0`) are retired — the negative half of the usefulness loop
   (speculative auto-inject doesn't reinforce). `0` disables; fresh rows can't
   be stale, keeping reflect idempotent on a young store.
5. **Pre-decay engagement sweep** (`_score_pending`). Before decay reads the
   signal, densify it: `rebuild_session_referent_df` + `score_pending_sessions`
   stamp engagement verdicts on finished, unscored injects, and
   `_refresh_query_df` rebuilds the topic router's query term-frequency cache.
   A write, so skipped on dry-run and gated by `score_pending_on_reflect`.
6. **Decay** (`_decay_chronically_ignored`). A memory that earned no positive
   signal loses `_IGNORED_DECAY_STEP` importance (floored at
   `_IGNORED_DECAY_FLOOR`, never retired). `_decay_reason` gates it in order:
   hard spares (`_decay_spared`: a deliberate recall, a `_POSITIVE_ACTIONS`
   validation, or already at the floor); the dense engagement *rate*
   (`_engagement_spare`, reading the engaged / soft-ignored / hard-ignored split
   — soft ignores get benefit of the doubt); then the pre-rate count thresholds
   `ignored` / `injected` (`_threshold_decay_reason`), each self-limiting or
   gated once per memory.
7. **Stale references** (`_flag_stale_references`, opt-in `verify_stale_refs`).
   `_referenced_paths` regex-extracts concrete repo file paths a memory names;
   `_missing_refs` checks them against the scope's repo root. A path git history
   shows was *renamed* is rewritten in place first (`_rename_follow`, gated by
   `topic_evolution.mechanical_autoapply`, via `lib.topics.drift`); only the
   genuinely-deleted residual is flagged with a `stale_ref` validation and a
   `veracity` demote true→unknown (never retired — a regex is a heuristic).
8. **Synthesize** (`_synthesize`). Generative-Agents-style reflection: greedy
   clusters of related-but-distinct episodic rows (cosine band
   `[_SYNTHESIS_FLOOR = 0.55, dedup_threshold)`, min 3 members, up to 3 clusters)
   are handed to the LLM (`_llm_synthesis`, `memory-reflect-synthesis` surface)
   to abstract one higher-order rule. `_write_synthesis` writes it as a new
   episodic row and stamps each source `synthesized` (the idempotency marker);
   `_record_synthesis_topic` either feeds it into the authoritative topic-
   proposal queue (`topic_attach.maybe_propose_authoritative` when
   `reflect_proposes_authoritative_topics`) or mints an orphan `memory_topic`
   (`create_topic`, gated by `topics_enabled`). Needs both an embedder and an
   LLM.
9. **Digest** (`_synthesize_digest`, opt-in `digest_enabled`). Rolls each
   scope's most important episodic rows into ONE maintained briefing, refreshed
   in place via `supersede`. Standing context read by scope, excluded from
   similarity recall and the lifecycle. Needs only an LLM; runs after synthesis
   so a fresh card can feed it the same pass.
10. **Embed** (`_embed_episodic`). Active rows of both tiers get vectors
    (content-hash-skipped when unchanged) so the dense recall leg can see them —
    a fresh working-tier lesson is dense-visible without waiting for promotion.
11. **Harvest edges** (`_harvest_edges`, `edges_enabled`). Rebuilds the whole
    `related` edge set from `cosine_pairs` in `[edge_floor, dedup_threshold)`
    (near-identical pairs are merged, not linked), capped per node
    (`_cap_edges_per_node` / `edge_max_per_node`). Runs after embed so freshly
    written rows are linkable.

## By-products

The `related`-edge graph and emergent `memory_topics` feed the curate UI's
relationship views and recall's edge expansion; `topic_attach.py` lands
synthesis proposals in the same human review queue as topic proposals. The
positive counterpart to this cycle's decay half is
**memory-engagement-feedback**; the rows it consolidates are born in
**memory-distillation-capture**.