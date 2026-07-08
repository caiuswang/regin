"""SQLite implementation of the `MemoryStore` port.

Single source of truth for reading and writing the memory DB. Unlike the
append-only `session_spans` store, memory is *mutable by design*: rows are
updated, superseded, retired, and deleted in place — that lifecycle is the
whole point of curation.

Recall mirrors `pattern_router`'s hybrid shape: an FTS5/BM25 lexical leg,
an optional brute-force-cosine dense leg over `memory_embeddings`, RRF
fusion, and an optional cross-encoder rerank — each outer stage degrading
gracefully when the injected `EmbeddingProvider` is absent or its
dependencies are missing. Embeddings are written by `reflect()` (working-
tier rows are raw on purpose), so a store that has never reflected simply
recalls FTS-only.
"""

from __future__ import annotations

import json
import math
import re
import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import or_
from sqlalchemy import text as sa_text
from sqlmodel import select

from lib.activity_log import get_activity_logger
from lib.settings import settings
from lib.memory.engine import MemorySessionLocal, memory_db_path
from lib.memory.models import (
    DEFAULT_KIND, DEFAULT_STATUS, DEFAULT_TIER, DISTILL_TAG,
    MEMORY_KINDS, MEMORY_STATUSES, MEMORY_TIERS, VERACITY_VALUES,
    InjectionEvent, Memory, MemoryAuthoritativeTopic, MemoryEdge,
    MemoryEmbedding, MemoryHit, MemoryInput, MemoryPairCheck, MemoryTopic,
    MemoryTopicMember, MemoryValidation, TopicExemplar, TopicInjection,
    TopicRouteDecision, TopicWikiRecall,
)

log = get_activity_logger("memory")

_BODY_MAX = 16_000
# Correction log trimmed to the most recent few rows per memory.
_VALIDATIONS_KEEP = 5

_UPDATABLE_FIELDS = frozenset({
    "title", "body", "kind", "tier", "scope", "tags",
    "importance", "veracity", "status", "valid_until",
    "consolidated_at", "superseded_by",
})

# Same forgiving FTS5 MATCH builder `pattern_router` uses; duplicated
# (8 lines) rather than imported so `lib/memory` stays extractable as a
# self-contained package.
_FTS_MATCH_SAFE = re.compile(r"[A-Za-z0-9_]+")


def _fts_query(text: str) -> str:
    tokens = _FTS_MATCH_SAFE.findall(text or "")
    if not tokens:
        return ""
    return " OR ".join(f'"{t}"' for t in tokens)


def _max_cosine_to(q_vec, vecs) -> float:
    """Max cosine in [0, 1] between `q_vec` and an iterable of float32 vectors,
    skipping dim mismatches; 0.0 when none match. Explicit normalisation so a
    non-normalised embedder still yields a bounded value."""
    import numpy as np
    qn = q_vec / (float(np.linalg.norm(q_vec)) or 1.0)
    best = 0.0
    for v in vecs:
        if v.shape[0] != qn.shape[0]:
            continue
        sim = float(qn @ (v / (float(np.linalg.norm(v)) or 1.0)))
        sim = max(0.0, min(1.0, sim))
        if sim > best:
            best = sim
    return best


def _clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def _group_exemplars(items: list[tuple[str, str]]) -> dict[str, list[str]]:
    """`{query: [topic_id, …]}` from `(topic_id, query)` pairs, dropping
    blanks — so each distinct query is embedded once for `add_topic_exemplars`."""
    uniq: dict[str, list[str]] = {}
    for mid, q in items:
        q = (q or "").strip()
        if mid and q:
            uniq.setdefault(q, []).append(mid)
    return uniq


def _exemplar_dict(r) -> dict:
    """Serialize one `TopicExemplar` row for an inspection
    surface — the fields a human needs to read and revert a case (the vector
    blob is deliberately omitted)."""
    return {"id": r.id, "query": r.query, "polarity": r.polarity,
            "source": r.source, "source_session": r.source_session,
            "created_at": r.created_at}


def _overlap_tokens(text: str) -> set[str]:
    """Distinct lowercase tokens (len ≥ 3, so stopwords like 'the'/'a'
    don't inflate the count) used by the `min_overlap` recall gate."""
    return {t.lower() for t in _FTS_MATCH_SAFE.findall(text or "")
            if len(t) >= 3}


