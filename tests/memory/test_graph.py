"""Memory graph: reflect-harvested `related` edges, synthesis topic nodes,
and opt-in recall expansion along edges."""

from __future__ import annotations

import lib.memory as memory
from lib.memory.reflect import reflect
from lib.settings import settings

from tests.memory.test_reflect import (
    StubEmbedder, StubLLM, _CLUSTER_VECS, _SYNTHESIS_JSON, _seed_cluster,
)


def _episodic(body, **kw):
    from lib.memory.models import MemoryInput
    kw.setdefault("is_test", True)
    kw.setdefault("title", body[:80])  # lessons now require a (unique) title
    return memory.get_store().remember(MemoryInput(
        body=body, tier="episodic", status="active", **kw))


# ── edges ────────────────────────────────────────────────────────────────


def test_reflect_harvests_related_edges():
    """The three clustered rows (pairwise cosine ~0.70-0.74, inside the edge
    band) yield one undirected edge per pair, persisted in memory_edges."""
    a = _episodic("MARKER-A reused backend served stale code until restart")
    b = _episodic("MARKER-B the dev server kept old routes after an edit")
    c = _episodic("MARKER-C playwright asserted against a stale backend")
    embedder = StubEmbedder(_CLUSTER_VECS)

    result = reflect(memory.get_store(), embedder=embedder)

    assert result.edges == 3
    store = memory.get_store()
    nbr_ids = {n["id"] for n in store.edge_neighbors(a, include_tests=True)}
    assert nbr_ids == {b, c}
    # undirected: the neighbour also sees the seed
    assert a in {n["id"] for n in store.edge_neighbors(b, include_tests=True)}


def test_edge_harvest_is_idempotent():
    _episodic("MARKER-A reused backend served stale code until restart")
    _episodic("MARKER-B the dev server kept old routes after an edit")
    _episodic("MARKER-C playwright asserted against a stale backend")
    embedder = StubEmbedder(_CLUSTER_VECS)

    first = reflect(memory.get_store(), embedder=embedder)
    second = reflect(memory.get_store(), embedder=embedder)

    assert first.edges == second.edges == 3
    assert len(memory.get_store().list_edges()) == 3  # rebuilt, not doubled


def test_edges_disabled_harvests_nothing(monkeypatch):
    monkeypatch.setattr(settings.agent_memory, "edges_enabled", False)
    _episodic("MARKER-A reused backend served stale code until restart")
    _episodic("MARKER-B the dev server kept old routes after an edit")
    _episodic("MARKER-C playwright asserted against a stale backend")

    result = reflect(memory.get_store(), embedder=StubEmbedder(_CLUSTER_VECS))

    assert result.edges == 0
    assert memory.get_store().list_edges() == []


def test_edges_skipped_without_embedder():
    """No embedder → no cosine graph to harvest (FTS-only stores stay flat)."""
    _episodic("MARKER-A reused backend served stale code until restart")
    _episodic("MARKER-B the dev server kept old routes after an edit")

    result = reflect(memory.get_store())  # no embedder

    assert result.edges == 0


# ── topics ───────────────────────────────────────────────────────────────


def test_synthesis_creates_topic_with_members():
    _seed_cluster()
    embedder = StubEmbedder(_CLUSTER_VECS)
    llm = StubLLM(_SYNTHESIS_JSON)

    result = reflect(memory.get_store(), embedder=embedder, llm=llm)

    assert result.synthesized == 1 and result.topics == 1
    topics = memory.get_store().list_topics()
    assert len(topics) == 1
    topic = topics[0]
    assert topic["name"] == "Restart the backend after editing server code"
    assert topic["member_count"] == 3
    assert topic["summary_memory_id"]  # the synthesised rule is the card

    detail = memory.get_store().get_topic(topic["id"], include_tests=True)
    assert len(detail["members"]) == 3


def test_topics_disabled_still_synthesizes(monkeypatch):
    monkeypatch.setattr(settings.agent_memory, "topics_enabled", False)
    _seed_cluster()
    embedder = StubEmbedder(_CLUSTER_VECS)
    llm = StubLLM(_SYNTHESIS_JSON)

    result = reflect(memory.get_store(), embedder=embedder, llm=llm)

    assert result.synthesized == 1 and result.topics == 0
    assert memory.get_store().list_topics() == []


def test_related_view_includes_topics():
    _seed_cluster()
    embedder = StubEmbedder(_CLUSTER_VECS)
    reflect(memory.get_store(), embedder=embedder, llm=StubLLM(_SYNTHESIS_JSON))

    member = next(m for m in memory.get_store().list_memories(
        include_tests=True) if "synthesis" not in (m["tags"] or []))
    rel = memory.get_store().related(member["id"], include_tests=True)
    assert rel["topics"]
    assert rel["topics"][0]["name"] == \
        "Restart the backend after editing server code"


# ── recall expansion ───────────────────────────────────────────────────────


def test_recall_expands_along_edges(monkeypatch):
    """With expansion on, a query that lexically hits one memory also pulls in
    its edge-linked neighbour that shares no query tokens."""
    monkeypatch.setattr(settings.agent_memory, "recall_expand_enabled", True)
    monkeypatch.setattr(settings.agent_memory, "recall_expand_max", 2)
    seed = _episodic("MARKER-A zebra-quokka unique-token lesson")
    nbr = _episodic("MARKER-B disjoint vocabulary about narwhals")
    _episodic("MARKER-C playwright asserted against a stale backend")
    reflect(memory.get_store(), embedder=StubEmbedder(_CLUSTER_VECS))

    hits = memory.recall("zebra-quokka unique-token", mode="fts",
                         include_tests=True, reinforce=False)
    ids = {h.memory["id"] for h in hits}
    assert seed in ids       # lexical match
    assert nbr in ids        # pulled in via the related edge


def test_recall_expansion_off_by_default():
    seed = _episodic("MARKER-A zebra-quokka unique-token lesson")
    nbr = _episodic("MARKER-B disjoint vocabulary about narwhals")
    _episodic("MARKER-C playwright asserted against a stale backend")
    reflect(memory.get_store(), embedder=StubEmbedder(_CLUSTER_VECS))

    hits = memory.recall("zebra-quokka unique-token", mode="fts",
                         include_tests=True, reinforce=False)
    ids = {h.memory["id"] for h in hits}
    assert seed in ids
    assert nbr not in ids     # no expansion without the flag
