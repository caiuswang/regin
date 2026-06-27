"""SqliteMemoryStore: CRUD, lifecycle, recall, and schema isolation."""

from __future__ import annotations

import pytest

import lib.memory as memory
from lib.memory.engine import MemorySessionLocal
from lib.memory.models import MemoryEmbedding, MemoryInput
from lib.memory.store import SqliteMemoryStore
from lib.settings import settings
from sqlmodel import select as sa_select


# ── Stub embedder shared by dense-recall tests ────────────────────────────────

class _StubEmbedder:
    """Minimal EmbeddingProvider: maps text substrings to fixed unit vectors."""

    def __init__(self, vectors_by_substring: dict):
        self._vectors = vectors_by_substring
        self.embed_calls: list[list[str]] = []

    @property
    def model_id(self) -> str:
        return "stub-dense"

    def embed(self, texts: list[str]) -> list[list[float]]:
        self.embed_calls.append(list(texts))
        out = []
        for t in texts:
            for marker, vec in self._vectors.items():
                if marker in t:
                    out.append(vec)
                    break
            else:
                out.append([0.0, 0.0, 1.0])
        return out

    # embed_queries == embed for this symmetric stub
    embed_queries = embed


def _remember(body, **kw):
    kw.setdefault("is_test", True)
    kw.setdefault("title", body[:80])  # lessons now require a (unique) title
    return memory.remember(body, **kw)


def test_remember_normalizes_and_roundtrips():
    mid = _remember("Always restart the backend after edits.",
                    kind="not-a-kind", title="Restart", tags=["backend"],
                    importance=7.5, source_trace_id="sess-1",
                    source_span_id="span-1")
    row = memory.get_store().get_dict(mid)
    assert row["kind"] == "lesson"          # unknown kind → default
    assert row["importance"] == 1.0          # clamped into [0, 1]
    assert row["tier"] == "working"
    assert row["status"] == "active"
    assert row["tags"] == ["backend"]
    assert row["source_trace_id"] == "sess-1"
    assert row["source_span_id"] == "span-1"


def test_remember_rejects_empty_body():
    with pytest.raises(ValueError):
        memory.remember("   ")


def test_remember_rejects_lesson_without_title():
    with pytest.raises(ValueError):
        memory.remember("A lesson body with no title.", kind="lesson",
                        is_test=True)
    with pytest.raises(ValueError):
        memory.remember("Whitespace title.", kind="lesson", title="   ",
                        is_test=True)


def test_remember_allows_nonlesson_without_title():
    # facts/gotchas/etc. may stay untitled — only lessons are mandatory.
    mid = memory.remember("A bare fact.", kind="fact", is_test=True)
    assert memory.get_store().get_dict(mid)["title"] is None


def test_update_rejects_blanking_a_lesson_title():
    mid = _remember("Some lesson", title="Real title")
    with pytest.raises(ValueError):
        memory.update(mid, title="   ")
    # body-only edits on a titled lesson are still fine.
    assert memory.update(mid, body="edited body")


def test_update_rejects_converting_to_titleless_lesson():
    mid = memory.remember("A bare fact.", kind="fact", is_test=True)
    with pytest.raises(ValueError):
        memory.update(mid, kind="lesson")


def test_title_from_body_takes_first_real_line_and_caps():
    from lib.memory.store import title_from_body
    assert title_from_body("## A heading rule\nmore detail") == "A heading rule"
    assert title_from_body("\n\n  spaced line  \n") == "spaced line"
    assert title_from_body("x" * 100).endswith("…")
    assert len(title_from_body("x" * 100)) == 80
    assert title_from_body("   ") == ""


