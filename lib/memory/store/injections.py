"""Per-session memory/topic injection tracking and the human gate over
topic suppression."""

from __future__ import annotations

from sqlmodel import select

from lib.activity_log import get_activity_logger
from lib.settings import settings
from lib.memory.engine import MemorySessionLocal
from lib.memory.models import (
    InjectionEvent,
    TopicExemplar,
    TopicInjection,
    TopicRouteDecision,
)
from lib.memory.store.helpers import _now

log = get_activity_logger("memory")


class InjectionsMixin:
    # ── Per-session injection tracking (auto-inject dedup + reinforce) ──
    def injected_memory_ids(self, session_id: str) -> set[str]:
        """Memory ids already auto-injected into `session_id` so far."""
        if not session_id:
            return set()
        with MemorySessionLocal() as session:
            rows = session.exec(select(InjectionEvent.memory_id).where(
                InjectionEvent.session_id == session_id)).all()
        return {r for r in rows}

    def record_injections(self, session_id: str, memory_ids: list[str],
                          query: "str | None" = None) -> None:
        """Persist that `memory_ids` were injected into `session_id`.
        Idempotent — ids already recorded for the session are skipped. `query`
        is the recall prompt this inject fired on, kept for provenance and
        engagement-feedback scoring of the inject."""
        if not session_id or not memory_ids:
            return
        now = _now()
        with MemorySessionLocal() as session:
            existing = set(session.exec(select(InjectionEvent.memory_id).where(
                InjectionEvent.session_id == session_id,
                InjectionEvent.memory_id.in_(memory_ids))).all())
            for mid in memory_ids:
                if mid in existing:
                    continue
                session.add(InjectionEvent(
                    session_id=session_id, memory_id=mid, injected_at=now,
                    query=query))
            session.commit()

    # ── Per-session topic-injection tracking (topic-routing feedback) ──
    def record_topic_injection(self, session_id: str, topic_id: str,
                               query: "str | None" = None) -> None:
        """Persist that `topic_id`'s `<topic_context>` banner was injected
        into `session_id`. Idempotent — a topic already recorded for the
        session is skipped (a prompt re-routes the same topic every turn).
        `query` is the routed prompt, kept so a `fail` verdict can become a
        topic negative (`add_topic_negatives`)."""
        if not session_id or not topic_id:
            return
        with MemorySessionLocal() as session:
            if session.get(TopicInjection, (session_id, topic_id)) is not None:
                return
            session.add(TopicInjection(
                session_id=session_id, topic_id=topic_id,
                query=query, injected_at=_now()))
            session.commit()

    def apply_topic_relevance(self, session_id: str, verdict: str) -> int:
        """Stamp the `InjectedRelated` verdict onto this session's *unscored*
        topic injections (idempotent via `scored_at`). Returns the number of
        rows stamped. The outcome half of the topic-routing loop, the analog
        of `feedback`'s engagement stamp on `InjectionEvent`."""
        if not session_id or not verdict:
            return 0
        now = _now()
        stamped = 0
        queries: list[tuple[str, str]] = []
        with MemorySessionLocal() as session:
            rows = session.exec(select(TopicInjection).where(
                TopicInjection.session_id == session_id,
                TopicInjection.scored_at.is_(None))).all()
            for row in rows:
                row.relevance = verdict
                row.scored_at = now
                session.add(row)
                stamped += 1
                if row.query:
                    queries.append((row.topic_id, row.query))
            session.commit()
        self._record_topic_exemplars(session_id, verdict, queries)
        return stamped

    def _record_topic_exemplars(self, session_id: str, verdict: str,
                                queries: list[tuple[str, str]]) -> None:
        """Turn graded topic injections into route exemplars: `fail` → a
        suppressing negative, `pass` → a protecting positive. Gated on the
        suppression feature being on. Best-effort — an exemplar write must
        never fail the grade."""
        if (not queries
                or settings.agent_memory.topic_negative_suppress_sim <= 0):
            return
        if verdict == "fail":
            polarity = -1
        elif verdict == "pass":
            polarity = 1
        else:
            return
        try:
            self.add_topic_exemplars(session_id, queries, polarity)
        except Exception:  # noqa: BLE001 — feedback is best-effort
            log.error("topic_exemplar_write_failed", exc_info=True)

    def topic_relevance_stats(self, topic_id: str) -> "tuple[int, int]":
        """`(fail, total_scored)` for one topic over every scored injection —
        the signal the recall hook's suppression gate reads. A targeted count,
        not the whole-table GROUP BY, since the gate runs per prompt."""
        if not topic_id:
            return (0, 0)
        from sqlalchemy import case, func
        with MemorySessionLocal() as session:
            row = session.exec(
                select(func.count(),
                       func.coalesce(func.sum(
                           case((TopicInjection.relevance == "fail", 1),
                                else_=0)), 0))
                .where(TopicInjection.topic_id == topic_id,
                       TopicInjection.scored_at.is_not(None))).one()
        total, fails = row
        return (int(fails), int(total))

    def list_topic_injections(self, limit: int = 200) -> "list[dict]":
        """Recent topic injections (newest first) for inspection surfaces —
        the CLI `memory topic-feedback` and the Memory view's panel.

        Each row carries `judged` ('positive'|'negative'|None): the polarity of
        the human-curated exemplar (if any) for its topic+query, so the panel's
        👍/👎 thumb re-lights after a reload instead of forgetting the click."""
        with MemorySessionLocal() as session:
            rows = session.exec(
                select(TopicInjection)
                .order_by(TopicInjection.injected_at.desc())
                .limit(limit)).all()
        injections = [{"session_id": r.session_id, "topic_id": r.topic_id,
                       "relevance": r.relevance, "query": r.query,
                       "injected_at": r.injected_at, "scored_at": r.scored_at}
                      for r in rows]
        judged = self._manual_topic_exemplar_polarities(
            {(i["topic_id"], i["query"]) for i in injections if i["query"]})
        for i in injections:
            pol = judged.get((i["topic_id"], i["query"])) if i["query"] else None
            i["judged"] = ("positive" if pol == 1
                           else "negative" if pol == -1 else None)
        return injections

    def _manual_topic_exemplar_polarities(self, pairs: set) -> dict:
        """`{(topic_id, query): polarity}` for the human-curated ('manual')
        topic exemplars matching `pairs`. Newest wins when one query was judged
        both ways (the last click stands). Empty when `pairs` is empty."""
        if not pairs:
            return {}
        topic_ids = {t for t, _ in pairs}
        with MemorySessionLocal() as session:
            rows = session.exec(
                select(TopicExemplar)
                .where(TopicExemplar.source == "manual")
                .where(TopicExemplar.topic_id.in_(topic_ids))
                .where(TopicExemplar.query.is_not(None))
                .order_by(TopicExemplar.id.asc())).all()
        out = {}
        for r in rows:
            key = (r.topic_id, r.query)
            if key in pairs:
                out[key] = r.polarity  # later (newer id) overwrites
        return out

    # ── Human gate over topic suppression (proposal → decision) ──
    def topic_decision(self, topic_id: str) -> "str | None":
        """The human's standing routing decision for `topic_id`: 'suppressed'
        (withhold), 'allowed' (pinned on), or None (auto — routes, re-
        proposable). The routing gate reads exactly this — the threshold only
        proposes, it never withholds on its own."""
        if not topic_id:
            return None
        with MemorySessionLocal() as session:
            row = session.get(TopicRouteDecision, topic_id)
        return row.decision if row is not None else None

    def topic_decisions(self) -> "dict[str, str]":
        """`{topic_id: decision}` for every topic a human has decided on."""
        with MemorySessionLocal() as session:
            rows = session.exec(select(TopicRouteDecision)).all()
        return {r.topic_id: r.decision for r in rows}

    def set_topic_decision(self, topic_id: str, decision: str,
                           note: "str | None" = None) -> None:
        """Record a human routing decision ('suppressed' | 'allowed'), or pass
        decision='auto' to clear it (back to threshold-proposed routing)."""
        if not topic_id:
            return
        if decision == "auto":
            self.clear_topic_decision(topic_id)
            return
        if decision not in ("suppressed", "allowed"):
            raise ValueError(f"unknown topic decision {decision!r}")
        with MemorySessionLocal() as session:
            row = session.get(TopicRouteDecision, topic_id)
            if row is None:
                row = TopicRouteDecision(topic_id=topic_id, decision=decision,
                                         note=note, decided_at=_now())
            else:
                row.decision = decision
                row.note = note
                row.decided_at = _now()
            session.add(row)
            session.commit()

    def clear_topic_decision(self, topic_id: str) -> None:
        """Drop a human decision so the topic returns to auto (routes, and is
        re-proposed if it is still over the fail-rate bar)."""
        if not topic_id:
            return
        with MemorySessionLocal() as session:
            row = session.get(TopicRouteDecision, topic_id)
            if row is not None:
                session.delete(row)
                session.commit()

    def topic_relevance_summary(self) -> "list[dict]":
        """Per-topic aggregate over every injection: total injects, how many
        are scored, fails, fail rate, the human `decision`, and the derived
        `status` — `suppressed`/`allowed` (a human decision) else `proposed`
        (over the fail-rate bar, awaiting sign-off) else `routing`. Sorted so
        the actionable rows (proposed, then suppressed) lead."""
        from sqlalchemy import case, func

        from lib.settings import settings
        cfg = settings.agent_memory
        scored_col = func.coalesce(func.sum(case(
            (TopicInjection.scored_at.is_not(None), 1), else_=0)), 0)
        fail_col = func.coalesce(func.sum(case(
            (TopicInjection.relevance == "fail", 1), else_=0)), 0)
        with MemorySessionLocal() as session:
            rows = session.exec(
                select(TopicInjection.topic_id, func.count(),
                       scored_col, fail_col)
                .group_by(TopicInjection.topic_id)).all()
        decisions = self.topic_decisions()
        _RANK = {"proposed": 0, "suppressed": 1, "allowed": 2, "routing": 3}
        out = []
        for topic_id, total, scored, fails in rows:
            scored, fails = int(scored), int(fails)
            rate = (fails / scored) if scored else 0.0
            decision = decisions.get(topic_id)
            over = (scored >= cfg.topic_relevance_min_scored
                    and rate >= cfg.topic_relevance_fail_rate)
            status = (decision if decision
                      else ("proposed" if over else "routing"))
            out.append({
                "topic_id": topic_id, "injections": int(total),
                "scored": scored, "fails": fails, "fail_rate": round(rate, 3),
                "decision": decision, "status": status})
        out.sort(key=lambda r: (_RANK.get(r["status"], 9),
                                -r["fail_rate"], -r["fails"]))
        return out
