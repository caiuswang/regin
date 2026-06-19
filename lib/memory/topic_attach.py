"""Attach a reflection synthesis cluster to the authoritative topic graph.

`reflect()` abstracts a higher-order rule from a cluster of related memories.
Instead of minting an orphan `memory_topic` (disconnected from the
human-approved graph in `.regin/topics/topic.json`), this module proposes that
the rule either **merge** onto an existing authoritative node — when the
cluster is clearly about that node — or seed a **create** candidate. Both land
in the same topic-proposal review queue external-agent proposals use, so a
human still gates every change to the approved graph.

Heuristic-first (the configured strategy): the merge-vs-create decision is
embedding cosine between the synthesised rule and each node's identity text
(label + intent + ref paths). No LLM required. A merge target already exists
in the graph, so the synthesised memory is linked to it immediately (Step-1
linkage); a create target doesn't exist yet, so its link waits for acceptance.

Every public entry point is failure-tolerant — a reflection pass must never
break because the topic graph or proposal store is unavailable.
"""

from __future__ import annotations

import os
from typing import Any, Optional

from lib.activity_log import get_activity_logger
from lib.settings import settings

log = get_activity_logger("memory")

ATTACH_PROVIDER = "memory-reflect"


def _node_identity_text(node: dict[str, Any]) -> str:
    """The text a node is matched on: label + intent + ref file paths — the
    same signal `_node_identity_text`'s authoritative counterpart routes on."""
    parts = [node.get("label") or "", node.get("intent") or ""]
    parts += [r.get("path", "") for r in node.get("refs", [])
              if isinstance(r, dict)]
    return " ".join(p for p in parts if p)


def map_cluster_to_topic(summary_vec, node_vecs,
                         *, threshold: float) -> dict[str, Any]:
    """Decide where a synthesised rule attaches. `node_vecs` is a list of
    `(node_id, vector)`; vectors are unit-length (the embedder normalises),
    so cosine is the dot product. Returns `{kind, topic_node_id, cosine}`:
    `merge` onto the best node at/above `threshold`, else `create`."""
    best_id: Optional[str] = None
    best = -1.0
    for node_id, vec in node_vecs:
        dot = sum(a * b for a, b in zip(summary_vec, vec))
        if dot > best:
            best, best_id = dot, node_id
    if best_id is not None and best >= threshold:
        return {"kind": "merge", "topic_node_id": best_id, "cosine": best}
    return {"kind": "create", "topic_node_id": None, "cosine": best}


def _repo_path_for_scope(scope: str) -> str:
    """Resolve a memory scope string to the repo whose topic graph (and
    proposal queue) the synthesis belongs to. `repo:<name>` → the matching
    registered repo; `global` (or unknown) → the project root."""
    if scope and scope.startswith("repo:"):
        name = scope[len("repo:"):]
        for repo_path in settings.repo_paths:
            if os.path.basename(os.path.realpath(str(repo_path))) == name:
                return str(repo_path)
    return str(settings.project_root)


def _slug(title: str) -> str:
    """A topic-id slug from a draft title (lowercase, hyphenated)."""
    out = "".join(c if c.isalnum() else "-" for c in title.lower())
    return "-".join(p for p in out.split("-") if p)[:60] or "synthesised-topic"


def _decide(draft: dict, embedder, graph: dict, threshold: float):
    """Embed the draft + graph nodes in one batch and map. Returns the
    decision dict, or None when there's nothing to match against."""
    nodes = [(nid, _node_identity_text(node))
             for nid, node in graph.get("topics", {}).items()]
    nodes = [(nid, text) for nid, text in nodes if text]
    if not nodes:
        return None
    summary_text = f"{draft['title']} {draft['body']}"
    vecs = embedder.embed([summary_text] + [t for _, t in nodes])
    if not vecs or len(vecs) != len(nodes) + 1:
        return None
    node_vecs = list(zip([nid for nid, _ in nodes], vecs[1:]))
    return map_cluster_to_topic(vecs[0], node_vecs, threshold=threshold)


def _proposed_topic(draft: dict, decision: dict,
                    graph: dict) -> dict[str, Any]:
    """Build the proposed-topic dict for `orm_save_proposal`. A merge keeps
    the target node's id and refs (the proposal folds the rule's summary into
    it); a create seeds a fresh candidate slug."""
    intent = draft["body"][:280]
    if decision["kind"] == "merge":
        node_id = decision["topic_node_id"]
        existing = graph.get("topics", {}).get(node_id, {})
        return {
            "id": node_id,
            "label": existing.get("label") or draft["title"],
            "intent": intent,
            "status": "active",
            "source": ATTACH_PROVIDER,
            "refs": existing.get("refs") or [],
        }
    return {
        "id": _slug(draft["title"]),
        "label": draft["title"],
        "intent": intent,
        "status": "draft",
        "source": ATTACH_PROVIDER,
        "refs": [],
    }


def _emit_proposal(repo_path: str, draft: dict, decision: dict,
                   graph: dict, *, source_memory_id: str) -> Optional[str]:
    """Persist a single-topic, human-gated proposal. Returns its id."""
    from lib.topics.proposal_orm.runs import orm_save_proposal

    proposal_id = f"{ATTACH_PROVIDER}-{source_memory_id}"
    proposal = {
        "provider": ATTACH_PROVIDER,
        "scope": "all",
        "status": "pending_review",
        "topics": [_proposed_topic(draft, decision, graph)],
        "metadata": {
            "source_memory_id": source_memory_id,
            "attach_kind": decision["kind"],
            "attach_cosine": round(float(decision["cosine"]), 4),
        },
    }
    orm_save_proposal(repo_path, proposal_id, proposal,
                      wiki=draft["body"])
    return proposal_id


def maybe_propose_authoritative(store, draft: dict, *, scope: str,
                                summary_memory_id: str,
                                embedder) -> Optional[dict]:
    """Reflection entry point. Embed the synthesised rule, map it onto the
    repo's authoritative graph, and emit a merge/create proposal into the
    review queue. On a `merge` (target node already exists) link the rule to
    that node now (`source='reflect'`). Returns the decision dict, or None
    when it couldn't run. Never raises — synthesis must not break on a topic
    or proposal-store failure."""
    cfg = settings.agent_memory
    if (not cfg.reflect_proposes_authoritative_topics
            or embedder is None or getattr(embedder, "model_id", None) is None):
        return None
    try:
        from lib.topics.route import load_authoritative_graph

        repo_path = _repo_path_for_scope(scope)
        graph = load_authoritative_graph(repo_path)
        decision = _decide(draft, embedder, graph,
                           cfg.reflect_topic_attach_cosine)
        if decision is None:
            return None
        if decision["kind"] == "merge":
            store.link_authoritative_topic(
                summary_memory_id, decision["topic_node_id"], source="reflect")
        proposal_id = _emit_proposal(
            repo_path, draft, decision, graph,
            source_memory_id=summary_memory_id)
        log.write("memory_topic_proposed", kind=decision["kind"],
                  topic_node_id=decision.get("topic_node_id"),
                  proposal_id=proposal_id, memory_id=summary_memory_id)
        return decision
    except Exception:
        log.error("memory_topic_propose_failed", exc_info=True)
        return None