def test_backfill_lesson_titles_fills_only_titleless_lessons():
    store = memory.get_store()
    # A legacy titleless lesson inserted under the radar (bypassing remember).
    from lib.memory.engine import MemorySessionLocal as _S
    from lib.memory.models import Memory
    from lib.memory.store import _now
    ts = _now()
    with _S() as s:
        s.add(Memory(id="legacy1", kind="lesson", title=None,
                     body="Restart the backend after editing server code.",
                     scope="global", veracity="unknown", is_test=1,
                     created_at=ts, updated_at=ts))
        s.add(Memory(id="legacy2", kind="lesson", title="",
                     body="Second untitled lesson body.",
                     scope="global", veracity="unknown", is_test=1,
                     created_at=ts, updated_at=ts))
        s.commit()
    titled = _remember("Already titled", title="Keep me")

    fixed = store.backfill_lesson_titles()

    assert fixed == 2
    assert store.get_dict("legacy1")["title"] == \
        "Restart the backend after editing server code."
    assert store.get_dict("legacy2")["title"] == "Second untitled lesson body."
    assert store.get_dict(titled)["title"] == "Keep me"  # untouched


def test_recall_fts_orders_by_relevance_and_reinforces():
    _remember("Playwright reuses a stale backend on :8321; restart it.",
              title="Stale backend")
    _remember("Pattern guides live under the patterns directory.")
    hits = memory.recall("playwright stale backend", mode="fts",
                         include_tests=True)
    assert hits and hits[0].memory["title"] == "Stale backend"
    assert hits[0].score_kind == "fts"
    # reinforcement: the returned hit's recall counter advanced
    row = memory.get_store().get_dict(hits[0].memory["id"])
    assert row["recall_count"] == 1 and row["last_recalled"]


def test_recall_excludes_retired_expired_and_scoped():
    visible = _remember("alpha bravo unique marker phrase")
    retired = _remember("alpha bravo unique marker phrase retired")
    memory.update(retired, status="retired")
    expired = _remember("alpha bravo unique marker phrase expired")
    memory.update(expired, valid_until="2000-01-01T00:00:00")
    other_repo = _remember("alpha bravo unique marker phrase other repo",
                           scope="repo:other")

    ids = {h.memory["id"]
           for h in memory.recall("alpha bravo unique marker", mode="fts",
                                  scope="repo:regin", include_tests=True)}
    assert visible in ids
    assert retired not in ids
    assert expired not in ids
    assert other_repo not in ids  # repo:other ∉ {global, repo:regin}


def test_recall_skips_test_rows_by_default():
    _remember("zulu yankee xray special words")
    assert memory.recall("zulu yankee xray", mode="fts") == []


def test_recall_min_overlap_gates_weak_matches():
    _remember("Restart the regin backend before running playwright specs.")
    # Shares exactly one content token ("regin") with the memory.
    weak = "implement a new search feature in regin"
    assert memory.recall(weak, mode="fts", include_tests=True,
                         min_overlap=2) == []
    # Without the gate, BM25 still surfaces it — the noise being gated.
    assert memory.recall(weak, mode="fts", include_tests=True)
    # A genuinely related prompt clears the gate.
    strong = "playwright specs hit a stale regin backend"
    assert memory.recall(strong, mode="fts", include_tests=True,
                         min_overlap=2)


def test_recall_min_overlap_adapts_to_short_queries():
    _remember("Restart the regin backend before running playwright specs.")
    # One-content-token query can't be asked for 2 overlaps; the gate
    # shrinks to the query size instead of dead-ending every short query.
    assert memory.recall("playwright", mode="fts", include_tests=True,
                         min_overlap=2)


def test_update_whitelist():
    mid = _remember("body text here")
    assert memory.update(mid, title="t2", importance=0.8)
    with pytest.raises(ValueError):
        memory.update(mid, recall_count=99)
    assert not memory.update("missing-id", title="x")


def test_supersede_retires_and_links():
    old = _remember("old fact about the deploy port")
    new = memory.supersede(old, MemoryInput(
        body="new fact about the deploy port", kind="fact", is_test=True))
    old_row = memory.get_store().get_dict(old)
    assert old_row["status"] == "retired"
    assert old_row["superseded_by"] == new
    # the retired row no longer recalls; the replacement does
    ids = {h.memory["id"] for h in memory.recall(
        "deploy port fact", mode="fts", include_tests=True)}
    assert new in ids and old not in ids


