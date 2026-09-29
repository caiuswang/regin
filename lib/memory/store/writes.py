"""Write side of `SqliteMemoryStore`: create, update, supersede, forget,
validations, pair checks, embeddings and the FTS mirror."""

from __future__ import annotations

import json
from typing import Optional

from sqlalchemy import or_
from sqlalchemy import text as sa_text
from sqlmodel import select

from lib.activity_log import get_activity_logger
from lib.memory.engine import MemorySessionLocal
from lib.memory.models import (
    DEFAULT_KIND,
    DEFAULT_STATUS,
    DEFAULT_TIER,
    MEMORY_KINDS,
    MEMORY_STATUSES,
    MEMORY_TIERS,
    VERACITY_VALUES,
    Memory,
    MemoryAuthoritativeTopic,
    MemoryEmbedding,
    MemoryInput,
    MemoryPairCheck,
    MemoryValidation,
)
from lib.memory.store.helpers import (
    _BODY_MAX,
    _UPDATABLE_FIELDS,
    _VALIDATIONS_KEEP,
    _canonical_pair,
    _new_id,
    _normalize_choice,
    _now,
    _require_lesson_title,
    title_from_body,
)

log = get_activity_logger("memory")


class WritesMixin:
    # ── Writes ───────────────────────────────────────────────

    def remember(self, mem: MemoryInput) -> str:
        body = (mem.body or "").strip()[:_BODY_MAX]
        if not body:
            raise ValueError("memory body must be non-empty")
        kind = _normalize_choice(mem.kind, MEMORY_KINDS, DEFAULT_KIND)
        _require_lesson_title(kind, mem.title)
        memory_id = _new_id()
        now = _now()
        row = Memory(
            id=memory_id,
            tier=_normalize_choice(mem.tier, MEMORY_TIERS, DEFAULT_TIER),
            kind=kind,
            title=(mem.title or None),
            body=body,
            scope=mem.scope or "global",
            tags=json.dumps(mem.tags) if mem.tags else None,
            importance=min(max(float(mem.importance), 0.0), 1.0),
            veracity=_normalize_choice(mem.veracity, VERACITY_VALUES, "unknown"),
            status=_normalize_choice(mem.status, MEMORY_STATUSES, DEFAULT_STATUS),
            source_trace_id=mem.source_trace_id,
            source_span_id=mem.source_span_id,
            source_agent_id=mem.source_agent_id,
            is_test=1 if mem.is_test else 0,
            created_at=now, updated_at=now,
        )
        with MemorySessionLocal() as session:
            session.add(row)
            self._upsert_fts(session, row)
            session.commit()
        log.write("memory_remembered", memory_id=memory_id, kind=row.kind,
                  tier=row.tier, scope=row.scope, status=row.status)
        return memory_id

    def import_memory(self, memory_id: str, mem: MemoryInput) -> str:
        """Like `remember`, but with a caller-supplied id: inserts a new row
        under that id, or updates one in place if it already exists. The
        write-side primitive `lib.memory.tree_io.import_memory_tree` uses so
        re-importing a git-shared markdown tree is idempotent rather than
        piling up duplicate rows on every run. `created_at` is preserved on
        an update; `updated_at` always refreshes."""
        body = (mem.body or "").strip()[:_BODY_MAX]
        if not body:
            raise ValueError("memory body must be non-empty")
        kind = _normalize_choice(mem.kind, MEMORY_KINDS, DEFAULT_KIND)
        _require_lesson_title(kind, mem.title)
        now = _now()
        with MemorySessionLocal() as session:
            row = session.get(Memory, memory_id)
            is_new = row is None
            row = row or Memory(id=memory_id, created_at=now)
            row.tier = _normalize_choice(mem.tier, MEMORY_TIERS, DEFAULT_TIER)
            row.kind = kind
            row.title = mem.title or None
            row.body = body
            row.scope = mem.scope or "global"
            row.tags = json.dumps(mem.tags) if mem.tags else None
            row.importance = min(max(float(mem.importance), 0.0), 1.0)
            row.veracity = _normalize_choice(mem.veracity, VERACITY_VALUES,
                                             "unknown")
            row.status = _normalize_choice(mem.status, MEMORY_STATUSES,
                                           DEFAULT_STATUS)
            row.source_trace_id = mem.source_trace_id
            row.source_span_id = mem.source_span_id
            row.source_agent_id = mem.source_agent_id
            row.is_test = 1 if mem.is_test else 0
            row.updated_at = now
            session.add(row)
            self._upsert_fts(session, row)
            session.commit()
        log.write("memory_imported", memory_id=memory_id, kind=row.kind,
                  tier=row.tier, scope=row.scope, status=row.status,
                  new=is_new)
        return memory_id

    def update(self, memory_id: str, **fields) -> bool:
        unknown = set(fields) - _UPDATABLE_FIELDS
        if unknown:
            raise ValueError(f"non-updatable memory fields: {sorted(unknown)}")
        with MemorySessionLocal() as session:
            row = session.get(Memory, memory_id)
            if row is None:
                return False
            # Enforce the lesson-title invariant against the *resulting* row:
            # an edit may not blank out (or convert into) a titleless lesson.
            if "kind" in fields or "title" in fields:
                _require_lesson_title(
                    _normalize_choice(fields.get("kind", row.kind),
                                      MEMORY_KINDS, row.kind),
                    fields["title"] if "title" in fields else row.title)
            self._apply_updates(row, fields)
            row.updated_at = _now()
            session.add(row)
            if {"title", "body", "tags"} & set(fields):
                self._upsert_fts(session, row)
            session.commit()
        log.write("memory_updated", memory_id=memory_id,
                  fields=sorted(fields))
        return True

    def _apply_updates(self, row: Memory, fields: dict) -> None:
        for key, value in fields.items():
            if key == "tags":
                value = json.dumps(value) if value else None
            elif key == "importance":
                value = min(max(float(value), 0.0), 1.0)
            elif key == "kind":
                value = _normalize_choice(value, MEMORY_KINDS, row.kind)
            elif key == "tier":
                value = _normalize_choice(value, MEMORY_TIERS, row.tier)
            elif key == "status":
                value = _normalize_choice(value, MEMORY_STATUSES, row.status)
            elif key == "veracity":
                value = _normalize_choice(value, VERACITY_VALUES, row.veracity)
            setattr(row, key, value)

    def backfill_lesson_titles(self) -> int:
        """Give every titleless `lesson` a title derived from its body — the
        one-time repair for rows written before the title became mandatory
        (chiefly `goal feedback --fail` lessons). Returns the count fixed."""
        fixed = 0
        with MemorySessionLocal() as session:
            rows = session.exec(
                select(Memory).where(Memory.kind == "lesson")).all()
            for row in rows:
                if (row.title or "").strip():
                    continue
                derived = title_from_body(row.body)
                if not derived:
                    continue
                row.title = derived
                row.updated_at = _now()
                session.add(row)
                self._upsert_fts(session, row)
                fixed += 1
            session.commit()
        log.write("memory_titles_backfilled", count=fixed)
        return fixed

    def supersede(self, old_id: str, new: MemoryInput) -> str:
        # A supersede is a refresh, not a re-file: the replacement keeps the
        # old memory's authoritative topic placement so it stays filed under
        # the same subsystem node and keeps boosting that topic's recall.
        # Without this, a refreshed lesson falls off the taxonomy until the
        # async classifier re-routes it (or forever, if it never runs).
        inherited = self.authoritative_topics_of(old_id)
        new_id = self.remember(new)
        self.update(old_id, status="retired", superseded_by=new_id)
        for node_id in inherited:
            self.link_authoritative_topic(new_id, node_id, source="inherited")
        self.record_validation(old_id, validator="store", action="superseded",
                               note=f"superseded by {new_id}")
        return new_id

    def restore(self, memory_id: str) -> bool:
        """Bring a retired memory back to active. Reverses both retire paths:
        sets status='active' AND clears `superseded_by` — the latter is
        essential because recall hides any row with `superseded_by` set
        regardless of status, so a restore that only flipped status would
        return the memory active-but-invisible. The supersede chain's other
        half (the replacement row) is left untouched. Hard `forget`s cannot
        be restored — the row is gone. Returns False if not found."""
        with MemorySessionLocal() as session:
            row = session.get(Memory, memory_id)
            if row is None:
                return False
        self.update(memory_id, status="active", superseded_by=None)
        self.record_validation(memory_id, validator="user", action="restored")
        return True

    def forget(self, memory_id: str) -> bool:
        with MemorySessionLocal() as session:
            row = session.get(Memory, memory_id)
            if row is None:
                return False
            session.delete(row)
            emb = session.get(MemoryEmbedding, memory_id)
            if emb is not None:
                session.delete(emb)
            for v in session.exec(select(MemoryValidation).where(
                    MemoryValidation.memory_id == memory_id)).all():
                session.delete(v)
            for link in session.exec(select(MemoryAuthoritativeTopic).where(
                    MemoryAuthoritativeTopic.memory_id == memory_id)).all():
                session.delete(link)
            for check in session.exec(select(MemoryPairCheck).where(
                    or_(MemoryPairCheck.a_id == memory_id,
                        MemoryPairCheck.b_id == memory_id))).all():
                session.delete(check)
            session.execute(
                sa_text("DELETE FROM memories_fts WHERE memory_id = :id"),
                {"id": memory_id})
            session.commit()
        log.write("memory_forgotten", memory_id=memory_id)
        return True

    def record_validation(self, memory_id: str, *, validator: str,
                          action: str, note: Optional[str] = None) -> None:
        with MemorySessionLocal() as session:
            session.add(MemoryValidation(
                memory_id=memory_id, validator=validator, action=action,
                note=note, created_at=_now()))
            self._trim_validations(session, memory_id)
            session.commit()

    def pair_checked(self, a_id: str, b_id: str) -> bool:
        with MemorySessionLocal() as session:
            return session.get(
                MemoryPairCheck, _canonical_pair(a_id, b_id)) is not None

    def checked_pair_keys(self) -> set[tuple[str, str]]:
        """Every judged-pair ledger key, canonical order. The table is
        small (bounded by budgeted dream verdicts per run), so callers load
        it once per pass instead of a per-candidate lookup."""
        with MemorySessionLocal() as session:
            rows = session.exec(
                select(MemoryPairCheck.a_id, MemoryPairCheck.b_id)).all()
        return {(a, b) for a, b in rows}

    def record_pair_check(self, a_id: str, b_id: str, verdict: str) -> None:
        """Insert-only: callers consult the ledger before judging, so an
        existing row means the verdict was already bought — keep it."""
        lo, hi = _canonical_pair(a_id, b_id)
        with MemorySessionLocal() as session:
            if session.get(MemoryPairCheck, (lo, hi)) is not None:
                return
            session.add(MemoryPairCheck(a_id=lo, b_id=hi, verdict=verdict,
                                        checked_at=_now()))
            session.commit()

    def prune_pair_checks(self) -> int:
        """Drop judged-pair ledger rows whose either side no longer resolves
        to an active memory — the retention half of the dream's idempotency
        ledger. Returns the count removed."""
        with MemorySessionLocal() as session:
            active = select(Memory.id).where(Memory.status == "active")
            rows = session.exec(select(MemoryPairCheck).where(or_(
                MemoryPairCheck.a_id.not_in(active),
                MemoryPairCheck.b_id.not_in(active)))).all()
            for row in rows:
                session.delete(row)
            session.commit()
        if rows:
            log.write("memory_pair_checks_pruned", count=len(rows))
        return len(rows)

    def _trim_validations(self, session, memory_id: str) -> None:
        rows = session.exec(
            select(MemoryValidation)
            .where(MemoryValidation.memory_id == memory_id)
            .order_by(MemoryValidation.id.desc())).all()
        for stale in rows[_VALIDATIONS_KEEP - 1:]:
            session.delete(stale)

    def set_embedding(self, memory_id: str, vector: list[float],
                      model_id: str, content_hash: str) -> None:
        import numpy as np
        blob = np.asarray(vector, dtype="float32").tobytes()
        with MemorySessionLocal() as session:
            row = session.get(MemoryEmbedding, memory_id)
            if row is None:
                row = MemoryEmbedding(
                    memory_id=memory_id, model_id=model_id,
                    dim=len(vector), vector=blob,
                    content_hash=content_hash, updated_at=_now())
            else:
                row.model_id = model_id
                row.dim = len(vector)
                row.vector = blob
                row.content_hash = content_hash
                row.updated_at = _now()
            session.add(row)
            session.commit()

    def embedding_meta(self) -> dict[str, tuple[str, str]]:
        """{memory_id: (content_hash, model_id)} — lets reflect() skip
        re-embedding rows whose document text hasn't changed."""
        with MemorySessionLocal() as session:
            rows = session.exec(select(MemoryEmbedding)).all()
        return {r.memory_id: (r.content_hash, r.model_id) for r in rows}

    def _upsert_fts(self, session, row: Memory) -> None:
        session.execute(
            sa_text("DELETE FROM memories_fts WHERE memory_id = :id"),
            {"id": row.id})
        session.execute(
            sa_text("INSERT INTO memories_fts(memory_id, title, body, tags) "
                    "VALUES (:id, :title, :body, :tags)"),
            {"id": row.id, "title": row.title or "", "body": row.body,
             "tags": row.tags or ""})
