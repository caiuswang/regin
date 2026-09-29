"""Injection/engagement counters and the store-wide `stats()` summary."""

from __future__ import annotations

from sqlmodel import select

from lib.activity_log import get_activity_logger
from lib.memory.engine import MemorySessionLocal, memory_db_path
from lib.memory.models import (
    DISTILL_TAG,
    InjectionEvent,
    Memory,
    MemoryEmbedding,
)
from lib.memory.store.helpers import _now

log = get_activity_logger("memory")


class StatsMixin:
    def reinforce_resurfaced(self, session_id: str, memory_id: str) -> bool:
        """A memory injected earlier this session matched again. Reinforce it
        once (stamp `reinforced_at`); return True only on that first bump so
        a memory relevant to every prompt isn't reinforced every turn."""
        if not session_id:
            return False
        with MemorySessionLocal() as session:
            row = session.get(InjectionEvent, (session_id, memory_id))
            if row is None or row.reinforced_at is not None:
                return False
            row.reinforced_at = _now()
            session.add(row)
            session.commit()
        self._bump_recall([memory_id])
        return True

    def injection_counts(self) -> dict[str, tuple[int, int]]:
        """`{memory_id: (injected, reinforced)}` aggregated over every
        session. The always-on usefulness signal reflect's decay rule reads:
        unlike `feedback`'s 'ignored' validations (written only at grade
        time), an injection event is recorded for *every* auto-inject, so a
        memory injected often yet never reinforced is visible here even when
        no grade ever ran. One GROUP BY over the (small) event log."""
        from sqlalchemy import func
        with MemorySessionLocal() as session:
            rows = session.exec(
                select(InjectionEvent.memory_id,
                       func.count(),
                       func.count(InjectionEvent.reinforced_at))
                .group_by(InjectionEvent.memory_id)).all()
        return {mid: (int(injected), int(reinforced))
                for mid, injected, reinforced in rows}

    def engagement_counts(self) -> dict[str, tuple[int, int]]:
        """`{memory_id: (engaged, ignored)}` over every *scored* injection
        event (`feedback` stamps `engaged` 1/0 per event). The positive
        always-on signal, symmetric with `injection_counts`: read from the
        uncapped event log rather than the trimmed validation log, so a
        memory's true engaged-rate survives however often it was injected.
        Events still NULL (unscored / abstained) contribute to neither
        count. One GROUP BY over the event log."""
        from sqlalchemy import case, func
        with MemorySessionLocal() as session:
            rows = session.exec(
                select(InjectionEvent.memory_id,
                       func.sum(case((InjectionEvent.engaged == 1, 1),
                                     else_=0)),
                       func.sum(case((InjectionEvent.engaged == 0, 1),
                                     else_=0)))
                .where(InjectionEvent.engaged.is_not(None))
                .group_by(InjectionEvent.memory_id)).all()
        return {mid: (int(engaged or 0), int(ignored or 0))
                for mid, engaged, ignored in rows}

    def engagement_match_counts(self) -> dict[str, tuple[int, int, int]]:
        """`{memory_id: (engaged, soft_ignored, hard_ignored)}` over every
        scored injection event — the three-way split the decay gate needs.
        `soft_ignored` (engaged=0, matched=1) is generic downstream contact
        with no idf credit; `hard_ignored` (engaged=0, matched=0) is a memory
        whose referents never appeared at all. Reflect spares soft ignores
        (absence of *specific* evidence ≠ uselessness) and decays only on a
        run of hard ones. Symmetric with `engagement_counts`; one GROUP BY."""
        from sqlalchemy import case, func
        eng = func.sum(case((InjectionEvent.engaged == 1, 1), else_=0))
        soft = func.sum(case(((InjectionEvent.engaged == 0) &
                              (InjectionEvent.matched == 1), 1), else_=0))
        hard = func.sum(case(((InjectionEvent.engaged == 0) &
                              (InjectionEvent.matched == 0), 1), else_=0))
        with MemorySessionLocal() as session:
            rows = session.exec(
                select(InjectionEvent.memory_id, eng, soft, hard)
                .where(InjectionEvent.engaged.is_not(None))
                .group_by(InjectionEvent.memory_id)).all()
        return {mid: (int(e or 0), int(s or 0), int(h or 0))
                for mid, e, s, h in rows}

    def _current_model_id(self) -> "str | None":
        return self._embedder.model_id if self._embedder is not None else None

    @property
    def has_embedder(self) -> bool:
        """True when a real embedder backs the store. Exemplar writes are
        embedding-keyed, so this gates whether a curated case can be stored at
        all — the UI surfaces it before the user judges into a no-op."""
        return self._current_model_id() is not None

    @staticmethod
    def _embedded_ids(emb_rows: list, model_id: "str | None") -> set:
        if model_id is None:
            return {r.memory_id for r in emb_rows}
        return {r.memory_id for r in emb_rows if r.model_id == model_id}

    @staticmethod
    def _embeddable_rows(rows: list) -> list:
        # Mirrors _stale_embedding_todo's population: active rows of both
        # tiers embed (proposed rows can't be recalled, so they don't
        # count against coverage).
        return [r for r in rows if r.status == "active"]

    def _embed_coverage(self, rows: list, emb_rows: list) -> "float | None":
        """Fraction of active rows covered by the current model's
        embeddings.  None when no embeddable rows exist."""
        candidates = self._embeddable_rows(rows)
        if not candidates:
            return None
        emb_ids = self._embedded_ids(emb_rows, self._current_model_id())
        covered = sum(1 for r in candidates if r.id in emb_ids)
        return covered / len(candidates)

    @staticmethod
    def _bucket_rows(rows: list) -> tuple[dict, dict, dict, dict]:
        by_tier: dict[str, int] = {}
        by_status: dict[str, int] = {}
        by_kind: dict[str, int] = {}
        by_scope: dict[str, int] = {}
        for r in rows:
            by_tier[r.tier] = by_tier.get(r.tier, 0) + 1
            by_status[r.status] = by_status.get(r.status, 0) + 1
            by_kind[r.kind] = by_kind.get(r.kind, 0) + 1
            by_scope[r.scope] = by_scope.get(r.scope, 0) + 1
        return by_tier, by_status, by_kind, by_scope

    def distilled_memories_from_trace(self, trace_id: str) -> int:
        """Count *distill-produced* memories attributed to `trace_id` — rows
        that carry both this `source_trace_id` and the `DISTILL_TAG`
        provenance marker.

        Used by `distill_session` to detect prior distillation so a second
        run can be skipped. Counting only marked rows (not every row with this
        `source_trace_id`) is the fix for a real conflation bug: a
        `send_to_user(type=lesson)` capture during the session ALSO stamps the
        session id as `source_trace_id` (tagged `send_to_user`), so counting
        all rows made any session that emitted a lesson look already-distilled
        and permanently skipped its distill.

        Still returns 0 when a distill run's proposals were all dropped or all
        reinforced into existing rows — no new marked row survives, so the
        guard won't fire. That is intentional: the signal already exists in
        the reinforced rows, and re-running is harmless beyond LLM cost. The
        `tags LIKE '%"distill"%'` match is exact at the JSON token level (the
        surrounding quotes prevent matching a longer tag like `distillery`)."""
        with MemorySessionLocal() as session:
            rows = session.exec(
                select(Memory).where(
                    Memory.source_trace_id == trace_id,
                    Memory.tags.contains(f'"{DISTILL_TAG}"'),
                )
            ).all()
        count = len(rows)
        log.read("distilled_memories_from_trace_counted",
                 trace_id=trace_id, count=count)
        return count

    def stats(self) -> dict:
        with MemorySessionLocal() as session:
            rows = session.exec(select(Memory)).all()
            emb_rows = session.exec(select(MemoryEmbedding)).all()
        by_tier, by_status, by_kind, by_scope = self._bucket_rows(rows)
        # Active-only tier/kind buckets for the category bar: its tier and kind
        # chips filter the list to `status='active'`, so their badge counts
        # must exclude retired/proposed rows or the count and the click-through
        # disagree (a "60 working" badge over an empty active-working list).
        # The full-status buckets above stay the Doctor's corpus census.
        tier_active, _, kind_active, _ = self._bucket_rows(
            [r for r in rows if r.status == "active"])
        return {
            "total": len(rows), "embedded": len(emb_rows),
            "embed_coverage": self._embed_coverage(rows, emb_rows),
            "by_tier": by_tier, "by_status": by_status, "by_kind": by_kind,
            "by_scope": by_scope,
            "by_tier_active": tier_active, "by_kind_active": kind_active,
            # Consolidation debt: rows the reflect cycle still owes work on.
            # `working_active` is the "grows forever" figure — active working
            # rows whose only drain is a reflect() promote (by_tier can't
            # isolate it: it counts working rows of every status). `proposed`
            # is the capture backlog awaiting human approval. Both derive from
            # the rows already fetched — no extra query.
            "consolidation_debt": self._consolidation_debt(rows),
            # Active memories with no authoritative-topic link — the "unfiled"
            # census the Doctor gates its Classify action on. Own query (the
            # NULL-join can't be read off `rows`), cheap and indexed.
            "orphaned": len(self.orphaned_memory_ids()),
            "db_path": memory_db_path(),
        }

    @staticmethod
    def _consolidation_debt(rows: list) -> dict:
        working_active = sum(
            1 for r in rows if r.tier == "working" and r.status == "active")
        proposed = sum(1 for r in rows if r.status == "proposed")
        return {"working_active": working_active, "proposed": proposed}