def test_restore_reactivates_superseded_and_makes_recallable():
    old = _remember("old fact about the staging port")
    new = memory.supersede(old, MemoryInput(
        body="new fact about the staging port", kind="fact", is_test=True))
    # precondition: retired + chained + invisible to recall
    pre = memory.get_store().get_dict(old)
    assert pre["status"] == "retired" and pre["superseded_by"] == new
    assert old not in {h.memory["id"] for h in memory.recall(
        "staging port fact", mode="fts", include_tests=True)}

    assert memory.restore(old) is True
    row = memory.get_store().get_dict(old)
    # reactivated AND unchained — the unchain is what restores recall visibility
    assert row["status"] == "active"
    assert row["superseded_by"] is None
    ids = {h.memory["id"] for h in memory.recall(
        "staging port fact", mode="fts", include_tests=True)}
    assert old in ids  # the replacement stays active too; both reachable


def test_restore_records_validation_and_handles_plain_retire():
    from lib.memory.models import MemoryValidation
    mid = _remember("plainly retired entry echo foxtrot")
    memory.update(mid, status="retired")  # plain retire, no supersede link
    assert memory.restore(mid) is True
    assert memory.get_store().get_dict(mid)["status"] == "active"
    with MemorySessionLocal() as session:
        actions = [v.action for v in session.exec(sa_select(MemoryValidation)
                   .where(MemoryValidation.memory_id == mid)).all()]
    assert "restored" in actions


def test_restore_missing_id_returns_false():
    assert memory.restore("does-not-exist") is False


def test_forget_cascades():
    mid = _remember("disposable entry charlie delta")
    store = memory.get_store()
    store.set_embedding(mid, [0.1, 0.2], "stub-model", "hash")
    store.record_validation(mid, validator="user", action="approved")
    assert memory.forget(mid)
    assert memory.get(mid) is None
    assert store.embedding_meta() == {}
    # FTS row gone too: no recall hit
    assert memory.recall("charlie delta", mode="fts", include_tests=True) == []


def test_stats_counts():
    _remember("one", kind="gotcha")
    _remember("two", kind="lesson")
    s = memory.stats()
    assert s["total"] == 2
    assert s["by_kind"] == {"gotcha": 1, "lesson": 1}
    assert s["by_tier"] == {"working": 2}


def test_stats_includes_embed_coverage_for_working_rows():
    """Working-tier rows are embeddable (dense recall must see fresh
    lessons before promotion), so an unembedded working row counts
    against coverage instead of being excluded from it."""
    _remember("working-only row")
    s = memory.stats()
    assert "embed_coverage" in s
    assert s["embed_coverage"] == 0.0


def test_stats_embed_coverage_after_reflect_and_embed():
    """After reflect+embed, embed_coverage reaches 1.0 for the episodic set."""
    from lib.memory.reflect import reflect

    _remember("MARKER-X first unique memory body for coverage test")
    _remember("MARKER-Y second unique memory body for coverage test")

    embedder = _StubEmbedder({
        "MARKER-X": [1.0, 0.0, 0.0],
        "MARKER-Y": [0.0, 1.0, 0.0],
    })
    store = SqliteMemoryStore(embedder=embedder)
    reflect(store, embedder=embedder)

    s = store.stats()
    assert s["embed_coverage"] == 1.0