# Below this many active memories, document-frequency estimates are too
# noisy to call any token "corpus-common" (a young store would mark nearly
# every token common and suppress all injects), so idf filtering stays off.
_IDF_MIN_CORPUS = 20


def _document_frequency(rows) -> "Counter":
    """Per-token document frequency across `(title, body, tags)` rows —
    how many memories each distinct content token appears in. Backs the
    idf-aware overlap gate's corpus-common-token set."""
    from collections import Counter
    df: Counter = Counter()
    for title, body, tags in rows:
        for tok in _overlap_tokens(f"{title or ''} {body} {tags or ''}"):
            df[tok] += 1
    return df


def _canonical_pair(a: str, b: str) -> tuple[str, str]:
    """Canonical (lo, hi) ordering for undirected id pairs — one row per
    pair in `memory_edges` and `memory_pair_checks`."""
    return (a, b) if a < b else (b, a)


def _now() -> str:
    return datetime.now().isoformat()


def _new_id() -> str:
    return uuid.uuid4().hex


def _normalize_choice(value: Optional[str], allowed: tuple[str, ...],
                      default: str) -> str:
    return value if value in allowed else default


def title_from_body(body: str, *, max_len: int = 80) -> str:
    """A usable lesson title derived from the body's first real line — used
    to backfill legacy titleless lessons and to title the rule-shaped
    failures `goal feedback` writes. Strips a leading markdown heading/bold
    marker, collapses whitespace, and caps the length with an ellipsis."""
    for line in (body or "").splitlines():
        line = " ".join(line.strip().lstrip("#").strip().strip("*").split())
        if line:
            return line[:max_len - 1] + "…" if len(line) > max_len else line
    return ""


def _require_lesson_title(kind: str, title: Optional[str]) -> None:
    """A `lesson` memory must carry a non-empty title — it is the one-line
    rule, the headline the taxonomy tree and lists key off. Other kinds may
    stay untitled. Raises ValueError so the API/CLI surface a clean 400."""
    if kind == "lesson" and not (title or "").strip():
        raise ValueError("a lesson memory requires a non-empty title")


def _serialize(m: Memory) -> dict:
    tags = []
    if m.tags:
        try:
            tags = json.loads(m.tags)
        except (json.JSONDecodeError, ValueError):
            tags = []
    return {
        "id": m.id, "tier": m.tier, "kind": m.kind, "title": m.title,
        "body": m.body, "scope": m.scope, "tags": tags,
        "importance": m.importance, "veracity": m.veracity,
        "source_trace_id": m.source_trace_id,
        "source_span_id": m.source_span_id,
        "source_agent_id": m.source_agent_id,
        "recall_count": m.recall_count, "last_recalled": m.last_recalled,
        "valid_until": m.valid_until, "superseded_by": m.superseded_by,
        "status": m.status, "is_test": bool(m.is_test),
        "consolidated_at": m.consolidated_at,
        "created_at": m.created_at, "updated_at": m.updated_at,
    }


def _rrf(rankings: list[list[str]], k: int = 60) -> list[tuple[str, float]]:
    """Reciprocal Rank Fusion across id rankings (best first), as in
    `pattern_router._rrf` — robust to incomparable per-leg score scales."""
    scores: dict[str, float] = {}
    for ranking in rankings:
        for rank, mid in enumerate(ranking, start=1):
            scores[mid] = scores.get(mid, 0.0) + 1.0 / (k + rank)
    return sorted(scores.items(), key=lambda x: -x[1])


_VERACITY_WEIGHT = {"true": 1.0, "false": 0.0}  # else (unknown) → 0.7


def _recency_weight(stamp: "str | None", now: datetime) -> float:
    """Exponential recency decay on a half-life (days). A memory's clock
    resets when it is *deliberately* recalled (`last_recalled`), else runs
    from creation. 1.0 when half-life is disabled or the stamp is bad."""
    half_life = settings.agent_memory.recall_recency_half_life_days
    if half_life <= 0 or not stamp:
        return 1.0
    try:
        then = datetime.fromisoformat(stamp)
    except (ValueError, TypeError):
        return 1.0
    age_days = max(0.0, (now - then).total_seconds() / 86_400.0)
    return 0.5 ** (age_days / half_life)


