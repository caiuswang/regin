"""Orphan-bucket parity (MCP index_* ↔ WebUI), the `stats.orphaned` census,
and the `POST /api/memory/link-orphans` classify-and-file endpoint.

Regression guard for the fix that made untopiced memories visible in the
tree-nav walk (they hung under no subtree before) and surfaced the agentic
`link-topics` classifier as a WebUI action.
"""

from __future__ import annotations

import json

import lib.memory as memory
from lib.memory import mcp_server as mcp


class StubLLM:
    """One queued response per `complete()` call; None once drained — exactly
    what an unconfigured external agent yields (→ ClassifierUnavailable)."""

    def __init__(self, responses):
        self._responses = list(responses)

    def complete(self, prompt, *, max_tokens=1024):
        return self._responses.pop(0) if self._responses else None


def _orphan_lesson() -> str:
    """An active, unfiled lesson — the shape the bug was reported against."""
    return memory.remember("Prefer notify-on-finish over blocking on stdout.",
                           kind="lesson", title="Notify on finish",
                           scope="repo:regin", is_test=True)


def _valid_topic_id() -> str:
    from lib.settings import settings
    from lib.topics.graph_io import load_authoritative_graph
    graph = load_authoritative_graph(str(settings.project_root))
    return next(t for t, n in graph["topics"].items()
                if not n.get("meta") and n.get("kind") != "bucket")


# ── stats census ──────────────────────────────────────────────

def test_stats_orphaned_counts_unfiled_active():
    assert memory.stats()["orphaned"] == 0
    _orphan_lesson()
    store = memory.get_store()
    assert memory.stats()["orphaned"] == len(store.orphaned_memory_ids()) == 1


# ── MCP index_* parity with the WebUI __orphaned__ node ────────

def test_index_root_surfaces_orphan_bucket_only_when_nonempty():
    assert memory.ORPHAN_NODE_ID not in mcp.index_root("repo:regin")
    _orphan_lesson()
    assert memory.ORPHAN_NODE_ID in mcp.index_root("repo:regin")
    # A scope with zero orphans must not show the bucket.
    assert memory.ORPHAN_NODE_ID not in mcp.index_root("repo:no-such-xyz")


def test_index_fetch_lists_orphans_and_expand_is_leaf():
    mid = _orphan_lesson()
    fetched = mcp.index_fetch(memory.ORPHAN_NODE_ID, top_k=50,
                              scope="repo:regin", reinforce=False)
    assert mid in fetched
    assert "leaf" in mcp.index_expand(memory.ORPHAN_NODE_ID, "repo:regin")


# ── POST /api/memory/link-orphans ─────────────────────────────

def test_link_orphans_empty_returns_ok_without_resolving_agent(
        flask_client, monkeypatch):
    """No orphans → 200 with a zeroed envelope, and the classifier is never
    resolved (the guard clause returns before any agent work)."""
    def _boom():
        raise AssertionError("classifier resolved despite 0 orphans")
    monkeypatch.setattr("lib.memory.adapters.resolve_topic_classifier", _boom)
    r = flask_client.post("/api/memory/link-orphans",
                          json={"scope": "repo:no-such-xyz"})
    assert r.status_code == 200
    body = r.get_json()
    assert body["orphaned"] == 0 and body["linked"] == 0


def test_link_orphans_409_when_classifier_unavailable(flask_client, monkeypatch):
    """Fail-loud: an unconfigured agent yields 409, never a silent no-op or 500."""
    _orphan_lesson()
    monkeypatch.setattr("lib.memory.adapters.resolve_topic_classifier",
                        lambda: StubLLM([]))
    r = flask_client.post("/api/memory/link-orphans", json={})
    assert r.status_code == 409
    assert "error" in r.get_json()


def test_link_orphans_files_and_is_idempotent(flask_client, monkeypatch):
    mid = _orphan_lesson()
    topic = _valid_topic_id()
    resp = json.dumps([{"id": mid, "topics": [topic]}])
    monkeypatch.setattr("lib.memory.adapters.resolve_topic_classifier",
                        lambda: StubLLM([resp]))
    body = flask_client.post("/api/memory/link-orphans", json={}).get_json()
    assert body["linked"] == 1 and body["placed"] == 1

    store = memory.get_store()
    assert topic in store.authoritative_topics_of(mid)
    assert mid not in store.orphaned_memory_ids()

    # Re-run: the memory is filed, so it isn't even selected → no double-count.
    monkeypatch.setattr("lib.memory.adapters.resolve_topic_classifier",
                        lambda: StubLLM([resp]))
    again = flask_client.post("/api/memory/link-orphans", json={}).get_json()
    assert again["memories"] == 0 and again["linked"] == 0