def test_recall_dense_backfills_unembedded_rows():
    """recall(mode='auto') lazily embeds stale episodic rows before scoring."""
    from lib.memory.reflect import reflect

    mid1 = _remember("BACKFILL-A lazy embed alpha phrase")
    mid2 = _remember("BACKFILL-B lazy embed beta phrase")

    embedder = _StubEmbedder({
        "BACKFILL-A": [1.0, 0.0, 0.0],
        "BACKFILL-B": [0.0, 1.0, 0.0],
    })
    store = SqliteMemoryStore(embedder=embedder)
    # Promote both to episodic without embedding (no embedder passed to reflect).
    reflect(store, dry_run=False)
    # Pre-condition: rows are episodic but unembedded.
    assert store.embedding_meta() == {}, "pre-condition: no embeddings"

    hits = store.recall("alpha phrase", mode="auto", include_tests=True)

    # Both rows must now be embedded (backfill ran during recall).
    meta = store.embedding_meta()
    assert mid1 in meta, "BACKFILL-A should be embedded after recall"
    assert mid2 in meta, "BACKFILL-B should be embedded after recall"
    # Dense leg must have contributed (score_kind != 'fts').
    assert hits, "expected at least one hit"
    assert hits[0].score_kind in ("rrf", "rerank")


def _wipe_embeddings():
    """Delete all MemoryEmbedding rows — simulates pre-backfill state."""
    with MemorySessionLocal() as sess:
        for row in sess.exec(sa_select(MemoryEmbedding)).all():
            sess.delete(row)
        sess.commit()


def test_recall_dense_backfill_respects_cap():
    """Backfill embeds at most `cap` rows per recall call."""
    from lib.memory.reflect import reflect

    cap = 5  # small cap to keep the test fast
    # Bodies must be distinct enough that text-similarity dedup (threshold
    # 0.90) doesn't collapse them — short shared prefix, long unique suffix.
    bodies = [
        "alpha recall cap test one",
        "beta recall cap test two",
        "gamma recall cap test three",
        "delta recall cap test four",
        "epsilon recall cap test five",
        "zeta recall cap test six",
        "eta recall cap test seven",
        "theta recall cap test eight",
    ]
    total = len(bodies)
    for body in bodies:
        _remember(body)

    # Promote all rows to episodic with no embedder so dedup uses text
    # similarity only (distinct texts → no merges) and no embeddings are
    # written yet.
    store = SqliteMemoryStore(embedder=None)
    reflect(store, dry_run=False)
    episodic = store.list_memories(tier="episodic", include_tests=True,
                                   limit=100)
    assert len(episodic) == total, "pre-condition: all rows promoted"
    assert store.embedding_meta() == {}, "pre-condition: no embeddings yet"

    # Now attach an embedder and call _lazy_backfill with a small cap.
    embedder = _StubEmbedder({})
    store_with_emb = SqliteMemoryStore(embedder=embedder)
    store_with_emb._lazy_backfill(embedder, cap=cap)

    assert len(store_with_emb.embedding_meta()) == cap, (
        f"backfill should embed exactly {cap} rows, not all {total}"
    )


def test_recall_dense_fts_mode_skips_backfill():
    """mode='fts' must never trigger backfill (short-lived hook path)."""
    from lib.memory.reflect import reflect

    _remember("FTSONLY-A hook path memory item one")
    embedder = _StubEmbedder({"FTSONLY-A": [1.0, 0.0, 0.0]})
    store = SqliteMemoryStore(embedder=embedder)
    reflect(store, dry_run=False)

    _wipe_embeddings()

    store.recall("hook path memory", mode="fts", include_tests=True)
    assert store.embedding_meta() == {}, "fts mode must not trigger backfill"


def test_recall_dense_embedder_failure_degrades_gracefully():
    """An embedder that raises must not prevent recall from returning hits."""
    from lib.memory.reflect import reflect

    _remember("DEGRADE-A graceful degradation memory body")

    class _BrokenEmbedder:
        @property
        def model_id(self):
            return "broken-model"

        def embed(self, texts):
            raise RuntimeError("embedding service unavailable")

        embed_queries = embed

    store = SqliteMemoryStore(embedder=_BrokenEmbedder())
    reflect(store, dry_run=False)

    # recall must not raise; FTS leg still returns hits
    hits = store.recall("graceful degradation", mode="auto", include_tests=True)
    assert isinstance(hits, list)


