"""Synthesis topics, authoritative-topic links, wiki-recall counters and the
orphaned-memory query."""

from __future__ import annotations

from typing import Optional

from sqlmodel import select

from lib.activity_log import get_activity_logger
from lib.memory.engine import MemorySessionLocal
from lib.memory.models import (
    Memory,
    MemoryAuthoritativeTopic,
    MemoryTopic,
    MemoryTopicMember,
    TopicWikiRecall,
)
from lib.memory.store.helpers import _new_id, _now, _serialize

log = get_activity_logger("memory")


class TopicsMixin:
    # ── Topics (named synthesis clusters) ────────────────────────

    @staticmethod
    def _serialize_topic(t: MemoryTopic) -> dict:
        return {"id": t.id, "name": t.name, "summary": t.summary,
                "summary_memory_id": t.summary_memory_id, "scope": t.scope,
                "member_count": t.member_count, "status": t.status,
                "created_at": t.created_at, "updated_at": t.updated_at}

    def create_topic(self, *, name: str, summary: Optional[str] = None,
                     summary_memory_id: Optional[str] = None,
                     scope: str = "global", member_ids=()) -> str:
        """Create a topic node grouping `member_ids` (deduped, order kept)."""
        topic_id = _new_id()
        now = _now()
        members = list(dict.fromkeys(member_ids))
        with MemorySessionLocal() as session:
            session.add(MemoryTopic(
                id=topic_id, name=(name or "")[:200], summary=(summary or None),
                summary_memory_id=summary_memory_id, scope=scope or "global",
                member_count=len(members), status="active",
                created_at=now, updated_at=now))
            for mid in members:
                session.add(MemoryTopicMember(
                    topic_id=topic_id, memory_id=mid, added_at=now))
            session.commit()
        log.write("memory_topic_created", topic_id=topic_id,
                  members=len(members))
        return topic_id

    def list_topics(self, *, limit: int = 200) -> list[dict]:
        """Active topic nodes, most-recently-updated first."""
        with MemorySessionLocal() as session:
            rows = session.exec(
                select(MemoryTopic).where(MemoryTopic.status == "active")
                .order_by(MemoryTopic.updated_at.desc()).limit(limit)).all()
        log.read("memory_topics_listed", count=len(rows))
        return [self._serialize_topic(t) for t in rows]

    def get_topic(self, topic_id: str, *,
                  include_tests: bool = False) -> Optional[dict]:
        """One topic with its (live) members serialized."""
        with MemorySessionLocal() as session:
            topic = session.get(MemoryTopic, topic_id)
            if topic is None:
                return None
            member_ids = [m.memory_id for m in session.exec(
                select(MemoryTopicMember).where(
                    MemoryTopicMember.topic_id == topic_id)).all()]
        members = []
        for mid in member_ids:
            row = self.get(mid)
            if row is None or (row.is_test and not include_tests):
                continue
            members.append(_serialize(row))
        data = self._serialize_topic(topic)
        data["members"] = members
        return data

    def topics_of(self, memory_id: str) -> list[dict]:
        """`{id, name}` of every active topic this memory belongs to."""
        with MemorySessionLocal() as session:
            rows = session.exec(
                select(MemoryTopic)
                .join(MemoryTopicMember,
                      MemoryTopicMember.topic_id == MemoryTopic.id)
                .where(MemoryTopicMember.memory_id == memory_id,
                       MemoryTopic.status == "active")).all()
        return [{"id": t.id, "name": t.name} for t in rows]

    # ── Authoritative-topic links (approved-graph node ids) ──────────

    def link_authoritative_topic(self, memory_id: str, topic_node_id: str,
                                 *, source: str = "manual") -> bool:
        """Link `memory_id` to an authoritative topic node. Idempotent on
        the (memory, node) PK — a repeat link refreshes `source`/`added_at`
        rather than erroring. Returns True when a new link was created."""
        now = _now()
        with MemorySessionLocal() as session:
            existing = session.get(
                MemoryAuthoritativeTopic, (memory_id, topic_node_id))
            if existing is not None:
                existing.source = source
                existing.added_at = now
                session.add(existing)
                session.commit()
                return False
            session.add(MemoryAuthoritativeTopic(
                memory_id=memory_id, topic_node_id=topic_node_id,
                source=source, added_at=now))
            session.commit()
        log.write("memory_authoritative_topic_linked",
                  memory_id=memory_id, topic_node_id=topic_node_id,
                  source=source)
        return True

    def unlink_authoritative_topic(self, memory_id: str,
                                   topic_node_id: str) -> bool:
        """Drop the (memory, node) link if present. Returns True if a row
        was removed."""
        with MemorySessionLocal() as session:
            row = session.get(
                MemoryAuthoritativeTopic, (memory_id, topic_node_id))
            if row is None:
                return False
            session.delete(row)
            session.commit()
        log.write("memory_authoritative_topic_unlinked",
                  memory_id=memory_id, topic_node_id=topic_node_id)
        return True

    def authoritative_topics_of(self, memory_id: str) -> list[str]:
        """Authoritative topic node ids this memory is linked to."""
        with MemorySessionLocal() as session:
            rows = session.exec(
                select(MemoryAuthoritativeTopic.topic_node_id)
                .where(MemoryAuthoritativeTopic.memory_id == memory_id)).all()
        return list(rows)

    def memories_for_topic_node(self, topic_node_id: str, *,
                                scope: Optional[str] = None) -> list[str]:
        """Active memory ids linked to an authoritative topic node — the
        recall-time lookup backing the topic boost. Index-driven on
        `topic_node_id`; an optional `scope` joins to `memories` and selects
        that scope plus `global`."""
        with MemorySessionLocal() as session:
            stmt = (select(MemoryAuthoritativeTopic.memory_id)
                    .join(Memory,
                          Memory.id == MemoryAuthoritativeTopic.memory_id)
                    .where(
                        MemoryAuthoritativeTopic.topic_node_id == topic_node_id,
                        Memory.status == "active"))
            if scope is not None:
                # Same union as `_eligible_rows`: a scope filter on a
                # retrieval path means "this repo *and* global", never
                # repo-only — else a scoped walk hides every cross-repo
                # lesson while `recall` for the same query surfaces it.
                stmt = stmt.where(Memory.scope.in_(["global", scope]))
            rows = session.exec(stmt).all()
        return list(rows)

    def memories_for_topic_subtree(self, topic_node_ids: list[str], *,
                                   scope: Optional[str] = None) -> list[str]:
        """Active memory ids linked to *any* node in `topic_node_ids` — the
        subtree generalisation of `memories_for_topic_node`, backing the
        navigation `index_fetch`. Ranked by importance then recall_count
        (most-valuable first) so a node with many memories surfaces the
        signal, not a dump; deduped order-preserving; empty input → []."""
        if not topic_node_ids:
            return []
        with MemorySessionLocal() as session:
            stmt = (select(MemoryAuthoritativeTopic.memory_id)
                    .join(Memory,
                          Memory.id == MemoryAuthoritativeTopic.memory_id)
                    .where(
                        MemoryAuthoritativeTopic.topic_node_id.in_(
                            topic_node_ids),
                        Memory.status == "active")
                    .order_by(Memory.importance.desc(),
                              Memory.recall_count.desc()))
            if scope is not None:
                # Same union as `_eligible_rows`: a scope filter on a
                # retrieval path means "this repo *and* global", never
                # repo-only — else a scoped walk hides every cross-repo
                # lesson while `recall` for the same query surfaces it.
                stmt = stmt.where(Memory.scope.in_(["global", scope]))
            rows = session.exec(stmt).all()
        return list(dict.fromkeys(rows))

    def bump_wiki_recall(self, topic_id: str, *,
                         signal: str = "exposure") -> None:
        """Increment a topic wiki's usage counter. `signal='exposure'` (an
        index_fetch surfaced the path) is the v0 event; `'read'` is reserved
        for trace-derived confirmation the file was actually Read. Upsert:
        first touch creates the row."""
        now = _now()
        with MemorySessionLocal() as session:
            row = session.get(TopicWikiRecall, (topic_id, signal))
            if row is None:
                row = TopicWikiRecall(topic_id=topic_id, signal=signal,
                                      recall_count=0)
            row.recall_count = (row.recall_count or 0) + 1
            row.last_recalled = now
            session.add(row)
            session.commit()

    def wiki_recall_stats(self, *, signal: Optional[str] = None
                          ) -> list[TopicWikiRecall]:
        """All wiki counters, most-recalled first — backs a `regin topics
        wiki-stats` readout and 'high-exposure, never-read → prune' triage."""
        with MemorySessionLocal() as session:
            stmt = select(TopicWikiRecall).order_by(
                TopicWikiRecall.recall_count.desc())
            if signal is not None:
                stmt = stmt.where(TopicWikiRecall.signal == signal)
            return list(session.exec(stmt).all())

    def wiki_read_counts(self) -> dict[str, int]:
        """Distinct-session read count per topic — the ranking key for floating
        genuinely-consulted wikis to the top of the navigation listing. Keys on
        the 'read' signal only (the agent actually opened the file), not
        'exposure' (index_fetch merely surfaced the path), so a wiki that is
        listed a lot but never read does not rank up. Topics with no read row
        are absent (caller defaults 0)."""
        with MemorySessionLocal() as session:
            rows = session.exec(
                select(TopicWikiRecall.topic_id, TopicWikiRecall.recall_count)
                .where(TopicWikiRecall.signal == "read")).all()
        return {topic_id: (count or 0) for topic_id, count in rows}

    def wiki_recall_for_topic(self, topic_id: str) -> dict:
        """One topic's wiki counters as ``{exposure, read, last_read}`` — the
        per-topic readout for the topic wiki page. `last_read` is the read
        signal's timestamp (None when never read)."""
        out = {"exposure": 0, "read": 0, "last_read": None}
        with MemorySessionLocal() as session:
            rows = session.exec(select(TopicWikiRecall).where(
                TopicWikiRecall.topic_id == topic_id)).all()
        for row in rows:
            if row.signal == "read":
                out["read"] = row.recall_count
                out["last_read"] = row.last_recalled
            elif row.signal == "exposure":
                out["exposure"] = row.recall_count
        return out

    def replace_wiki_read_counts(self, counts: dict[str, dict]) -> None:
        """Replace every `signal='read'` row with a freshly-derived set —
        SET, not increment. The read signal is recomputed from the append-only
        trace, so this is idempotent: re-running the sync reflects the current
        span log rather than double-counting. Topics absent from `counts` (their
        reads vanished, e.g. sessions pruned) have their read rows dropped.
        `counts` maps topic_id -> {'count': int, 'last_read': str|None}."""
        with MemorySessionLocal() as session:
            existing = {
                r.topic_id: r for r in session.exec(
                    select(TopicWikiRecall).where(
                        TopicWikiRecall.signal == "read")).all()}
            for topic_id, data in counts.items():
                row = existing.pop(topic_id, None)
                if row is None:
                    row = TopicWikiRecall(topic_id=topic_id, signal="read",
                                          recall_count=0)
                row.recall_count = int(data["count"])
                row.last_recalled = data.get("last_read")
                session.add(row)
            for stale in existing.values():
                session.delete(stale)
            session.commit()

    def orphaned_memory_ids(self, *,
                            scope: Optional[str] = None) -> list[str]:
        """Active memory ids with NO authoritative-topic link — the
        'unfiled' bucket the taxonomy surface exposes so a user can file
        them. Same ranking as `memories_for_topic_subtree` (importance then
        recall_count, most-valuable first); deduped order-preserving. The
        outer-join-IS-NULL keeps the un-linked rows; an optional `scope`
        selects that scope plus `global`."""
        with MemorySessionLocal() as session:
            stmt = (select(Memory.id)
                    .outerjoin(
                        MemoryAuthoritativeTopic,
                        MemoryAuthoritativeTopic.memory_id == Memory.id)
                    .where(Memory.status == "active",
                           MemoryAuthoritativeTopic.memory_id.is_(None))
                    .order_by(Memory.importance.desc(),
                              Memory.recall_count.desc()))
            if scope is not None:
                # Same union as `_eligible_rows`: a scope filter on a
                # retrieval path means "this repo *and* global", never
                # repo-only — else a scoped walk hides every cross-repo
                # lesson while `recall` for the same query surfaces it.
                stmt = stmt.where(Memory.scope.in_(["global", scope]))
            rows = session.exec(stmt).all()
        out = list(dict.fromkeys(rows))
        log.read("memory_orphans_listed", count=len(out))
        return out
