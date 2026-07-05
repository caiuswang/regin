# Memory recall pipeline

The read-side ranking stack in `lib/memory/store.py` — everything a `recall()`
request flows through, so an agent tuning or debugging why a memory ranks where
it does knows which knob moves which result. Recall mirrors `pattern_router`'s
hybrid shape and degrades gracefully at every outer stage when the injected
`EmbeddingProvider` is absent.

## The ordered stack (`SqliteMemoryStore.recall`)

1. **Retrieve wide, embed once.** `retrieval_k = max(top_k * 4, 20)`. On the
   dense path the query is embedded a single time (`_embed_query`, which prefers
   the embedder's `embed_queries` extension) and reused across the leg.
2. **Two legs + RRF.** `_lexical_ids` runs FTS5/BM25 over `memories_fts`;
   `_dense_ids` does brute-force cosine over `memory_embeddings` (preceded by
   `_lazy_backfill`, which embeds up to 32 stale active rows in-process so the
   dense leg can see freshly written rows). `_rrf` fuses the two rankings —
   robust to the incomparable per-leg score scales. `mode='fts'` skips the dense
   leg entirely, so an FTS-only path always works; `mode='auto'`/`'hybrid'` uses
   dense + rerank when the embedder can deliver.
3. **Eligibility.** `_eligible_rows` narrows to `active`, non-`digest`,
   scope-filtered (`["global", scope]`) rows whose `valid_until` has not passed,
   tests excluded unless asked.
4. **Precision gate.** `_gate_lexical_overlap` applies the `min_overlap` gate to
   *lexical-only* candidates: it drops those sharing fewer than `needed`
   *informative* tokens with the query. Corpus-saturated tokens are filtered out
   via `_common_overlap_tokens` (document-frequency over active rows, gated by
   `overlap_idf_max_df`, and only once the corpus clears `_IDF_MIN_CORPUS = 20`).
   Dense hits pass untouched — a semantic match legitimately has zero token
   overlap, and its precision is guarded by the rerank confidence downstream.
5. **Rerank.** `_order_candidates` sends the RRF head (capped at `rerank_cap`)
   through the cross-encoder when the embedder offers `rerank` and the dense leg
   ran, mapping raw logit diffs through a sigmoid to a calibrated (0,1)
   confidence. It returns a `score_kind` of `rerank`, `rrf`, or `fts`. The cap
   exists because cross-encoder cost is linear (~25ms/candidate warm) and the
   auto-inject hook has a sub-second budget.
6. **Quality weighting.** `_apply_quality` multiplies each score by the bounded
   `_quality_factor` (`[0.9, 1.3]`) folding in importance, veracity, deliberate-
   recall count, and recency (`recall_recency_half_life_days`) — rewarding
   proven/recent/important rows without letting quality override relevance.
   Gated by `recall_quality_weighting`.
7. **Topic boost.** `_apply_topic_boost` softly lifts (`× (1 + topic_boost_weight)`)
   candidates linked to a routed authoritative topic node (`boost_topic_node_id`).
   It reorders, never filters.
8. **Diversity.** `_mmr_select` applies optional maximal-marginal-relevance
   diversity (`inject_mmr_lambda`) over the scored pool; candidates lacking an
   embedding compete on relevance alone, so the lexical tail is never dropped.
9. **Edge expansion.** `_expand_via_edges` optionally appends the strongest
   1-hop `related` neighbours of the selected hits (`recall_expand_*`, off by
   default, each scored below its seed).
10. **Reinforce.** `_bump_recall` increments `recall_count` and stamps
    `last_recalled` — but only on deliberate recall (`reinforce=True`);
    speculative auto-inject passes `reinforce=False`.

## Supporting modules

- `expand.py` — the LLM query-rewrite front-end for terse prompts, turning a
  two-word ask into a fuller recall query.
- `evaluate.py` — an FTS-only regression harness for recall quality (its own
  `tests/memory/test_evaluate.py`).

Embeddings are written by `reflect()` (working-tier rows are raw on purpose,
with `_lazy_backfill` covering the gap), so a store that has never reflected
simply recalls FTS-only. Delivery of these hits to a prompt is
**memory-auto-injection**; whether a delivered hit helped is
**memory-engagement-feedback**.