"""Capture per-topic-ref content fingerprints at wiki-write time.

The substrate Phase 3 reads to decide a topic's wiki narrative has drifted
from the code under it. For each `ref.path` on a topic we store a sha256 of
the file's current content (always) plus an optional embedding (when an
embedder is supplied). Rows live in the ORM DB's `topic_ref_digests` table,
keyed `(repo_id, topic_id, path)`, so re-capturing an unchanged file is an
idempotent upsert.

Every entry point is failure-tolerant: capturing digests must never break
the wiki-write / accept flow it hangs off, mirroring
`lib.memory.topic_attach`. Nothing here runs automatically unless
`settings.topic_evolution.evolution_enabled` is set — the caller checks.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from sqlmodel import select

from lib.activity_log import get_activity_logger
from lib.orm import SessionLocal
from lib.orm.models import Repo, TopicRefDigest
from lib.topics.graph_io import load_authoritative_graph

log = get_activity_logger("topics")


def _content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _repo_id_for_path(repo_path: str | Path) -> Optional[int]:
    """Repo.id for a registered repo path, or None when unregistered —
    digests can't be keyed without it, so the capture is skipped."""
    p = str(Path(repo_path).resolve())
    with SessionLocal() as session:
        row = session.exec(select(Repo).where(Repo.path == p)).first()
    return row.id if row is not None and row.id is not None else None


def _read_ref(repo_root: Path, path: str) -> Optional[str]:
    """File content for a ref path relative to the repo root, or None when it
    doesn't resolve (a deleted/renamed ref — skipped, not fatal)."""
    target = repo_root / path
    if not target.is_file():
        return None
    try:
        return target.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        log.error("topic_ref_read_failed", path=path, exc_info=exc)
        return None


def _embed_one(embedder, text: str) -> tuple[Optional[str], Optional[str]]:
    """(embedding_json, model_id) for one ref's content, or (None, None) when
    no embedder is supplied or it can't deliver."""
    if embedder is None or getattr(embedder, "model_id", None) is None:
        return None, None
    vecs = embedder.embed([text])
    if not vecs:
        return None, None
    return json.dumps(list(vecs[0])), embedder.model_id


def _upsert(session, *, repo_id: int, topic_id: str, path: str,
            role: Optional[str], content_hash: str,
            embedding_json: Optional[str], model_id: Optional[str],
            now: str) -> None:
    """Insert a digest row or update the existing one for its unique key."""
    existing = session.exec(
        select(TopicRefDigest).where(
            TopicRefDigest.repo_id == repo_id,
            TopicRefDigest.topic_id == topic_id,
            TopicRefDigest.path == path)).first()
    if existing is None:
        session.add(TopicRefDigest(
            repo_id=repo_id, topic_id=topic_id, path=path, role=role,
            content_hash=content_hash, embedding_json=embedding_json,
            embedding_model_id=model_id, captured_at=now))
        return
    existing.role = role
    existing.content_hash = content_hash
    existing.captured_at = now
    if embedding_json is not None:
        existing.embedding_json = embedding_json
        existing.embedding_model_id = model_id
    session.add(existing)


def _capture_topic(session, repo_root: Path, repo_id: int, topic_id: str,
                   topic: dict[str, Any], embedder, now: str) -> int:
    """Capture every resolvable ref of one topic. Returns rows written."""
    written = 0
    for ref in topic.get("refs", []):
        if not isinstance(ref, dict):
            continue
        path = ref.get("path")
        if not path:
            continue
        content = _read_ref(repo_root, path)
        if content is None:
            continue
        embedding_json, model_id = _embed_one(embedder, content)
        _upsert(session, repo_id=repo_id, topic_id=topic_id, path=path,
                role=ref.get("role"), content_hash=_content_hash(content),
                embedding_json=embedding_json, model_id=model_id, now=now)
        written += 1
    return written


def capture_ref_digests(repo_path: str | Path, topic_id: str, *,
                        embedder=None) -> int:
    """Capture digests for one topic's refs. Returns rows written (0 when the
    repo is unregistered, the topic is unknown, or it has no resolvable
    refs). Never raises — a digest failure must not break wiki-write."""
    try:
        repo_id = _repo_id_for_path(repo_path)
        if repo_id is None:
            return 0
        graph = load_authoritative_graph(repo_path)
        topic = graph.get("topics", {}).get(topic_id)
        if not topic:
            return 0
        now = datetime.now().isoformat()
        repo_root = Path(repo_path)
        with SessionLocal() as session:
            written = _capture_topic(session, repo_root, repo_id, topic_id,
                                     topic, embedder, now)
            session.commit()
        log.write("topic_ref_digests_captured", topic_id=topic_id,
                  rows=written, embedded=embedder is not None)
        return written
    except Exception:  # noqa: BLE001 - capture must never break the caller
        log.error("topic_ref_digests_capture_failed", topic_id=topic_id,
                  exc_info=True)
        return 0


def capture_all_digests(repo_path: str | Path, *, embedder=None) -> dict[str, int]:
    """Backfill digests for every topic in the repo's approved graph. Returns
    `{topic_id: rows_written}`. Best-effort per topic; one topic failing
    doesn't abort the rest. Raises `TopicGraphError` if the repo has no
    approved graph to load — a setup precondition, surfaced cleanly by the
    `regin topics digest-refs` CLI, not swallowed."""
    graph = load_authoritative_graph(repo_path)
    out: dict[str, int] = {}
    for topic_id in graph.get("topics", {}):
        out[topic_id] = capture_ref_digests(repo_path, topic_id,
                                            embedder=embedder)
    return out


def digests_for_topic(repo_id: int, topic_id: str) -> list[dict[str, Any]]:
    """Stored digest rows for one topic, as plain dicts (read helper for
    drift detection and tests). `embedding` is the parsed stored vector (or
    None when the digest was captured hash-only) so the cosine drift filter
    can read it without a second query."""
    with SessionLocal() as session:
        rows = session.exec(
            select(TopicRefDigest).where(
                TopicRefDigest.repo_id == repo_id,
                TopicRefDigest.topic_id == topic_id)).all()
    return [{
        "path": r.path, "role": r.role, "content_hash": r.content_hash,
        "embedding_model_id": r.embedding_model_id,
        "embedding": json.loads(r.embedding_json) if r.embedding_json else None,
        "captured_at": r.captured_at,
    } for r in rows]


def repo_id_for_path(repo_path: str | Path) -> Optional[int]:
    """Public alias of the repo-id resolver, for drift detection callers."""
    return _repo_id_for_path(repo_path)


__all__ = ["capture_ref_digests", "capture_all_digests", "digests_for_topic",
           "repo_id_for_path"]
