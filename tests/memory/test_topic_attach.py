"""Step 3: reflection synthesis → authoritative topic-proposal queue.

`map_cluster_to_topic` is the pure merge-vs-create decision;
`maybe_propose_authoritative` is the failure-tolerant orchestration that
embeds the rule, matches the graph, emits a human-gated proposal, and (on a
merge) links the synthesised memory to the existing node.
"""

from __future__ import annotations

import lib.memory as memory
from lib.memory import topic_attach
from lib.settings import settings


def _remember(body, **kw):
    kw.setdefault("is_test", True)
    return memory.remember(body, **kw)


# ── pure decision ────────────────────────────────────────────────────────────

def test_map_cluster_merges_above_threshold():
    summary = [1.0, 0.0, 0.0]
    nodes = [("web", [1.0, 0.0, 0.0]), ("db", [0.0, 1.0, 0.0])]
    d = topic_attach.map_cluster_to_topic(summary, nodes, threshold=0.6)
    assert d["kind"] == "merge"
    assert d["topic_node_id"] == "web"
    assert d["cosine"] == 1.0


def test_map_cluster_creates_below_threshold():
    summary = [0.0, 0.0, 1.0]               # orthogonal to every node
    nodes = [("web", [1.0, 0.0, 0.0]), ("db", [0.0, 1.0, 0.0])]
    d = topic_attach.map_cluster_to_topic(summary, nodes, threshold=0.6)
    assert d["kind"] == "create"
    assert d["topic_node_id"] is None


# ── orchestration ────────────────────────────────────────────────────────────

class _MarkerEmbedder:
    """Unit vectors keyed by the first matching substring marker."""

    model_id = "stub-attach"

    def __init__(self, vectors: dict):
        self._vectors = vectors

    def embed(self, texts):
        out = []
        for t in texts:
            for marker, vec in self._vectors.items():
                if marker in t:
                    out.append(vec)
                    break
            else:
                out.append([0.0, 0.0, 1.0])
        return out


_GRAPH = {"topics": {
    "web": {"label": "Web", "intent": "Flask routes",
            "refs": [{"path": "web/app.py", "role": "entrypoint"}]},
    "db": {"label": "Database", "intent": "sqlite layer",
           "refs": [{"path": "lib/orm/engine.py", "role": "schema"}]},
}}


def _enable(monkeypatch):
    monkeypatch.setattr(settings.agent_memory,
                        "reflect_proposes_authoritative_topics", True)
    monkeypatch.setattr("lib.topics.route.load_authoritative_graph",
                        lambda repo: _GRAPH)


def _proposal_run(proposal_id):
    from lib.orm.engine import SessionLocal
    from lib.orm.models.proposals import ProposalRun
    with SessionLocal() as s:
        return s.get(ProposalRun, proposal_id)


def test_merge_proposal_links_memory_and_lands_in_queue(monkeypatch):
    _enable(monkeypatch)
    embedder = _MarkerEmbedder({"Flask": [1.0, 0.0, 0.0],
                                "sqlite": [0.0, 1.0, 0.0]})
    store = memory.get_store()
    mid = _remember("Flask blueprint routing facts")
    draft = {"title": "Web routing rules", "body": "Flask blueprint routing."}

    decision = topic_attach.maybe_propose_authoritative(
        store, draft, scope="global", summary_memory_id=mid,
        embedder=embedder)

    assert decision is not None and decision["kind"] == "merge"
    assert decision["topic_node_id"] == "web"
    # merge target already exists → the rule is linked to it now
    assert store.authoritative_topics_of(mid) == ["web"]
    # the proposal is in the review queue, awaiting a human
    run = _proposal_run(f"memory-reflect-{mid}")
    assert run is not None and run.provider == "memory-reflect"


def test_create_proposal_does_not_link(monkeypatch):
    _enable(monkeypatch)
    # draft matches no node marker → falls to the orthogonal default vector
    embedder = _MarkerEmbedder({"Flask": [1.0, 0.0, 0.0],
                                "sqlite": [0.0, 1.0, 0.0]})
    store = memory.get_store()
    mid = _remember("totally unrelated synthesis body")
    draft = {"title": "Unrelated rule", "body": "Nothing about the graph."}

    decision = topic_attach.maybe_propose_authoritative(
        store, draft, scope="global", summary_memory_id=mid,
        embedder=embedder)

    assert decision is not None and decision["kind"] == "create"
    # no node exists yet → nothing to link to
    assert store.authoritative_topics_of(mid) == []
    assert _proposal_run(f"memory-reflect-{mid}") is not None


def test_disabled_by_default_is_noop(monkeypatch):
    # flag stays off (default): no proposal, no link, returns None
    embedder = _MarkerEmbedder({"Flask": [1.0, 0.0, 0.0]})
    store = memory.get_store()
    mid = _remember("Flask blueprint routing facts")
    draft = {"title": "Web routing rules", "body": "Flask blueprint routing."}
    assert settings.agent_memory.reflect_proposes_authoritative_topics is False
    assert topic_attach.maybe_propose_authoritative(
        store, draft, scope="global", summary_memory_id=mid,
        embedder=embedder) is None
    assert store.authoritative_topics_of(mid) == []