def test_memory_tables_stay_off_regin_metadata(tmp_memory_db, tmp_db):
    """The decoupling that makes the engine self-initializing: memory
    tables must not register on regin's shared MetaData (else create_all
    / Alembic would try to build them into regin.db), and the memory DB
    must contain only memory tables."""
    from lib.orm.base import metadata as regin_metadata
    from lib.memory.models import memory_metadata

    assert "memories" not in regin_metadata.tables
    assert "session_spans" not in memory_metadata.tables

    _remember("force schema init")
    import sqlite3
    conn = sqlite3.connect(str(tmp_memory_db))
    try:
        names = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
    finally:
        conn.close()
    assert {"memories", "memory_embeddings", "memory_validations"} <= names
    assert "session_spans" not in names


def test_mmr_select_diversifies_near_duplicates(monkeypatch):
    """With MMR on, a near-duplicate of an already-picked memory loses its
    slot to an equally-relevant but distinct candidate; off, greedy keeps both
    duplicates. A and B share a vector (cosine 1.0); C is orthogonal. Relevance
    A > B == C, so only the diversity term can break the B/C tie."""
    a = _remember("MMR-A first candidate body")
    b = _remember("MMR-B near-duplicate of A body")
    c = _remember("MMR-C distinct candidate body")
    store = SqliteMemoryStore(embedder=_StubEmbedder({}))
    store.set_embedding(a, [1.0, 0.0, 0.0], "stub-dense", "ha")
    store.set_embedding(b, [1.0, 0.0, 0.0], "stub-dense", "hb")  # dupe of A
    store.set_embedding(c, [0.0, 1.0, 0.0], "stub-dense", "hc")  # orthogonal
    ordered = [(a, 1.0), (b, 0.9), (c, 0.9)]

    monkeypatch.setattr(settings.agent_memory, "inject_mmr_lambda", None)
    assert [m for m, _ in store._mmr_select(ordered, 2)] == [a, b]

    monkeypatch.setattr(settings.agent_memory, "inject_mmr_lambda", 0.7)
    assert [m for m, _ in store._mmr_select(ordered, 2)] == [a, c]


def test_mmr_select_noop_without_embeddings(monkeypatch):
    """MMR falls through to greedy top_k when the candidates carry no
    embeddings (the FTS path), even with λ set."""
    a = _remember("NOEMB-A lexical only candidate")
    b = _remember("NOEMB-B lexical only candidate")
    c = _remember("NOEMB-C lexical only candidate")
    store = SqliteMemoryStore(embedder=_StubEmbedder({}))
    ordered = [(a, 1.0), (b, 0.9), (c, 0.8)]
    monkeypatch.setattr(settings.agent_memory, "inject_mmr_lambda", 0.7)
    assert [m for m, _ in store._mmr_select(ordered, 2)] == [a, b]


def test_min_overlap_gate_exempts_dense_candidates():
    """A semantically-matched memory with zero token overlap must survive
    the min_overlap lexical gate (the gate exists for BM25 noise, not for
    the dense leg); the same memory is gated out on the FTS-only path."""
    from lib.memory.reflect import reflect

    _remember("ZEBRA-DENSE quux flibbertigibbet entirely disjoint wording")
    embedder = _StubEmbedder({
        "ZEBRA-DENSE": [1.0, 0.0, 0.0],
        "QUERY-MARK": [1.0, 0.0, 0.0],  # query maps onto the same vector
    })
    store = SqliteMemoryStore(embedder=embedder)
    reflect(store, embedder=embedder)  # promote + embed

    query = "QUERY-MARK totally unrelated tokens"
    dense_hits = store.recall(query, mode="auto", min_overlap=2,
                              include_tests=True, reinforce=False)
    assert any("ZEBRA-DENSE" in h.memory["body"] for h in dense_hits), \
        "dense-surfaced candidate must be exempt from the lexical gate"

    fts_hits = store.recall(query, mode="fts", min_overlap=2,
                            include_tests=True, reinforce=False)
    assert not any("ZEBRA-DENSE" in h.memory["body"] for h in fts_hits), \
        "FTS-only path keeps the lexical precision gate"