def _quality_factor(row: Memory, now: datetime) -> float:
    """A bounded [0.9, 1.3] multiplier on a candidate's relevance score,
    folding in how much the *store* values it — importance, veracity,
    deliberate-recall count, and recency — so a sharp, proven memory
    outranks a mundane one of equal lexical match without ever letting
    quality override relevance outright."""
    ver_w = _VERACITY_WEIGHT.get(row.veracity, 0.7)
    recall_w = min(1.0, math.log1p(row.recall_count or 0) / math.log(11))
    recency_w = _recency_weight(row.last_recalled or row.created_at, now)
    quality = (0.5 * row.importance + 0.2 * ver_w
               + 0.15 * recall_w + 0.15 * recency_w)
    return 0.9 + 0.4 * quality


def _mmr_enabled(lam, ordered: list, top_k: int, embedder) -> bool:
    """MMR fires only with a λ set, a real candidate surplus, and an embedder
    able to supply the cosine diversity term."""
    if lam is None or len(ordered) <= top_k:
        return False
    return embedder is not None and embedder.model_id is not None


def _mmr_gram(ordered: list, idx: dict, mat):
    """Pairwise cosine among the `ordered` candidates as an [n, n] matrix.
    Unembedded candidates get a zero row/column (zero-vector dot product), so
    they incur and impose no diversity penalty — relevance-only, never dropped."""
    import numpy as np
    vecs = np.zeros((len(ordered), mat.shape[1]), dtype="float32")
    for i, (mid, _) in enumerate(ordered):
        if mid in idx:
            vecs[i] = mat[idx[mid]]
    return vecs @ vecs.T


def _mmr_step_score(i: int, selected: list, gram, norm: list, lam: float) -> float:
    """MMR score of candidate `i`: λ·relevance − (1−λ)·max cosine to any
    already-selected candidate."""
    penalty = max((gram[i][j] for j in selected), default=0.0)
    return lam * norm[i] - (1.0 - lam) * float(penalty)


def _mmr_order(ordered: list, gram, lam: float, top_k: int) -> list:
    """Greedy maximal-marginal-relevance ordering. Relevance is min-max
    normalized within the pool so λ trades against cosine on a common scale;
    each pick maximizes λ·rel − (1−λ)·(similarity to the closest pick so far).
    Returns the selected ids, best first."""
    rel = [s for _, s in ordered]
    lo, hi = min(rel), max(rel)
    rng = (hi - lo) or 1.0
    norm = [(s - lo) / rng for s in rel]
    selected: list[int] = []
    remaining = list(range(len(ordered)))
    while remaining and len(selected) < top_k:
        best = max(remaining,
                   key=lambda i: _mmr_step_score(i, selected, gram, norm, lam))
        selected.append(best)
        remaining.remove(best)
    return [ordered[i][0] for i in selected]


class SqliteMemoryStore:
    """The default `MemoryStore`. An `EmbeddingProvider` may be injected
    for the dense leg + rerank; None means lexical-only recall."""

    def __init__(self, embedder=None):
        self._embedder = embedder

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

    # ── Reads ────────────────────────────────────────────────

    def get(self, memory_id: str) -> Optional[Memory]:
        with MemorySessionLocal() as session:
            return session.get(Memory, memory_id)

    def get_dict(self, memory_id: str) -> Optional[dict]:
        row = self.get(memory_id)
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

    # ── Authoritative-topic links (topic.json node ids) ──────────────

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
        `topic_node_id`; an optional `scope` filter joins to `memories`."""
        with MemorySessionLocal() as session:
            stmt = (select(MemoryAuthoritativeTopic.memory_id)
                    .join(Memory,
                          Memory.id == MemoryAuthoritativeTopic.memory_id)
                    .where(
                        MemoryAuthoritativeTopic.topic_node_id == topic_node_id,
                        Memory.status == "active"))
            if scope is not None:
                stmt = stmt.where(Memory.scope == scope)
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
                stmt = stmt.where(Memory.scope == scope)
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
        filters Memory.scope."""
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
                stmt = stmt.where(Memory.scope == scope)
            rows = session.exec(stmt).all()
        out = list(dict.fromkeys(rows))
        log.read("memory_orphans_listed", count=len(out))
        return out

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
        authoritative topic.json node) by `1 + topic_boost_weight`. A soft
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
            "db_path": memory_db_path(),
        }

    @staticmethod
    def _consolidation_debt(rows: list) -> dict:
        working_active = sum(
            1 for r in rows if r.tier == "working" and r.status == "active")
        proposed = sum(1 for r in rows if r.status == "proposed")
        return {"working_active": working_active, "proposed": proposed}


__all__ = ["SqliteMemoryStore"]
