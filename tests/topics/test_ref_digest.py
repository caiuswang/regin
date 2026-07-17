"""Phase 0 — TopicRefDigest table + ref-digest capture.

Covers the foundation for code-driven topic evolution: the settings block,
the new table across its schema authorities, and `capture_ref_digests`
(content-hash always, embedding optional, idempotent, missing-ref-safe).
"""

from __future__ import annotations

import hashlib
import json

from pathlib import Path

from sqlmodel import select

from lib.orm import SessionLocal
from lib.orm.models import TopicRefDigest
from lib.settings import settings
from lib.topics.core import write_split_graph
from lib.topics.ref_digest import (
    capture_all_digests,
    capture_ref_digests,
    digests_for_topic,
)
from lib.topics.snapshots import resolve_or_create_repo


class _FakeEmbedder:
    model_id = "fake-model-v1"

    def embed(self, texts):
        return [[0.1, 0.2, 0.3] for _ in texts]


def _write_graph(repo: Path, topics: dict) -> None:
    write_split_graph(repo, {"version": 1, "repo": repo.name,
                            "updated_at": "2026-01-01T00:00:00Z", "topics": topics})


def _topic(refs: list[dict]) -> dict:
    return {
        "label": "T", "intent": "t", "status": "active", "aliases": [],
        "refs": refs, "edges": [], "commands": [],
        "include_globs": [], "exclude_globs": [],
    }


def _register(repo: Path) -> int:
    return resolve_or_create_repo(str(repo)).id


# ── settings ──────────────────────────────────────────────────


def test_settings_defaults_off():
    cfg = settings.topic_evolution
    assert cfg.evolution_enabled is False
    assert cfg.mechanical_autoapply is False
    assert cfg.content_drift_cosine == 0.995
    assert cfg.drift_proposal_batch_max == 3
    assert cfg.auto_proposal_expire_days == 14


# ── table across schema authorities ───────────────────────────


def test_table_built_from_schema_sql(tmp_db):
    # tmp_db seeds db/schema.sql; the ORM model must map cleanly onto it.
    with SessionLocal() as session:
        assert session.exec(select(TopicRefDigest)).all() == []


# ── capture ───────────────────────────────────────────────────


def test_capture_hashes_each_existing_ref(fake_git_repo):
    repo = fake_git_repo
    (repo / "a.py").write_text("alpha\n")
    _write_graph(repo, {"t1": _topic([
        {"path": "README.md", "role": "overview"},
        {"path": "a.py", "role": "implementation"},
    ])})
    repo_id = _register(repo)

    written = capture_ref_digests(repo, "t1")
    assert written == 2

    rows = {d["path"]: d for d in digests_for_topic(repo_id, "t1")}
    assert set(rows) == {"README.md", "a.py"}
    expect = hashlib.sha256((repo / "a.py").read_bytes()).hexdigest()
    assert rows["a.py"]["content_hash"] == expect
    assert rows["a.py"]["role"] == "implementation"
    assert rows["a.py"]["embedding_model_id"] is None


def test_capture_is_idempotent(fake_git_repo):
    repo = fake_git_repo
    _write_graph(repo, {"t1": _topic([{"path": "README.md", "role": "overview"}])})
    repo_id = _register(repo)

    assert capture_ref_digests(repo, "t1") == 1
    first = digests_for_topic(repo_id, "t1")[0]["content_hash"]
    assert capture_ref_digests(repo, "t1") == 1  # no second row
    rows = digests_for_topic(repo_id, "t1")
    assert len(rows) == 1
    assert rows[0]["content_hash"] == first


def test_missing_ref_is_skipped_not_fatal(fake_git_repo):
    repo = fake_git_repo
    _write_graph(repo, {"t1": _topic([
        {"path": "README.md", "role": "overview"},
        {"path": "does/not/exist.py", "role": "implementation"},
    ])})
    repo_id = _register(repo)

    assert capture_ref_digests(repo, "t1") == 1
    paths = {d["path"] for d in digests_for_topic(repo_id, "t1")}
    assert paths == {"README.md"}


def test_embedding_is_optional(fake_git_repo):
    repo = fake_git_repo
    _write_graph(repo, {"t1": _topic([{"path": "README.md", "role": "overview"}])})
    repo_id = _register(repo)

    capture_ref_digests(repo, "t1", embedder=_FakeEmbedder())
    row = digests_for_topic(repo_id, "t1")[0]
    assert row["embedding_model_id"] == "fake-model-v1"
    with SessionLocal() as session:
        stored = session.exec(select(TopicRefDigest)).first()
    assert json.loads(stored.embedding_json) == [0.1, 0.2, 0.3]


def test_unregistered_repo_returns_zero(fake_git_repo):
    # No resolve_or_create_repo call → no Repo row → nothing to key on.
    _write_graph(fake_git_repo, {"t1": _topic([{"path": "README.md"}])})
    assert capture_ref_digests(fake_git_repo, "t1") == 0


def test_capture_all_covers_every_topic(fake_git_repo):
    repo = fake_git_repo
    _write_graph(repo, {
        "t1": _topic([{"path": "README.md", "role": "overview"}]),
        "t2": _topic([{"path": "missing.py"}]),
    })
    _register(repo)

    result = capture_all_digests(repo)
    assert result == {"t1": 1, "t2": 0}
