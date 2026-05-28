"""Experimental SkillRouter-style pattern routing.

Wraps `lib.skills.skill_router` with regin's pattern catalog: embed pattern
bodies into the `pattern_embeddings` table, then route a query through
brute-force cosine similarity (small catalog, no ANN needed) and an
optional cross-encoder rerank.

Not wired into the default `regin search` path. Reach for this when the
pattern catalog grows past ~200 overlapping items.
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import math
import os
import re
from pathlib import Path
from typing import Optional

from sqlalchemy import text as sa_text
from sqlmodel import select

from lib.skills import skill_router
from lib.activity_log import get_activity_logger as _get_activity_logger


def _patterns_log():
    return _get_activity_logger("patterns")
from lib.settings import settings
from lib.orm import SessionLocal
from lib.orm.models import DocTag, PatternDoc, PatternEmbedding, Repo, Tag
from lib.patterns.pattern_importer import _parse_frontmatter


def _body_at(file_path: str) -> Optional[str]:
    # Current writers store file_path relative to PROJECT_ROOT. Older rows
    # may still be relative to PATTERNS_DIR; check both anchors.
    for anchor in (str(settings.project_root), str(settings.patterns_dir)):
        path = (Path(anchor) / file_path).resolve()
        if path.is_file():
            try:
                return path.read_text()
            except OSError:
                return None
    return None


def _body_for(pd: PatternDoc) -> Optional[str]:
    return _body_at(pd.file_path)


def _split_frontmatter(raw: str) -> tuple[str, str]:
    """Return (frontmatter_description, body_without_frontmatter)."""
    fm, body = _parse_frontmatter(raw)
    return (fm.get("description") or "").strip(), body


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _upsert_fts(session, pd: PatternDoc, fm_description: str,
                body: str, tag_names: list[str]) -> None:
    """Refresh the `patterns_fts` row for one pattern. FTS5 doesn't have
    UPSERT semantics, so we DELETE then INSERT.
    """
    session.execute(
        sa_text("DELETE FROM patterns_fts WHERE slug = :slug"),
        {"slug": pd.slug},
    )
    session.execute(
        sa_text(
            "INSERT INTO patterns_fts(slug, title, description, "
            "category, tag_names, body) VALUES "
            "(:slug, :title, :desc, :cat, :tags, :body)"
        ),
        {
            "slug": pd.slug,
            "title": pd.title or "",
            "desc": fm_description or "",
            "cat": pd.category or "",
            "tags": ", ".join(tag_names),
            "body": body or "",
        },
    )


_FTS_MATCH_SAFE = re.compile(r"[A-Za-z0-9_]+")


def _fts_query(text: str) -> str:
    """Build a forgiving FTS5 MATCH expression from raw user input.

    Strategy: extract alphanumeric tokens, quote each so FTS5 treats them
    as literal phrases (no operator interpretation), and join with OR so
    BM25 ranks by overlap. Drops everything else, including punctuation
    that would otherwise raise `fts5: syntax error`. Returns empty string
    when no token survives — callers should fall back to "no lexical
    hits" rather than execute an empty MATCH.
    """
    tokens = _FTS_MATCH_SAFE.findall(text or "")
    if not tokens:
        return ""
    return " OR ".join(f'"{t}"' for t in tokens)


def _lexical_route(query: str, top_k: int) -> list[tuple[str, float]]:
    """Run the BM25 leg over `patterns_fts`. Returns (slug, score)
    pairs ordered by score-desc. SQLite's `bm25()` returns *negative*
    values (smaller = better), so we negate to make the score directly
    comparable across legs.
    """
    expr = _fts_query(query)
    if not expr:
        return []
    with SessionLocal() as session:
        rows = session.execute(
            sa_text(
                "SELECT slug, bm25(patterns_fts) AS s FROM patterns_fts "
                "WHERE patterns_fts MATCH :q "
                "ORDER BY s LIMIT :k"
            ),
            {"q": expr, "k": top_k},
        ).all()
    return [(slug, -float(s)) for slug, s in rows]


def _tag_names_by_doc() -> dict[int, list[str]]:
    """Fetch all tag names per pattern in a single query (avoids N+1)."""
    with SessionLocal() as session:
        stmt = (
            select(DocTag.doc_id, Tag.name)
            .join(Tag, Tag.id == DocTag.tag_id)
            .order_by(DocTag.doc_id, Tag.name)
        )
        out: dict[int, list[str]] = {}
        for doc_id, name in session.exec(stmt).all():
            out.setdefault(doc_id, []).append(name)
        return out


def _description_slot(pd: PatternDoc, fm_description: str,
                      tag_names: list[str]) -> str:
    """Build the `description` slot of the SkillRouter `name|description|body`
    document format. SkillRouter was trained on `name|description|body`, so
    we keep the shape verbatim and just populate the slot — previously the
    code passed `""` here, throwing away every metadata signal we had.
    """
    parts: list[str] = [pd.category]
    if fm_description:
        parts.append(fm_description)
    if tag_names:
        parts.append("tags: " + ", ".join(tag_names))
    return " | ".join(parts)


def _document_text(pd: PatternDoc, fm_description: str, body: str,
                   tag_names: list[str]) -> str:
    return skill_router.format_document(
        name=f"{pd.slug} {pd.title}",
        description=_description_slot(pd, fm_description, tag_names),
        body=body,
    )


def _load_pattern_index_state(session) -> tuple[list, dict]:
    """Read the docs + existing-embedding lookup in one place.

    Only iterates user-authored pattern rows — wiki rows live in the
    same table but are indexed by `lib.patterns.wiki_indexer`, which
    computes a different document shape (`_description_for_wiki`).
    Letting both indexers touch the same row would ping-pong the
    embedding hash and waste GPU cycles.
    """
    patterns = session.exec(
        select(PatternDoc).where(PatternDoc.source_kind == "pattern")
    ).all()
    existing = {
        row.pattern_id: row
        for row in session.exec(select(PatternEmbedding)).all()
    }
    return patterns, existing


def _classify_pattern_for_indexing(
    pd: "PatternDoc",
    tags_by_doc: dict[int, list[str]],
    existing: dict,
    model_id: str,
    force: bool,
) -> tuple[str, tuple]:
    """Return (kind, payload). Kinds:
      'missing'  — no body on disk; payload=()
      'skip'     — hash unchanged; payload=(pd, fm_desc, body, tag_names)
      'todo'     — needs re-embed; payload=(pd, fm_desc, body, tag_names, doc_text, hash)
    """
    raw = _body_for(pd)
    if raw is None:
        return "missing", ()
    fm_desc, body = _split_frontmatter(raw)
    tag_names = tags_by_doc.get(pd.id, [])
    doc_text = _document_text(pd, fm_desc, body, tag_names)
    h = _hash(doc_text)
    prior = existing.get(pd.id)
    if not force and prior is not None and prior.content_hash == h and prior.model_id == model_id:
        return "skip", (pd, fm_desc, body, tag_names)
    return "todo", (pd, fm_desc, body, tag_names, doc_text, h)


def _persist_fts_and_touched(
    fts_rows: list, touched_pids: list[int],
) -> None:
    skip_now = _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    with SessionLocal() as session:
        for pd, fm_desc, body, tag_names in fts_rows:
            _upsert_fts(session, pd, fm_desc, body, tag_names)
        for pid in touched_pids:
            row = session.get(PatternEmbedding, pid)
            if row is not None:
                row.updated_at = skip_now
                session.add(row)
        session.commit()


def _persist_new_embeddings(
    todo: list, vectors, model_id: str,
) -> int:
    import numpy as np
    dim = int(vectors.shape[1])
    now = _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    indexed = 0
    with SessionLocal() as session:
        for (pd, _doc_text, h), vec in zip(todo, vectors):
            blob = np.asarray(vec, dtype="float32").tobytes()
            row = session.get(PatternEmbedding, pd.id)
            if row is None:
                row = PatternEmbedding(
                    pattern_id=pd.id, content_hash=h, model_id=model_id,
                    dim=dim, vector=blob,
                )
            else:
                row.content_hash = h
                row.model_id = model_id
                row.dim = dim
                row.vector = blob
                row.updated_at = now
            session.add(row)
            indexed += 1
        session.commit()
    return indexed


def index_patterns(*, model_id: str = skill_router.EMBEDDING_MODEL_ID,
                   force: bool = False,
                   progress=None) -> dict:
    """Embed every pattern whose hash or model_id has changed.

    Returns counts: {indexed, skipped, missing}. The optional `progress`
    callable receives one-line status strings for the CLI to surface.
    """
    skill_router.ensure_deps()

    def _say(msg: str) -> None:
        if progress is not None:
            progress(msg)

    with SessionLocal() as session:
        patterns, existing = _load_pattern_index_state(session)
    tags_by_doc = _tag_names_by_doc()
    _say(f"loaded {len(patterns)} pattern rows ({len(existing)} previously embedded)")

    # `todo` carries the full enriched doc-text — the hash covers
    # description + tags + category + body in one signature. `fts_rows`
    # is unconditional: we always refresh FTS so the lexical leg stays
    # in sync even when the embedding cache skips the pattern. `touched_pids`
    # bumps `updated_at` on hash-unchanged rows so the coverage chip's
    # stale-timestamp metric clears after Re-embed.
    todo: list[tuple] = []
    fts_rows: list[tuple] = []
    touched_pids: list[int] = []
    missing = 0
    for pd in patterns:
        kind, payload = _classify_pattern_for_indexing(
            pd, tags_by_doc, existing, model_id, force,
        )
        if kind == "missing":
            missing += 1
            continue
        if kind == "skip":
            fts_rows.append(payload)
            touched_pids.append(pd.id)
            continue
        # kind == "todo"
        fts_rows.append(payload[:4])
        todo.append((payload[0], payload[4], payload[5]))

    _persist_fts_and_touched(fts_rows, touched_pids)
    _say(f"refreshed {len(fts_rows)} fts row(s)")

    skipped = len(touched_pids)
    if not todo:
        _say(f"nothing to do (skipped={skipped} missing={missing})")
        return {"indexed": 0, "skipped": skipped, "missing": missing}
    _say(f"encoding {len(todo)} pattern(s): {', '.join(pd.slug for pd, _, _ in todo)}")

    texts = [doc_text for _pd, doc_text, _h in todo]
    vectors = skill_router.embed(texts, model_id=model_id)
    _say(f"saving {len(vectors)} vectors (dim={vectors.shape[1]})")
    indexed = _persist_new_embeddings(todo, vectors, model_id)

    _patterns_log().write(
        "patterns_embedded",
        indexed=indexed, skipped=skipped, missing=missing,
        model_id=model_id, force=force,
    )
    return {"indexed": indexed, "skipped": skipped, "missing": missing}


def _load_index(model_id: str):
    """Return (pattern_ids list, fp32 matrix [N, dim])."""
    import numpy as np
    with SessionLocal() as session:
        rows = session.exec(
            select(PatternEmbedding).where(PatternEmbedding.model_id == model_id)
        ).all()
    ids = [r.pattern_id for r in rows]
    if not rows:
        return ids, np.zeros((0, 0), dtype="float32")
    dim = rows[0].dim
    mat = np.frombuffer(
        b"".join(r.vector for r in rows), dtype="float32"
    ).reshape(len(rows), dim).copy()
    return ids, mat


def _tags_by_doc(session, doc_ids: list[int]) -> dict[int, list[str]]:
    if not doc_ids:
        return {}
    tag_stmt = (
        select(DocTag.doc_id, Tag.name)
        .join(Tag, Tag.id == DocTag.tag_id)
        .where(DocTag.doc_id.in_(doc_ids))
        .order_by(DocTag.doc_id, Tag.name)
    )
    out: dict[int, list[str]] = {}
    for doc_id, name in session.exec(tag_stmt).all():
        out.setdefault(doc_id, []).append(name)
    return out


def _repo_names_by_id(session, repo_ids: set[int]) -> dict[int, str]:
    if not repo_ids:
        return {}
    out: dict[int, str] = {}
    for repo_id, name in session.exec(
        select(Repo.id, Repo.name).where(Repo.id.in_(repo_ids))
    ).all():
        out[repo_id] = name
    return out


def _wiki_headers_by_pid(
    session,
    pds: list,
    repo_name_by_id: dict[int, str],
) -> dict[int, str]:
    """Wiki rows get a one-line header sourced from the live approved
    graph (topic_id, refs, intent). Batched: one latest-snapshot read
    per repo, no extra query per result. Computed on-read so the disk
    wiki body stays plain markdown."""
    wiki_repo_ids = {
        pd.repo_id for pd in pds
        if pd.source_kind == "wiki" and pd.repo_id is not None
    }
    if not wiki_repo_ids:
        return {}
    topics_by_repo = _topics_by_repo(session, wiki_repo_ids)
    headers: dict[int, str] = {}
    for pd in pds:
        if pd.source_kind != "wiki" or pd.repo_id is None:
            continue
        header = _wiki_header(
            topics_by_repo.get(pd.repo_id, {}),
            pd.slug, repo_name_by_id.get(pd.repo_id) or "",
        )
        if header:
            headers[pd.id] = header
    return headers


def _pattern_meta(session, pattern_ids: list[int]) -> dict[int, dict]:
    if not pattern_ids:
        return {}
    stmt = select(PatternDoc).where(PatternDoc.id.in_(pattern_ids))
    pds = session.exec(stmt).all()
    tags_by_doc = _tags_by_doc(session, [pd.id for pd in pds])
    repo_name_by_id = _repo_names_by_id(
        session, {pd.repo_id for pd in pds if pd.repo_id is not None},
    )
    headers_by_pid = _wiki_headers_by_pid(session, pds, repo_name_by_id)
    return {
        pd.id: {
            "id": pd.id, "slug": pd.slug, "title": pd.title,
            "category": pd.category, "file_path": pd.file_path,
            "source_kind": pd.source_kind,
            "repo_id": pd.repo_id,
            "repo_name": repo_name_by_id.get(pd.repo_id) if pd.repo_id else None,
            "header": headers_by_pid.get(pd.id),
            "_pd": pd, "tag_names": tags_by_doc.get(pd.id, []),
        }
        for pd in pds
    }


def _topics_by_repo(session, repo_ids: set[int]) -> dict[int, dict]:
    """`{repo_id: graph["topics"]}` from each repo's latest GraphSnapshot.

    One query for the snapshots + one JSON decode per repo. Returns an
    empty dict for any repo with no snapshot (fresh-install case) — the
    header just gets skipped for those rows.
    """
    import json
    from lib.orm.models import GraphSnapshot
    out: dict[int, dict] = {}
    rows = session.exec(
        select(GraphSnapshot)
        .where(GraphSnapshot.repo_id.in_(repo_ids))
        .where(GraphSnapshot.is_latest == 1)
    ).all()
    for snap in rows:
        try:
            graph = json.loads(snap.graph_json or "{}")
        except (TypeError, ValueError):
            continue
        topics = graph.get("topics") or {}
        if isinstance(topics, dict):
            out[snap.repo_id] = topics
    return out


def _intent_segment(topic: dict) -> Optional[str]:
    """Cap intent so the header stays one screen line."""
    intent = topic.get("intent")
    if not isinstance(intent, str) or not intent.strip():
        return None
    snipped = intent.strip().splitlines()[0]
    if len(snipped) > 200:
        snipped = snipped[:197] + "..."
    return f"Intent: {snipped}"


def _refs_segment(topic: dict) -> Optional[str]:
    ref_paths = [
        r.get("path") for r in (topic.get("refs") or [])
        if isinstance(r, dict) and isinstance(r.get("path"), str)
    ]
    if not ref_paths:
        return None
    return "Refs: " + ", ".join(ref_paths[:6])


def _wiki_header(topics: dict, slug: str, repo_name: str) -> Optional[str]:
    """One-line topic context for a wiki result.

    `slug` is `wiki/<repo>/<topic_id>`; the topic_id is the third
    segment. Returns None if the topic is missing from the graph
    (e.g. wiki file orphaned across a topic rename).
    """
    parts = slug.split("/", 2)
    if len(parts) < 3:
        return None
    topic_id = parts[2]
    topic = topics.get(topic_id)
    if not isinstance(topic, dict):
        return None
    segments = [f"Topic: {topic_id}"]
    if repo_name:
        segments.append(f"Repo: {repo_name}")
    intent = _intent_segment(topic)
    if intent:
        segments.append(intent)
    refs = _refs_segment(topic)
    if refs:
        segments.append(refs)
    return " | ".join(segments)


def _rerank_candidate(pid: int, m: dict) -> dict:
    """Build a rerank candidate using the same `name|description|body`
    shape we embed with. Strips frontmatter from the body so the reranker
    isn't shown YAML noise.
    """
    raw = _body_at(m["file_path"]) or ""
    fm_desc, body = _split_frontmatter(raw)
    return {
        "pattern_id": pid,
        "name": f"{m['slug']} {m['title']}",
        "description": _description_slot(m["_pd"], fm_desc, m["tag_names"]),
        "body": body,
    }


def _dense_retrieve(query: str, retrieval_k: int,
                    embed_model_id: str) -> list[int]:
    """Run the SkillRouter dense leg. Returns pattern_ids ordered best-first."""
    import numpy as np
    ids, mat = _load_index(embed_model_id)
    if not ids:
        return []
    query_text = skill_router.format_query(query)
    qvec = skill_router.embed([query_text], model_id=embed_model_id)[0]
    sims = mat @ qvec  # L2-normalized → dot == cosine
    k = min(retrieval_k, len(ids))
    top_idx = np.argsort(-sims)[:k]
    return [ids[i] for i in top_idx]


def _slug_to_pid(slugs: list[str]) -> dict[str, int]:
    """Resolve a list of slugs to pattern_ids in one query."""
    if not slugs:
        return {}
    with SessionLocal() as session:
        rows = session.exec(
            select(PatternDoc.slug, PatternDoc.id).where(PatternDoc.slug.in_(slugs))
        ).all()
    return {slug: pid for slug, pid in rows}


def _rrf(rankings: list[list[int]], k: int = 60) -> list[tuple[int, float]]:
    """Reciprocal Rank Fusion (Cormack et al. 2009, SIGIR).

    Each ranking is an ordered list of pattern_ids (best first).
    `score(pid) = Σ 1/(k + rank_i)`. k=60 is the standard constant
    from the paper. RRF needs no per-leg weighting; it's robust to
    very different score scales (cosine vs negated BM25).
    """
    scores: dict[int, float] = {}
    for ranking in rankings:
        for rank, pid in enumerate(ranking, start=1):
            scores[pid] = scores.get(pid, 0.0) + 1.0 / (k + rank)
    return sorted(scores.items(), key=lambda x: -x[1])


def _fused_candidates(
    query: str,
    retrieval_k: int,
    *,
    use_dense: bool,
    embed_model_id: str,
) -> list[tuple[int, float]]:
    """Run dense + lexical legs, RRF-fuse, return truncated to retrieval_k."""
    if use_dense:
        skill_router.ensure_deps()
        dense_pids = _dense_retrieve(query, retrieval_k, embed_model_id)
    else:
        dense_pids = []
    lex_hits = _lexical_route(query, retrieval_k)
    pid_by_slug = _slug_to_pid([s for s, _ in lex_hits])
    lex_pids = [pid_by_slug[s] for s, _ in lex_hits if s in pid_by_slug]
    fused = _rrf([dense_pids, lex_pids], k=60)
    return fused[:retrieval_k]


def _filter_meta(
    meta: dict[int, dict], kinds: Optional[list[str]], repo: Optional[str],
) -> dict[int, dict]:
    if kinds:
        allowed = set(kinds)
        meta = {pid: m for pid, m in meta.items() if m["source_kind"] in allowed}
    if repo:
        # Only filter rows that *have* a repo association (wikis). Patterns
        # have repo_name=None and are not narrowed by this filter.
        meta = {
            pid: m for pid, m in meta.items()
            if m["repo_name"] is None or m["repo_name"] == repo
        }
    return meta


def _rerank_ordering(
    query: str,
    fused: list[tuple[int, float]],
    meta: dict[int, dict],
    rerank_model_id: str,
) -> list[tuple[int, float]]:
    """Cross-encoder rerank of fused candidates. `rerank` returns raw
    `logit[yes] - logit[no]` differences — unbounded reals that can be
    negative even for the best candidate in a small catalog. Sigmoid maps
    them to (0, 1) so the UI shows a meaningful "confidence" number;
    ordering is preserved exactly."""
    candidates = [_rerank_candidate(pid, meta[pid]) for pid, _ in fused if pid in meta]
    rr_scores = skill_router.rerank(query, candidates, model_id=rerank_model_id)
    rr_probs = [1.0 / (1.0 + math.exp(-s)) for s in rr_scores]
    return sorted(
        zip([c["pattern_id"] for c in candidates], rr_probs),
        key=lambda x: -x[1],
    )


def _build_route_results(
    ordered: list[tuple[int, float]],
    meta: dict[int, dict],
    score_kind: str,
    top_k: int,
) -> list[dict]:
    out: list[dict] = []
    for pid, score in ordered[:top_k]:
        m = meta.get(pid)
        if not m:
            continue
        # Strip private/non-JSON fields before returning.
        public = {k: v for k, v in m.items() if not k.startswith("_") and k != "tag_names"}
        out.append({**public, "score": float(score), "score_kind": score_kind})
    return out


def route(query: str, *, top_k: int = 5, retrieval_k: int = 20,
          # Default True: although a patterns-only ablation (scripts/
          # ablate_pattern_router.py) found rerank regressed top-1 on
          # 12 pattern queries, wiki queries like "approve proposal"
          # rely on it for confidence calibration RRF cannot provide.
          # See conversation 2026-05-21 for the wiki regression.
          rerank: bool = True,
          kinds: Optional[list[str]] = None,
          repo: Optional[str] = None,
          embed_model_id: str = skill_router.EMBEDDING_MODEL_ID,
          rerank_model_id: str = skill_router.RERANKER_MODEL_ID) -> list[dict]:
    """Hybrid retrieval: SkillRouter dense + SQLite FTS5 lexical, fused
    by RRF, then optionally reranked by the SkillRouter cross-encoder.
    Returns up to `top_k` result dicts each carrying `slug`, `title`,
    `category`, `file_path`, `source_kind`, `repo_name`, `score`, and
    `score_kind`.

    `kinds` filters by `source_kind` (e.g. `["pattern"]` or `["wiki"]`);
    None (default) returns both. `repo` filters wiki rows by repo name;
    patterns have no `repo_id` and pass through any `repo` filter
    unchanged (repo-scope narrows wikis, not the global pattern catalog).
    """
    from lib.settings import settings as _settings

    # `pattern_router_dense_enabled=False` short-circuits the embedding
    # leg entirely: no `ensure_deps()`, no model load, no candidate fetch.
    # Lexical-only RRF then collapses to BM25 ordering.
    use_dense = _settings.pattern_router_dense_enabled
    fused = _fused_candidates(
        query, retrieval_k, use_dense=use_dense, embed_model_id=embed_model_id,
    )
    if not fused:
        return []

    with SessionLocal() as session:
        meta = _pattern_meta(session, [pid for pid, _ in fused])
    meta = _filter_meta(meta, kinds, repo)
    fused = [(pid, s) for pid, s in fused if pid in meta]
    if not fused:
        return []

    use_rerank = (
        rerank and use_dense
        and len(fused) >= _settings.dense_rerank_min_corpus
    )
    if use_rerank:
        ordered = _rerank_ordering(query, fused, meta, rerank_model_id)
        score_kind = "rerank"
    else:
        ordered = fused
        score_kind = "rrf"
    return _build_route_results(ordered, meta, score_kind, top_k)


def index_patterns_best_effort(*, progress=None) -> Optional[dict]:
    """Embed if router deps are installed; otherwise silently return None.

    For callers that want auto-embedding to happen when the env supports
    it but must never block the main workflow.
    """
    try:
        return index_patterns(progress=progress)
    except skill_router.DependencyError:
        return None


def embedding_coverage() -> dict:
    """Return coverage stats for the **pattern** dense-search index.

    Filters out wiki rows (source_kind='wiki') because they're indexed
    by `lib.patterns.wiki_indexer.index_wikis` on a different cadence
    (auto on accept, manual via `regin wiki index`). Mixing them into
    this chip made the "Re-embed" button look broken: the button only
    re-runs `index_patterns`, so wiki rows would never converge against
    a count that included them.

    - `total`: pattern_docs row count where source_kind='pattern'
    - `embedded`: matching pattern_embeddings rows
    - `unembedded`: patterns with no embedding row
    - `stale`: patterns whose embedding is older than the pattern's
              own updated_at (i.e. user edited the source after embed)
    - `model_ids`: distinct model_ids across the whole index (patterns
              and wikis share the embedding model)
    """
    from sqlalchemy import func
    with SessionLocal() as session:
        total = int(session.exec(
            select(func.count(PatternDoc.id))
            .where(PatternDoc.source_kind == "pattern")
        ).one())
        embedded_rows = session.execute(sa_text(
            "SELECT count(*) FROM pattern_embeddings e "
            "JOIN pattern_docs p ON p.id = e.pattern_id "
            "WHERE p.source_kind = 'pattern'"
        )).scalar() or 0
        unembedded = int(session.execute(sa_text(
            "SELECT count(*) FROM pattern_docs p "
            "LEFT JOIN pattern_embeddings e ON e.pattern_id = p.id "
            "WHERE e.pattern_id IS NULL AND p.source_kind = 'pattern'"
        )).scalar() or 0)
        stale = int(session.execute(sa_text(
            "SELECT count(*) FROM pattern_docs p "
            "JOIN pattern_embeddings e ON e.pattern_id = p.id "
            "WHERE e.updated_at < p.updated_at AND p.source_kind = 'pattern'"
        )).scalar() or 0)
        model_ids = list(
            session.exec(select(PatternEmbedding.model_id).distinct()).all()
        )
    return {
        "total": total,
        "embedded": int(embedded_rows),
        "unembedded": unembedded,
        "stale": stale,
        "model_ids": model_ids,
    }
