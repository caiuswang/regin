"""Module-level constants and pure scoring helpers shared by the store mixins."""

from __future__ import annotations

import json
import math
import re
import uuid
from datetime import datetime
from typing import Optional

from lib.settings import settings
from lib.memory.models import Memory


# Synthetic taxonomy node collecting active memories with NO authoritative-topic
# link — the "unfiled" bucket surfaced by BOTH the WebUI taxonomy tree and the
# MCP index_* walk, so an untopiced memory is visibly pending instead of being
# invisible to every subtree. Double-underscore so it can never collide with a
# real approved-graph id. Shared from the store package (whose
# `orphaned_memory_ids` is the query that defines "orphan") so the two surfaces
# can never drift on id/label/blurb.
ORPHAN_NODE_ID = "__orphaned__"
ORPHAN_LABEL = "Orphaned (unfiled)"
ORPHAN_BLURB = ("Active memories not linked to any topic node — "
                "classify or file them below.")


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
