"""Read side of `SqliteMemoryStore`: get/list, neighbours and the
reflect-harvested associative edge graph."""

from __future__ import annotations

from typing import Optional

from sqlalchemy import or_
from sqlalchemy import text as sa_text
from sqlmodel import select

from lib.activity_log import get_activity_logger
from lib.memory.engine import MemorySessionLocal
from lib.memory.models import Memory, MemoryEdge
from lib.memory.store.helpers import _canonical_pair, _now, _serialize

log = get_activity_logger("memory")


class ReadsMixin:
    # ── Reads ────────────────────────────────────────────────

    def get(self, memory_id: str) -> Optional[Memory]:
        with MemorySessionLocal() as session:
            return session.get(Memory, memory_id)

    def get_dict(self, memory_id: str) -> Optional[dict]:
        """Accepts a full id or an unambiguous PREFIX, matching how ids are
        displayed (recall hits and the inject block both show 8 chars), so a
        caller can read back exactly the id it was shown. An ambiguous prefix
        resolves to None — never to an arbitrary one of its matches."""
        row = self.get(memory_id)
        if row is None:
            full = self._resolve_id(memory_id)
            row = self.get(full) if full is not None else None
        return _serialize(row) if row is not None else None

    def _filtered_memory_stmt(self, *, tier: Optional[str],
                              status: Optional[str], kind: Optional[str],
                              scope: Optional[str], q: Optional[str],
                              include_tests: bool, sort: Optional[str] = None):
        """Shared SELECT for the curate-UI list endpoints: applies the
        tier/status/kind/scope/q filters and a deterministic order. `sort`
        picks the primary key (`recent` — default — most-recently-updated;
        `recalled` / `least_recalled` — by recall_count); `updated_at` then
        `id` always tie-break so offset pages stay stable when the primary
        key collides."""
        stmt = select(Memory)
        if tier:
            stmt = stmt.where(Memory.tier == tier)
        if status:
            stmt = stmt.where(Memory.status == status)
        if kind:
            stmt = stmt.where(Memory.kind == kind)
        if scope:
            stmt = stmt.where(Memory.scope == scope)
        if q and q.strip():
            stmt = stmt.where(or_(
                Memory.title.contains(q.strip()),
                Memory.body.contains(q.strip()),
                Memory.tags.contains(q.strip())))
        if not include_tests:
            stmt = stmt.where(Memory.is_test == 0)
        tie = (Memory.updated_at.desc(), Memory.id.desc())
        if sort == "recalled":
            return stmt.order_by(Memory.recall_count.desc(), *tie)
        if sort == "least_recalled":
            return stmt.order_by(Memory.recall_count.asc(), *tie)
        return stmt.order_by(*tie)

    def list_memories(self, *, tier: Optional[str] = None,
                      status: Optional[str] = None,
                      kind: Optional[str] = None,
                      scope: Optional[str] = None,
                      q: Optional[str] = None,
                      include_tests: bool = False,
                      limit: int = 200) -> list[dict]:
        with MemorySessionLocal() as session:
            stmt = self._filtered_memory_stmt(
                tier=tier, status=status, kind=kind, scope=scope, q=q,
                include_tests=include_tests).limit(limit)
            rows = session.exec(stmt).all()
            log.read("memories_listed", count=len(rows))
            return [_serialize(r) for r in rows]

    def list_memories_page(self, *, tier: Optional[str] = None,
                           status: Optional[str] = None,
                           kind: Optional[str] = None,
                           scope: Optional[str] = None,
                           q: Optional[str] = None,
                           include_tests: bool = False,
                           sort: Optional[str] = None,
                           page: int = 0, size: int = 50):
        """Offset-limit paginated variant for the curate UI. Returns a
        :class:`lib.utils.pagination.Page` whose items are serialized
        memory dicts (same shape as :meth:`list_memories`). `sort` selects
        the ordering (see :meth:`_filtered_memory_stmt`)."""
        from lib.utils.pagination import paginate_query_stmt
        with MemorySessionLocal() as session:
            stmt = self._filtered_memory_stmt(
                tier=tier, status=status, kind=kind, scope=scope, q=q,
                include_tests=include_tests, sort=sort)
            result = paginate_query_stmt(
                session, stmt, page=page, size=size, row_to_dict=_serialize)
        log.read("memories_listed", count=len(result.items))
        return result

    def related(self, memory_id: str, *, top_k: int = 5,
                include_tests: bool = False) -> dict:
        """Relationship view for one memory (curate UI detail pane):
        `neighbors` (embedding-nearest active memories), `supersedes` (rows
        this one retired — reverse `superseded_by` lookup), and
        `superseded_by` (the row that retired this one, if any)."""
        row = self.get(memory_id)
        if row is None:
            return {"neighbors": [], "supersedes": [], "superseded_by": None}
        with MemorySessionLocal() as session:
            reverse = session.exec(select(Memory).where(
                Memory.superseded_by == memory_id)).all()
            forward = (session.get(Memory, row.superseded_by)
                       if row.superseded_by else None)
            supersedes = [_serialize(r) for r in reverse]
            superseded_by = (_serialize(forward)
                             if forward is not None else None)
        log.read("memory_related_read", memory_id=memory_id)
        # Prefer the persisted edge graph (reflect-harvested); fall back to
        # on-demand cosine for a store that has edges disabled or has never
        # reflected since the feature landed.
        neighbors = self.edge_neighbors(memory_id, top_k=top_k,
                                        include_tests=include_tests)
        if not neighbors:
            neighbors = self.neighbors(memory_id, top_k=top_k,
                                       include_tests=include_tests)
        return {
            "neighbors": neighbors,
            "topics": self.topics_of(memory_id),
            "authoritative_topics": self.authoritative_topics_of(memory_id),
            "supersedes": supersedes,
            "superseded_by": superseded_by,
        }

    def neighbors(self, memory_id: str, *, top_k: int = 5,
                  include_tests: bool = False) -> list[dict]:
        """Embedding-cosine nearest memories to `memory_id`, excluding self,
        retired, and superseded rows. Empty when no embedding exists for the
        target under the current model."""
        model_id = self._current_model_id()
        if model_id is None:
            return []
        import numpy as np
        ids, mat = self._embedding_matrix(model_id)
        if memory_id not in ids or mat.size == 0:
            return []
        norms = np.linalg.norm(mat, axis=1, keepdims=True)
        unit = mat / np.where(norms == 0, 1.0, norms)
        sims = unit @ unit[ids.index(memory_id)]
        out: list[dict] = []
        for i in np.argsort(-sims):
            mid = ids[i]
            if mid == memory_id:
                continue
            hit = self._neighbor_hit(mid, float(sims[i]), include_tests)
            if hit is not None:
                out.append(hit)
            if len(out) >= top_k:
                break
        return out

    def _neighbor_hit(self, mid: str, similarity: float,
                      include_tests: bool) -> "dict | None":
        row = self.get(mid)
        if row is None or row.status == "retired" or row.superseded_by:
            return None
        if not include_tests and row.is_test:
            return None
        hit = _serialize(row)
        hit["similarity"] = similarity
        return hit

    # ── Edge graph (reflect-harvested associative links) ─────────

    def _edge_eligible(self, ids, include_tests: bool) -> set:
        """Of `ids`, those a `related` edge may touch: active, not retired or
        superseded, test rows only when asked."""
        out = set()
        for mid in ids:
            row = self.get(mid)
            if row is None or row.status != "active" or row.superseded_by:
                continue
            if row.is_test and not include_tests:
                continue
            out.add(mid)
        return out

    def cosine_pairs(self, *, floor: float, ceiling: float = 1.0,
                     model_id: Optional[str] = None,
                     include_tests: bool = False) -> list:
        """`(id_a, id_b, cosine)` for every pair of edge-eligible memories
        whose stored-embedding cosine sits in `[floor, ceiling)` — upper
        triangle only. Reads the persisted vectors (no re-embedding); the
        backing computation for reflect's edge harvest. `model_id` selects the
        embedding set (defaults to the store's current embedder)."""
        model_id = model_id or self._current_model_id()
        if model_id is None:
            return []
        import numpy as np
        ids, mat = self._embedding_matrix(model_id)
        if mat.size == 0:
            return []
        eligible = self._edge_eligible(ids, include_tests)
        norms = np.linalg.norm(mat, axis=1, keepdims=True)
        unit = mat / np.where(norms == 0, 1.0, norms)
        sims = unit @ unit.T
        out = []
        for i, a in enumerate(ids):
            if a not in eligible:
                continue
            for j in range(i + 1, len(ids)):
                s = float(sims[i][j])
                if ids[j] in eligible and floor <= s < ceiling:
                    out.append((a, ids[j], s))
        return out

    def replace_related_edges(self, pairs, *, kind: str = "related") -> int:
        """Rebuild the whole `kind` edge set from `pairs` (each
        `(a, b, weight)`). Idempotent: drops every existing `kind` edge, then
        inserts one canonical (`src_id` < `dst_id`) row per distinct pair,
        keeping the strongest weight. Self-pairs are skipped."""
        best: dict[tuple[str, str], float] = {}
        for a, b, w in pairs:
            if a == b:
                continue
            lo, hi = _canonical_pair(a, b)
            if (lo, hi) not in best or w > best[(lo, hi)]:
                best[(lo, hi)] = float(w)
        now = _now()
        with MemorySessionLocal() as session:
            session.execute(sa_text(
                "DELETE FROM memory_edges WHERE kind = :k"), {"k": kind})
            for (lo, hi), w in best.items():
                session.add(MemoryEdge(src_id=lo, dst_id=hi, kind=kind,
                                       weight=w, created_at=now, updated_at=now))
            session.commit()
        log.write("memory_edges_rebuilt", kind=kind, count=len(best))
        return len(best)

    def edge_neighbors(self, memory_id: str, *, kind: str = "related",
                       top_k: int = 5,
                       include_tests: bool = False) -> list[dict]:
        """Persisted-edge neighbours of `memory_id`, strongest weight first,
        skipping retired/superseded rows. The fast, no-cosine backing for the
        curate UI's Related list and recall expansion."""
        with MemorySessionLocal() as session:
            edges = session.exec(
                select(MemoryEdge).where(
                    MemoryEdge.kind == kind,
                    or_(MemoryEdge.src_id == memory_id,
                        MemoryEdge.dst_id == memory_id))
                .order_by(MemoryEdge.weight.desc())).all()
        out: list[dict] = []
        for e in edges:
            other = e.dst_id if e.src_id == memory_id else e.src_id
            hit = self._neighbor_hit(other, float(e.weight), include_tests)
            if hit is not None:
                out.append(hit)
            if len(out) >= top_k:
                break
        return out

    def list_edges(self, *, kind: str = "related",
                   limit: int = 2000) -> list[dict]:
        """All `kind` edges (for the graph view), strongest first."""
        with MemorySessionLocal() as session:
            edges = session.exec(
                select(MemoryEdge).where(MemoryEdge.kind == kind)
                .order_by(MemoryEdge.weight.desc()).limit(limit)).all()
        return [{"src": e.src_id, "dst": e.dst_id, "kind": e.kind,
                 "weight": float(e.weight)} for e in edges]
