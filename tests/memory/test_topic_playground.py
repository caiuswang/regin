"""The topic-route playground: `topic_query_signals` + the
`/api/memory/topic-route-preview` endpoint. Given a probe query, surface each
topic's query-exemplar lean (pos/neg max-cosine, counts, suppress verdict) so a
human can stamp 👍/👎 cases — the read half of the manual topic-exemplar loop
whose write half is the existing `POST /api/memory/exemplars`.
"""

from __future__ import annotations

import lib.memory as memory
from lib.memory.store import SqliteMemoryStore
from lib.settings import settings
from tests.memory.test_store import _StubEmbedder


# Two orthogonal unit vectors so cosines are exactly 1.0 / 0.0: a query marked
# "alpha" lands on the negative exemplar, "beta" on the positive one.
_VECTORS = {"alpha": [1.0, 0.0, 0.0], "beta": [0.0, 1.0, 0.0]}


def _store() -> SqliteMemoryStore:
    return SqliteMemoryStore(embedder=_StubEmbedder(_VECTORS))


def _seed_topic(store: SqliteMemoryStore, topic_id: str = "topicX") -> None:
    """One negative ('alpha') and one positive ('beta') exemplar for one topic."""
    store.add_topic_negatives("s-neg", [(topic_id, "alpha hard ignore case")])
    store.add_topic_positives("s-pos", [(topic_id, "beta engaged case")])


# ── store: topic_query_signals ────────────────────────────────

def test_signals_report_pos_neg_sims_and_counts():
    store = _store()
    _seed_topic(store)
    sig = store.topic_query_signals("alpha probe")["topicX"]
    assert sig["neg_sim"] == 1.0 and sig["pos_sim"] == 0.0
    assert sig["pos_count"] == 1 and sig["neg_count"] == 1


def test_negative_lean_suppresses_over_threshold(monkeypatch):
    monkeypatch.setattr(settings.agent_memory,
                        "topic_negative_suppress_sim", 0.5)
    store = _store()
    _seed_topic(store)
    # query resembles the negative → withheld
    assert store.topic_query_signals("alpha probe")["topicX"]["suppressed"]
    # query resembles the positive → protected (a closer positive wins)
    assert not store.topic_query_signals("beta probe")["topicX"]["suppressed"]


def test_threshold_zero_never_suppresses(monkeypatch):
    monkeypatch.setattr(settings.agent_memory,
                        "topic_negative_suppress_sim", 0.0)
    store = _store()
    _seed_topic(store)
    assert not store.topic_query_signals("alpha probe")["topicX"]["suppressed"]


def test_allowed_pin_protects_despite_negative(monkeypatch):
    monkeypatch.setattr(settings.agent_memory,
                        "topic_negative_suppress_sim", 0.5)
    store = _store()
    _seed_topic(store)
    store.set_topic_decision("topicX", "allowed")
    sig = store.topic_query_signals("alpha probe")["topicX"]
    assert sig["decision"] == "allowed" and not sig["suppressed"]


def test_signals_empty_without_embedder_or_query():
    store = SqliteMemoryStore(embedder=None)
    assert store.topic_query_signals("anything") == {}
    assert _store().topic_query_signals("   ") == {}


# ── endpoint: /api/memory/topic-route-preview ─────────────────

def test_preview_requires_query(flask_client):
    assert flask_client.post("/api/memory/topic-route-preview",
                             json={}).status_code == 400


def test_preview_shape_and_candidate(flask_client):
    store = memory.get_store()
    store._embedder = _StubEmbedder(_VECTORS)
    _seed_topic(store)
    data = flask_client.post("/api/memory/topic-route-preview",
                             json={"query": "alpha probe"}).get_json()
    assert set(data) >= {"query", "routed", "candidates", "topics", "threshold"}
    cand = next(c for c in data["candidates"] if c["id"] == "topicX")
    assert cand["neg_sim"] == 1.0 and cand["neg_count"] == 1


# ── store: view + single-operation revert ─────────────────────

def test_exemplars_persist_the_query_text():
    store = _store()
    _seed_topic(store)
    rows = store.list_topic_exemplars("topicX")
    assert {r["query"] for r in rows} == {
        "alpha hard ignore case", "beta engaged case"}
    assert {r["polarity"] for r in rows} == {1, -1}
    assert all(r["source"] == "auto" and r["id"] for r in rows)


def test_delete_exemplar_by_id_reverts_one_case():
    store = _store()
    _seed_topic(store)
    rows = store.list_topic_exemplars("topicX")
    victim = next(r for r in rows if r["polarity"] == 1)
    assert store.delete_exemplar(victim["id"], "topic") is True
    remaining = store.list_topic_exemplars("topicX")
    assert [r["id"] for r in remaining] == [
        r["id"] for r in rows if r["id"] != victim["id"]]
    # gone, and idempotent on a second try
    assert store.delete_exemplar(victim["id"], "topic") is False


def test_delete_exemplar_rejects_unknown_kind():
    store = _store()
    _seed_topic(store)
    vid = store.list_topic_exemplars("topicX")[0]["id"]
    assert store.delete_exemplar(vid, "bogus") is False
    assert len(store.list_topic_exemplars("topicX")) == 2  # untouched


# ── endpoint: list + delete-by-id ─────────────────────────────

def test_endpoint_lists_and_reverts_a_case(flask_client):
    store = memory.get_store()
    store._embedder = _StubEmbedder(_VECTORS)
    _seed_topic(store)
    listed = flask_client.get(
        "/api/memory/exemplars/topic/topicX").get_json()["exemplars"]
    assert len(listed) == 2 and all(e["query"] for e in listed)

    vid = listed[0]["id"]
    r = flask_client.delete(f"/api/memory/exemplars/topic/{vid}")
    assert r.status_code == 200 and r.get_json()["removed"] == 1
    after = flask_client.get(
        "/api/memory/exemplars/topic/topicX").get_json()["exemplars"]
    assert vid not in [e["id"] for e in after]


def test_endpoint_rejects_bad_kind_and_id(flask_client):
    assert flask_client.get(
        "/api/memory/exemplars/bogus/x").status_code == 400
    assert flask_client.delete(
        "/api/memory/exemplars/topic/not-an-int").status_code == 400
