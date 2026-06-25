"""Topic→memory cascade: the return edge of the topic↔memory link.

`reflect` synthesis already proposes topics *from* memories
(`lib/memory/topic_attach`). This is the other direction: when an approved
topic goes stale — a ref file deleted, or the topic flagged stale by content
drift — the lessons mounted on it (via `MemoryAuthoritativeTopic`) inherit
doubt. We demote their veracity `true → unknown` and stamp a `topic_drift`
validation so they surface for human re-validation at `/memory`.

A **rename is deliberately not a trigger**: relocation is not staleness, and
Phase 1 already follows renames in place without touching veracity — keeping
the strength / veracity / importance axes orthogonal. Only genuine staleness
demotes.

`restore_topic_memories` is the inverse: when a topic is refreshed/accepted,
the memories *we* demoted (identified by the `topic_drift` marker) are restored
to `true`. The link crosses two SQLite DBs by string topic-id (no FK), so
everything keys on that id via `store.memories_for_topic_node`.

Best-effort: a cascade must never break the drift pass or the accept flow.
"""

from __future__ import annotations

from sqlmodel import select

from lib.activity_log import get_activity_logger

log = get_activity_logger("memory")


# The `topic_drift` validation note is `"<topic_node_id>|<reason>"` so a later
# restore can tell WHICH topic demoted a memory — a memory linked to two topics
# must only be restored by the topic that actually demoted it, not by an
# unrelated sibling topic's refresh.
_NOTE_SEP = "|"


def _drift_note(topic_node_id: str, reason: str) -> str:
    return f"{topic_node_id}{_NOTE_SEP}{reason}"


def _drifted_by_topic(memory_id: str, topic_node_id: str) -> bool:
    """Whether THIS topic demoted this memory — i.e. it carries a `topic_drift`
    validation whose note names `topic_node_id`. Topic-scoped on purpose. The
    log is trimmed to the last few rows per memory, so a positive answer is
    proof; if the marker aged out restore stays conservative (won't fire),
    never wrongly promoting."""
    from lib.memory.engine import MemorySessionLocal
    from lib.memory.models import MemoryValidation
    with MemorySessionLocal() as session:
        notes = session.exec(
            select(MemoryValidation.note).where(
                MemoryValidation.memory_id == memory_id,
                MemoryValidation.action == "topic_drift")).all()
    return any((note or "").split(_NOTE_SEP, 1)[0] == topic_node_id
               for note in notes)


def cascade_topic_stale(store, topic_node_id: str, *, reason: str,
                        dry_run: bool = False) -> int:
    """Demote linked memories whose topic just went stale. Only `veracity ==
    'true'` rows are touched (true → unknown) — already-doubtful rows are left
    alone, which makes a re-run idempotent without leaning on the trimmed
    validation log. Importance/strength are never touched (orthogonal axes).
    Returns the number demoted. Never raises."""
    try:
        ids = store.memories_for_topic_node(topic_node_id)
        demoted = 0
        for mid in ids:
            mem = store.get(mid)
            if mem is None or mem.veracity != "true":
                continue
            demoted += 1
            if dry_run:
                continue
            store.update(mid, veracity="unknown")
            store.record_validation(mid, validator="cascade",
                                    action="topic_drift",
                                    note=_drift_note(topic_node_id, reason))
        if demoted and not dry_run:
            log.write("topic_drift_cascaded", topic_node_id=topic_node_id,
                      reason=reason, demoted=demoted)
        return demoted
    except Exception:  # noqa: BLE001 - cascade must not break drift/accept
        log.error("topic_drift_cascade_failed", topic_node_id=topic_node_id,
                  exc_info=True)
        return 0


def restore_topic_memories(store, topic_node_id: str, *,
                           dry_run: bool = False) -> int:
    """Recovery half: when a topic is refreshed, restore the memories WE
    demoted. A row qualifies only if it is currently `unknown` AND carries a
    `topic_drift` marker — so we never promote a memory that became doubtful
    for another reason. Returns the number restored. Never raises."""
    try:
        ids = store.memories_for_topic_node(topic_node_id)
        restored = 0
        for mid in ids:
            mem = store.get(mid)
            if mem is None or mem.veracity != "unknown":
                continue
            if not _drifted_by_topic(mid, topic_node_id):
                continue
            restored += 1
            if dry_run:
                continue
            store.update(mid, veracity="true")
            store.record_validation(mid, validator="cascade",
                                    action="topic_refreshed",
                                    note=topic_node_id)
        if restored and not dry_run:
            log.write("topic_memories_restored", topic_node_id=topic_node_id,
                      restored=restored)
        return restored
    except Exception:  # noqa: BLE001 - recovery must not break accept
        log.error("topic_memories_restore_failed", topic_node_id=topic_node_id,
                  exc_info=True)
        return 0


__all__ = ["cascade_topic_stale", "restore_topic_memories"]
