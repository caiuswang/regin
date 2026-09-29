"""Hybrid recall: FTS5 lexical leg, dense leg, RRF fusion, rerank, MMR,
and recall reinforcement."""

from __future__ import annotations

import math
from datetime import datetime
from typing import Optional

from sqlalchemy import text as sa_text
from sqlmodel import select

from lib.activity_log import get_activity_logger
from lib.settings import settings
from lib.memory.engine import MemorySessionLocal
from lib.memory.models import Memory, MemoryEmbedding, MemoryHit
from lib.memory.store.helpers import (
    _IDF_MIN_CORPUS,
    _document_frequency,
    _fts_query,
    _mmr_enabled,
    _mmr_gram,
    _mmr_order,
    _now,
    _overlap_tokens,
    _quality_factor,
    _rrf,
    _serialize,
)

log = get_activity_logger("memory")


class RecallMixin:
    def recall(self, query: str, *, top_k: int = 5,
               scope: Optional[str] = None, mode: str = "auto",
               include_tests: bool = False, reinforce: bool = True,
               min_overlap: int = 0,
               boost_topic_node_id: Optional[str] = None) -> list[MemoryHit]:
        """Hybrid recall. `mode`: 'fts' = lexical only (the hook path —
        never loads models), 'auto'/'hybrid' = dense + rerank when the
        injected embedder can deliver them. `min_overlap` > 0 drops
        candidates sharing fewer than that many distinct content tokens
        with the query — the precision gate for speculative surfaces
        (auto-inject), where BM25's always-rank-something behavior would
        otherwise attach tangential memories to every prompt."""
        retrieval_k = max(top_k * 4, 20)
        # Embed the query once (dense path only) and reuse it across the
        # dense leg, so recall pays at most one query-embedding cost.
        q_vec = None if mode == "fts" else self._embed_query(query)
        lex_ids = self._lexical_ids(query, retrieval_k)
        dense_ids = ([] if mode == "fts"
                     else self._dense_ids(query, retrieval_k, q_vec))
        fused = _rrf([dense_ids, lex_ids])[:retrieval_k]
        rows = self._eligible_rows([mid for mid, _ in fused],
                                   scope, include_tests)
        rows = self._gate_lexical_overlap(rows, query, min_overlap,
                                          dense_ids)
        fused = [(mid, s) for mid, s in fused if mid in rows]
        ordered, score_kind = self._order_candidates(
            query, fused, rows, used_dense=bool(dense_ids),
            rerank_cap=max(top_k * 2, 8))
        ordered = self._apply_quality(ordered, rows)
        ordered = self._apply_topic_boost(ordered, boost_topic_node_id, scope)
        selected = self._mmr_select(ordered, top_k)
        selected = self._expand_via_edges(selected, rows, scope, include_tests)
        hits = [MemoryHit(memory=_serialize(rows[mid]), score=float(s),
                          score_kind=score_kind)
                for mid, s in selected]
        if reinforce and hits:
            self._bump_recall([h.memory["id"] for h in hits])
        log.read("memories_recalled", count=len(hits), mode=mode,
                 score_kind=score_kind)
        return hits

    def _expand_candidates(self, selected, have, scope, include_tests, cfg):
        """`{neighbour_id: (hit, score)}` for the in-scope, not-yet-selected
        `related` neighbours of every seed hit. Score discounts seed-score by
        edge-weight and `recall_expand_discount`."""
        cap = cfg.edge_max_per_node or 8
        cands: dict[str, tuple] = {}
        for mid, score in selected:
            for hit in self.edge_neighbors(mid, top_k=cap,
                                           include_tests=include_tests):
                nid = hit["id"]
                in_scope = scope is None or hit.get("scope") == scope
                if nid in have or nid in cands or not in_scope:
                    continue
                cands[nid] = (hit, score * float(hit.get("similarity") or 0.0)
                              * cfg.recall_expand_discount)
        return cands

    def _expand_via_edges(self, selected: list, rows: dict, scope, include_tests):
        """Opt-in 1-hop expansion: append the strongest `related` neighbours
        of the selected hits, each scored below its seed. Off by default, so
        the hot auto-inject path is unchanged. Newly pulled rows are added to
        `rows` so the caller can serialize them."""
        cfg = settings.agent_memory
        if not cfg.recall_expand_enabled or not selected:
            return selected
        have = {mid for mid, _ in selected}
        cands = self._expand_candidates(selected, have, scope,
                                        include_tests, cfg)
        ranked = sorted(cands.values(), key=lambda hs: -hs[1])
        additions = []
        for hit, s in ranked[:max(0, cfg.recall_expand_max)]:
            row = self.get(hit["id"])
            if row is not None:
                rows[hit["id"]] = row
                additions.append((hit["id"], s))
        return selected + additions

    def _mmr_select(self, ordered: list, top_k: int) -> list:
        """Final top_k selection with maximal-marginal-relevance diversity.

        Greedy MMR over the already-relevance-scored candidate pool (see
        `_mmr_order`). Candidates lacking an embedding compete on relevance
        alone, so the lexical tail is never dropped. A plain `ordered[:top_k]`
        no-op unless MMR is enabled and there is a surplus to diversify over —
        the FTS / k=1 / no-embedder paths fall straight through. Curbs near-
        duplicate hits filling adjacent slots, which wastes inject budget and
        skews the engaged/ignored feedback signal."""
        lam = settings.agent_memory.inject_mmr_lambda
        if not _mmr_enabled(lam, ordered, top_k, self._embedder):
            return ordered[:top_k]
        ids, mat = self._embedding_matrix(self._embedder.model_id)
        if mat.size == 0:
            return ordered[:top_k]
        idx = {m: i for i, m in enumerate(ids)}
        gram = _mmr_gram(ordered, idx, mat)
        rank = {m: i for i, m
                in enumerate(_mmr_order(ordered, gram, lam, top_k))}
        return sorted((p for p in ordered if p[0] in rank),
                      key=lambda p: rank[p[0]])

    def _lexical_ids(self, query: str, k: int) -> list[str]:
        expr = _fts_query(query)
        if not expr:
            return []
        with MemorySessionLocal() as session:
            rows = session.execute(
                sa_text("SELECT memory_id, bm25(memories_fts) AS s "
                        "FROM memories_fts WHERE memories_fts MATCH :q "
                        "ORDER BY s LIMIT :k"),
                {"q": expr, "k": k}).all()
        return [mid for mid, _ in rows]

    def _lazy_backfill(self, embedder, cap: int = 32) -> None:
        """Embed up to `cap` stale active rows in the calling process.

        Runs only when a real embedder is available (dense leg active).
        On any failure the dense leg still proceeds — degrade, never raise.
        Called only from `_dense_ids`, so short-lived FTS-only paths are
        never affected."""
        from lib.memory.reflect import _doc_text, stale_embedding_todo
        try:
            todo = stale_embedding_todo(self, embedder.model_id)[:cap]
            if not todo:
                return
            vecs = embedder.embed([_doc_text(m) for m, _ in todo])
            if not vecs:
                return
            for (mem, h), vec in zip(todo, vecs):
                self.set_embedding(mem["id"], vec, embedder.model_id, h)
            log.write("memory_embeddings_backfilled", count=len(todo))
        except Exception:
            log.error("memory_backfill_failed", exc_info=True)

    def _dense_ids(self, query: str, k: int, q_vec=None) -> list[str]:
        embedder = self._embedder
        if embedder is None or embedder.model_id is None:
            return []
        self._lazy_backfill(embedder)
        ids, mat = self._embedding_matrix(embedder.model_id)
        if not ids:
            return []
        if q_vec is None:
            q_vec = self._embed_query(query)
        if q_vec is None:
            return []
        import numpy as np
        sims = mat @ q_vec
        top = np.argsort(-sims)[: min(k, len(ids))]
        return [ids[i] for i in top]

    def _embed_query(self, query: str):
        """The query embedding as a float32 numpy array, or None when no
        embedder is available / the query is empty / embedding failed. Used by
        the dense recall leg and topic-route exemplar similarity. Best-effort: a
        raising embedder degrades to None (no dense leg) rather than failing the
        recall."""
        embedder = self._embedder
        if embedder is None or embedder.model_id is None or not query:
            return None
        embed_q = getattr(embedder, "embed_queries", embedder.embed)
        try:
            vecs = embed_q([query])
        except Exception:
            log.error("query_embed_failed", exc_info=True)
            return None
        if not vecs:
            return None
        import numpy as np
        return np.asarray(vecs[0], dtype="float32")

    def _embedding_matrix(self, model_id: str):
        import numpy as np
        with MemorySessionLocal() as session:
            rows = session.exec(select(MemoryEmbedding).where(
                MemoryEmbedding.model_id == model_id)).all()
        ids = [r.memory_id for r in rows]
        if not rows:
            return ids, np.zeros((0, 0), dtype="float32")
        dim = rows[0].dim
        mat = np.frombuffer(
            b"".join(r.vector for r in rows), dtype="float32"
        ).reshape(len(rows), dim).copy()
        return ids, mat

    def _gate_lexical_overlap(self, rows: dict[str, Memory], query: str,
                              min_overlap: int,
                              dense_ids: list[str]) -> dict[str, Memory]:
        """Apply the `min_overlap` precision gate to lexical candidates
        only. The gate exists for BM25's always-rank-something behavior; a
        memory the dense leg surfaced was matched semantically — often with
        zero token overlap (that is the point of dense recall) — and its
        precision is guarded downstream by the rerank confidence gate
        (`recall_min_score`), not by token counting."""
        if min_overlap <= 0:
            return rows
        dense_set = set(dense_ids)
        lex_only = {mid: r for mid, r in rows.items() if mid not in dense_set}
        kept = self._overlap_filtered(lex_only, query, min_overlap)
        return {mid: r for mid, r in rows.items()
                if mid in dense_set or mid in kept}

    def _overlap_filtered(self, rows: dict[str, Memory], query: str,
                          min_overlap: int) -> dict[str, Memory]:
        query_tokens = _overlap_tokens(query)
        # Overlap is counted on *informative* tokens only: a token the
        # corpus is saturated with ('session'/'memory'/'trace' here) is
        # dropped, so a coincidental match on common words can't clear the
        # gate. A query left with no informative tokens has no distinctive
        # signal — nothing passes rather than injecting on noise.
        informative_q = query_tokens - self._common_overlap_tokens()
        needed = min(min_overlap, len(informative_q))
        if needed <= 0:
            return {}
        out = {}
        for mid, row in rows.items():
            doc = _overlap_tokens(f"{row.title or ''} {row.body} {row.tags or ''}")
            if len(informative_q & doc) >= needed:
                out[mid] = row
        return out

    def _common_overlap_tokens(self) -> set[str]:
        """Tokens appearing in more than `overlap_idf_max_df` of active
        (non-test) memories — too common to count as meaningful overlap.
        Cached per (process, active-count): the df scan reruns only when the
        corpus size changes. Empty set when idf filtering is disabled, which
        collapses `_overlap_filtered` back to a raw-token gate."""
        max_df = settings.agent_memory.overlap_idf_max_df
        if max_df <= 0 or max_df >= 1:
            return set()
        with MemorySessionLocal() as session:
            rows = session.exec(
                select(Memory.title, Memory.body, Memory.tags)
                .where(Memory.status == "active")
                .where(Memory.is_test == 0)).all()
        n = len(rows)
        if n < _IDF_MIN_CORPUS:
            return set()
        cache = getattr(self, "_common_tok_cache", None)
        if cache is not None and cache[0] == n:
            return cache[1]
        df = _document_frequency(rows)
        common = {tok for tok, count in df.items() if count > max_df * n}
        self._common_tok_cache = (n, common)
        return common

    def _eligible_rows(self, ids: list[str], scope: Optional[str],
                       include_tests: bool) -> dict[str, Memory]:
        if not ids:
            return {}
        now = _now()
        with MemorySessionLocal() as session:
            stmt = (select(Memory)
                    .where(Memory.id.in_(ids))
                    .where(Memory.status == "active")
                    # Digests are standing context read by scope, never a
                    # similarity-recall hit — keep them out of every candidate
                    # set (auto-inject and the deliberate `recall` tool alike).
                    .where(Memory.kind != "digest"))
            if not include_tests:
                stmt = stmt.where(Memory.is_test == 0)
            if scope:
                stmt = stmt.where(Memory.scope.in_(["global", scope]))
            rows = session.exec(stmt).all()
        return {r.id: r for r in rows
                if r.valid_until is None or r.valid_until >= now}

    def _order_candidates(self, query: str, fused: list[tuple[str, float]],
                          rows: dict[str, Memory], *, used_dense: bool,
                          rerank_cap: int = 0):
        """Cross-encoder rerank when the embedder offers it; otherwise the
        RRF ordering stands. Sigmoid maps raw logit diffs to (0,1) so
        callers can threshold on a calibrated confidence.

        `rerank_cap` > 0 sends only the RRF head through the cross-encoder
        and drops the tail. Cross-encoder cost is linear in candidates
        (~25ms each warm) and the auto-inject hook gives the whole recall
        a sub-second budget — reranking 20 candidates to pick 3 blew it.
        Tail entries were already ranked below the head by RRF and could
        only surface if the head were smaller than top_k, so the cap costs
        recall nothing the rank-gate wouldn't have cut anyway."""
        rerank = getattr(self._embedder, "rerank", None)
        if not fused or rerank is None or not used_dense:
            return fused, ("rrf" if used_dense else "fts")
        if rerank_cap > 0:
            fused = fused[:rerank_cap]
        candidates = [self._rerank_candidate(rows[mid]) for mid, _ in fused]
        scores = rerank(query, candidates)
        if scores is None:
            return fused, "rrf"
        probs = [1.0 / (1.0 + math.exp(-s)) for s in scores]
        ordered = sorted(zip([mid for mid, _ in fused], probs),
                         key=lambda x: -x[1])
        return ordered, "rerank"

    def _apply_quality(self, ordered: list[tuple[str, float]],
                       rows: dict[str, Memory]) -> list[tuple[str, float]]:
        """Re-rank the relevance ordering by each candidate's stored
        quality (`_quality_factor`). A no-op — preserving the pure
        relevance order — when `recall_quality_weighting` is off."""
        if not ordered or not settings.agent_memory.recall_quality_weighting:
            return ordered
        now = datetime.now()
        rescored = [(mid, score * _quality_factor(rows[mid], now))
                    for mid, score in ordered if mid in rows]
        rescored.sort(key=lambda x: -x[1])
        return rescored

    def _apply_topic_boost(self, ordered: list[tuple[str, float]],
                           topic_node_id: Optional[str],
                           scope: Optional[str]) -> list[tuple[str, float]]:
        """Multiply the score of candidates linked to `topic_node_id` (an
        authoritative approved-graph node) by `1 + topic_boost_weight`. A soft
        boost next to quality/intent — it reorders, never filters, so an
        unlinked but strongly-relevant memory still surfaces. A no-op when no
        topic routed or the weight is 0."""
        weight = settings.agent_memory.topic_boost_weight
        if not ordered or not topic_node_id or weight <= 0:
            return ordered
        linked = set(self.memories_for_topic_node(topic_node_id, scope=scope))
        if not linked:
            return ordered
        factor = 1.0 + weight
        rescored = [(mid, score * factor if mid in linked else score)
                    for mid, score in ordered]
        rescored.sort(key=lambda x: -x[1])
        return rescored


    def _rerank_candidate(self, row: Memory) -> dict:
        return {
            "name": row.title or row.kind,
            "description": f"{row.kind} | {row.scope} | {row.tags or ''}",
            "body": row.body,
        }

    def _bump_recall(self, ids: list[str]) -> None:
        now = _now()
        with MemorySessionLocal() as session:
            for row in session.exec(select(Memory).where(Memory.id.in_(ids))).all():
                row.recall_count = (row.recall_count or 0) + 1
                row.last_recalled = now
                session.add(row)
            session.commit()

    def _resolve_id(self, memory_id: str) -> Optional[str]:
        """Resolve an exact id, or an unambiguous id PREFIX, to a full memory
        id. The inject/recall block displays 8-char prefixes (`(memory ed630222,
        …)`), so a hand-passed `--included` id is usually a prefix. Returns None
        when it matches zero rows or is an ambiguous prefix (>1) — the caller
        must treat that as 'not found', never as a silent success."""
        with MemorySessionLocal() as session:
            if session.get(Memory, memory_id) is not None:
                return memory_id
            rows = session.exec(select(Memory.id).where(
                Memory.id.like(f"{memory_id}%")).limit(2)).all()
        return rows[0] if len(rows) == 1 else None

    def reinforce(self, memory_id: str) -> bool:
        """Bump one memory's recall counter directly, outside a `recall()`
        call. The deliberate-usefulness signal: the auto-inject hook calls
        this when a previously injected memory keeps mattering.

        Accepts a full id or an unambiguous prefix. Returns True iff a row was
        actually bumped — False when the id matched nothing or a prefix was
        ambiguous, so callers report honestly instead of assuming success."""
        full = self._resolve_id(memory_id)
        if full is None:
            return False
        self._bump_recall([full])
        return True
