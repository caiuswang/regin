"""Topic-route exemplars: the query-local suppression and protection signals."""

from __future__ import annotations

from sqlmodel import select

from lib.settings import settings
from lib.memory.engine import MemorySessionLocal
from lib.memory.models import TopicExemplar
from lib.memory.store.helpers import (
    _exemplar_dict,
    _group_exemplars,
    _max_cosine_to,
    _now,
)


class ExemplarsMixin:
    # ── Topic-route exemplars (query-local suppression + protection) ──
    def add_topic_exemplars(self, session_id: str,
                            items: list[tuple[str, str]], polarity: int,
                            source: str = "auto") -> int:
        """Record `(topic_id, query)` exemplars for topic banners: `polarity`
        -1 for `fail`-graded routes (suppress), +1 for `pass`/curated ones
        (protect). Embeds distinct queries once,
        writes one `TopicExemplar` per (topic, query), trims per polarity to
        cap. No-op without an embedder (best-effort, never raises)."""
        embedder = self._embedder
        model_id = getattr(embedder, "model_id", None)
        if not items or model_id is None:
            return 0
        uniq = _group_exemplars(items)
        if not uniq:
            return 0
        embed_q = getattr(embedder, "embed_queries", embedder.embed)
        queries = list(uniq.keys())
        vecs = embed_q(queries)
        if not vecs:
            return 0
        written = self._write_topic_exemplars(session_id, model_id, queries,
                                              vecs, uniq, polarity, source)
        self._trim_topic_exemplars({t for ts in uniq.values() for t in ts},
                                   polarity)
        return written

    def add_topic_negatives(self, session_id: str,
                            items: list[tuple[str, str]],
                            source: str = "auto") -> int:
        """Back-compat: record `fail` (negative) topic exemplars."""
        return self.add_topic_exemplars(session_id, items, -1, source)

    def add_topic_positives(self, session_id: str,
                            items: list[tuple[str, str]],
                            source: str = "auto") -> int:
        """Record `pass`/curated (positive) topic exemplars that protect a
        route from suppression for similar queries."""
        return self.add_topic_exemplars(session_id, items, 1, source)

    def remove_topic_exemplars(self, topic_id: str,
                               polarity: "int | None" = None) -> int:
        """Drop a topic's exemplars (one polarity, or both). Returns rows
        removed."""
        if not topic_id:
            return 0
        with MemorySessionLocal() as session:
            stmt = select(TopicExemplar).where(
                TopicExemplar.topic_id == topic_id)
            if polarity is not None:
                stmt = stmt.where(TopicExemplar.polarity == polarity)
            rows = session.exec(stmt).all()
            for r in rows:
                session.delete(r)
            session.commit()
        return len(rows)

    # ── Per-exemplar inspection + single-operation revert ──
    def list_topic_exemplars(self, topic_id: str) -> list[dict]:
        """Every individual exemplar for one topic (newest first) — the case
        list behind the playground's drill-down: each row's id (for delete),
        query text, polarity, source, origin session, and timestamp."""
        if not topic_id:
            return []
        with MemorySessionLocal() as session:
            rows = session.exec(
                select(TopicExemplar)
                .where(TopicExemplar.topic_id == topic_id)
                .order_by(TopicExemplar.id.desc())).all()
        return [_exemplar_dict(r) for r in rows]

    def delete_exemplar(self, exemplar_id: int, kind: str) -> bool:
        """Delete one exemplar row by id — the undo for a single mislabel
        (👍 where you meant 👎), the fine-grained complement to the
        polarity-wide `remove_topic_exemplars`. `kind` is 'topic'.
        Returns True when a row was removed."""
        model = {"topic": TopicExemplar}.get(kind)
        if model is None or not exemplar_id:
            return False
        with MemorySessionLocal() as session:
            row = session.get(model, exemplar_id)
            if row is None:
                return False
            session.delete(row)
            session.commit()
        return True

    def _write_topic_exemplars(self, session_id: str, model_id: str,
                               queries: list[str], vecs, uniq, polarity: int,
                               source: str) -> int:
        import numpy as np
        now = _now()
        written = 0
        with MemorySessionLocal() as session:
            for q, vec in zip(queries, vecs):
                blob = np.asarray(vec, dtype="float32").tobytes()
                for tid in uniq[q]:
                    session.add(TopicExemplar(
                        topic_id=tid, polarity=polarity, source=source,
                        query=q, model_id=model_id, dim=len(vec), vector=blob,
                        source_session=session_id, created_at=now))
                    written += 1
            session.commit()
        return written

    def _trim_topic_exemplars(self, topic_ids: set[str], polarity: int) -> None:
        cap = settings.agent_memory.negative_max_per_memory
        if cap <= 0 or not topic_ids:
            return
        with MemorySessionLocal() as session:
            for tid in topic_ids:
                rows = session.exec(
                    select(TopicExemplar)
                    .where(TopicExemplar.topic_id == tid,
                           TopicExemplar.polarity == polarity)
                    .order_by(TopicExemplar.id.desc())).all()
                for stale in rows[cap:]:
                    session.delete(stale)
            session.commit()

    def topic_exemplar_max_sim(self, topic_id: str, q_vec, model_id: str,
                               polarity: int) -> float:
        """Max cosine in [0, 1] between `q_vec` and `topic_id`'s exemplars of
        `polarity` recorded under `model_id`; 0.0 when the topic has none."""
        import numpy as np
        if not topic_id:
            return 0.0
        with MemorySessionLocal() as session:
            rows = session.exec(
                select(TopicExemplar.vector).where(
                    TopicExemplar.topic_id == topic_id,
                    TopicExemplar.polarity == polarity,
                    TopicExemplar.model_id == model_id)).all()
        if not rows:
            return 0.0
        return _max_cosine_to(
            q_vec, (np.frombuffer(v, dtype="float32") for v in rows))

    # Back-compat: negative-only max similarity.
    def topic_negative_max_sim(self, topic_id: str, q_vec,
                               model_id: str) -> float:
        return self.topic_exemplar_max_sim(topic_id, q_vec, model_id, -1)

    def topic_route_suppressed(self, topic_id: str, query: str) -> bool:
        """True when `topic_id`'s banner should be withheld for `query` because
        the query resembles a context where the route already failed *and* not
        one where it succeeded. The query-local replacement for the binary
        fail-rate gate: embeds the query (warm-server path only) and compares to
        the topic's `fail` negatives, but a positive exemplar at least as close
        protects the route (the query-local complement to the standing human
        `allowed` pin, which also overrides). Disabled when
        `topic_negative_suppress_sim` <= 0 or no embedder is available."""
        threshold = settings.agent_memory.topic_negative_suppress_sim
        model_id = getattr(self._embedder, "model_id", None)
        if not topic_id or threshold <= 0 or model_id is None:
            return False
        if self.topic_decision(topic_id) == "allowed":
            return False
        q_vec = self._embed_query(query)
        if q_vec is None:
            return False
        neg = self.topic_exemplar_max_sim(topic_id, q_vec, model_id, -1)
        if neg < threshold:
            return False
        pos = self.topic_exemplar_max_sim(topic_id, q_vec, model_id, 1)
        return pos < neg  # a closer positive protects the route

    def _topic_exemplar_counts(
            self, topic_ids: "list[str] | None" = None) -> dict:
        """`{topic_id: {1: pos_n, -1: neg_n}}` exemplar counts, over `topic_ids`
        or every topic that has any when None. Drives the playground's "which
        topics already carry cases" read without loading the vectors."""
        from sqlalchemy import func
        stmt = (select(TopicExemplar.topic_id, TopicExemplar.polarity,
                       func.count(TopicExemplar.id))
                .group_by(TopicExemplar.topic_id, TopicExemplar.polarity))
        if topic_ids is not None:
            stmt = stmt.where(TopicExemplar.topic_id.in_(topic_ids))
        with MemorySessionLocal() as session:
            rows = session.exec(stmt).all()
        out: dict = {}
        for tid, pol, n in rows:
            out.setdefault(tid, {})[int(pol)] = int(n)
        return out

    def topic_query_signals(self, query: str,
                            topic_ids: "list[str] | None" = None) -> dict:
        """For a probe `query`, the per-topic exemplar signals that drive
        query-local routing — the read behind the topic-route playground.

        Embeds the query once (warm-server path only), then for each topic
        returns its max cosine to the topic's positive (protect) and negative
        (suppress) exemplars, the recorded counts, the human `decision`, and
        the derived `suppressed` verdict (`topic_route_suppressed`'s logic, so
        the panel shows exactly what the route hook would do). `topic_ids`
        limits the scan; None means every topic that carries an exemplar.
        Returns `{topic_id: {...}}`, empty without an embedder or a blank
        query."""
        model_id = getattr(self._embedder, "model_id", None)
        q = (query or "").strip()
        if model_id is None or not q:
            return {}
        q_vec = self._embed_query(q)
        if q_vec is None:
            return {}
        counts = self._topic_exemplar_counts(topic_ids)
        ids = topic_ids if topic_ids is not None else list(counts.keys())
        decisions = self.topic_decisions()
        return {tid: self._topic_query_signal(
                    tid, q_vec, model_id, counts.get(tid, {}),
                    decisions.get(tid))
                for tid in ids}

    def _topic_query_signal(self, tid: str, q_vec, model_id: str,
                            counts: dict, decision: "str | None") -> dict:
        """One topic's signal row for `topic_query_signals`: pos/neg max cosine
        (skipping the kNN when that polarity has no exemplars) plus the
        `suppressed` verdict mirroring `topic_route_suppressed`."""
        threshold = settings.agent_memory.topic_negative_suppress_sim
        pos_n, neg_n = counts.get(1, 0), counts.get(-1, 0)
        pos = (self.topic_exemplar_max_sim(tid, q_vec, model_id, 1)
               if pos_n else 0.0)
        neg = (self.topic_exemplar_max_sim(tid, q_vec, model_id, -1)
               if neg_n else 0.0)
        suppressed = (decision != "allowed" and threshold > 0
                      and neg >= threshold and pos < neg)
        return {"pos_sim": pos, "neg_sim": neg,
                "pos_count": pos_n, "neg_count": neg_n,
                "decision": decision, "suppressed": suppressed}