# ── Authoritative-topic links (Step 1: topic-router ↔ memory) ─────────────────

def test_authoritative_topic_link_roundtrip_and_idempotent():
    store = memory.get_store()
    mid = _remember("Restart the backend after editing hooks.")
    assert store.link_authoritative_topic(mid, "debug-hooks",
                                          source="manual") is True
    # PK dedup: a re-link refreshes source, returns False (no new row).
    assert store.link_authoritative_topic(mid, "debug-hooks",
                                          source="route") is False
    assert store.authoritative_topics_of(mid) == ["debug-hooks"]
    # Reverse lookup finds the memory under the node.
    assert mid in store.memories_for_topic_node("debug-hooks")
    assert store.unlink_authoritative_topic(mid, "debug-hooks") is True
    assert store.authoritative_topics_of(mid) == []
    assert store.unlink_authoritative_topic(mid, "debug-hooks") is False


def test_memories_for_topic_node_scope_and_status_filter():
    store = memory.get_store()
    a = _remember("alpha note", scope="repo:regin")
    b = _remember("beta note", scope="global")
    store.link_authoritative_topic(a, "topic-routing", source="manual")
    store.link_authoritative_topic(b, "topic-routing", source="manual")
    both = set(store.memories_for_topic_node("topic-routing"))
    assert both == {a, b}
    scoped = store.memories_for_topic_node("topic-routing", scope="repo:regin")
    assert scoped == [a]
    # Retired memories drop out of the recall-time lookup.
    memory.forget(a)
    assert a not in store.memories_for_topic_node("topic-routing")


def test_related_exposes_authoritative_topics():
    store = memory.get_store()
    mid = _remember("gamma note")
    store.link_authoritative_topic(mid, "debug-hooks", source="manual")
    related = store.related(mid, include_tests=True)
    assert related["authoritative_topics"] == ["debug-hooks"]


# ── Topic boost (Step 2: route-boosted recall) ───────────────────────────────

def test_topic_boost_reorders_without_filtering():
    store = memory.get_store()
    a = _remember("topic boost alpha note")
    b = _remember("topic boost beta note")
    store.link_authoritative_topic(b, "boosted-node", source="manual")
    # Equal base scores: the boost lifts the linked memory above the
    # unlinked one, but the unlinked one is NOT dropped (boost, not filter).
    ordered = store._apply_topic_boost([(a, 1.0), (b, 1.0)],
                                       "boosted-node", None)
    assert ordered[0][0] == b
    weight = settings.agent_memory.topic_boost_weight
    assert dict(ordered)[b] == pytest.approx(1.0 + weight)
    assert dict(ordered)[a] == pytest.approx(1.0)


def test_topic_boost_noop_without_node_or_link():
    store = memory.get_store()
    a = _remember("noop boost note")
    assert store._apply_topic_boost([(a, 1.0)], None, None) == [(a, 1.0)]
    assert store._apply_topic_boost(
        [(a, 1.0)], "unlinked-node", None) == [(a, 1.0)]


def test_recall_threads_boost_topic_node_id():
    store = memory.get_store()
    a = _remember("xyzzy plugh frobnicate disjoint")
    b = _remember("xyzzy plugh frobnicate disjoint")
    store.link_authoritative_topic(b, "node-x", source="manual")
    hits = store.recall("xyzzy plugh frobnicate", mode="fts",
                        include_tests=True, reinforce=False,
                        boost_topic_node_id="node-x")
    ids = [h.memory["id"] for h in hits]
    assert b in ids and a in ids        # both surface
    assert ids[0] == b                  # linked one is boosted to the top
