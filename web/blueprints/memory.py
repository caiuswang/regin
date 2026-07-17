"""Agent-memory endpoints (`/api/memory/*`).

Read/curate surface over `lib.memory` for the Memory view: list/inspect,
edit, approve proposals, retire/forget, run reflect, and a recall probe
for testing what a query would surface. All routes sit behind the global
auth gate (memory content is distilled session experience — nothing here
belongs on the public allowlist; the hook capture path writes through
`lib.memory` directly, not HTTP).
"""

from __future__ import annotations

from flask import Blueprint, jsonify, request

import lib.memory as memory
from lib.utils.pagination import clamp_page, clamp_size

memory_bp = Blueprint("memory", __name__)

# Synthetic taxonomy root for active memories with no authoritative-topic link.
# Shared with the MCP index_* walk (lib.memory.store) so the two surfaces can't
# drift on the node's id/label/blurb.
_ORPHAN_NODE_ID = memory.ORPHAN_NODE_ID
_ORPHAN_LABEL = memory.ORPHAN_LABEL
_ORPHAN_BLURB = memory.ORPHAN_BLURB


def _bool_arg(name: str, default: bool = False) -> bool:
    raw = request.args.get(name)
    if raw is None:
        return default
    return raw.lower() in ("1", "true", "yes")


@memory_bp.route("/api/memory")
def api_memory_list():
    """Memories, paginated (offset-limit). Filters: tier/status/kind/scope/q.
    `sort` orders the page: `recent` (default, newest-updated first),
    `recalled`, or `least_recalled`. Returns the standard `{items,
    pagination}` envelope plus a `stats` extra for the category bar."""
    page_idx = clamp_page(request.args.get("page"))
    size = clamp_size(request.args.get("size"), default=50)
    result = memory.get_store().list_memories_page(
        tier=request.args.get("tier") or None,
        status=request.args.get("status") or None,
        kind=request.args.get("kind") or None,
        scope=request.args.get("scope") or None,
        q=request.args.get("q") or None,
        include_tests=_bool_arg("include_tests"),
        sort=request.args.get("sort") or None,
        page=page_idx, size=size,
    )
    return jsonify({**result.to_envelope(), "stats": memory.stats()})


@memory_bp.route("/api/memory/stats")
def api_memory_stats():
    """Store census for the Doctor page — the same `stats` payload the list
    envelope carries, without paying for a page of rows."""
    return jsonify(memory.stats())


@memory_bp.route("/api/memory/<memory_id>/related")
def api_memory_related(memory_id):
    """Relationship view for the detail pane: embedding neighbors plus the
    supersede chain (rows this one retired, and the row that retired it)."""
    try:
        top_k = min(int(request.args.get("top_k", 5)), 20)
    except (TypeError, ValueError):
        top_k = 5
    data = memory.get_store().related(
        memory_id, top_k=top_k, include_tests=_bool_arg("include_tests"))
    return jsonify(data)


_BULK_ACTIONS = {"approve", "retire", "forget", "restore"}


@memory_bp.route("/api/memory/bulk", methods=["POST"])
def api_memory_bulk():
    """Apply one curate action to many memories. Body: {ids: [...],
    action: approve|retire|restore|forget}. Returns per-id success counts."""
    payload = request.get_json(silent=True) or {}
    action = payload.get("action")
    ids = payload.get("ids") or []
    if action not in _BULK_ACTIONS:
        return jsonify({"error": "unknown action"}), 400
    if not isinstance(ids, list) or not ids:
        return jsonify({"error": "ids required"}), 400
    done = [mid for mid in ids if _apply_bulk(action, mid)]
    return jsonify({"ok": True, "applied": len(done), "ids": done})


_BULK_VALIDATION_ACTION = {"approve": "approved", "retire": "retired"}


def _apply_bulk(action: str, memory_id: str) -> bool:
    if action == "forget":
        return memory.forget(memory_id)
    if action == "restore":
        return memory.restore(memory_id)
    status = "active" if action == "approve" else "retired"
    if not memory.update(memory_id, status=status):
        return False
    memory.get_store().record_validation(
        memory_id, validator="user", action=_BULK_VALIDATION_ACTION[action])
    return True


@memory_bp.route("/api/memory/recall", methods=["POST"])
def api_memory_recall():
    """Recall probe. Body: {query, top_k?, scope?, mode?, min_overlap?,
    boost_topic_node_id?}.

    Also the dense path for the auto-inject hook: a short-lived hook can't
    load the embedder, so it POSTs here to borrow this process's warm
    models. `min_overlap` is that path's precision gate (see
    `AgentMemoryConfig`); `boost_topic_node_id` (the authoritative topic the
    hook routed the prompt to) softly boosts memories linked to that node.
    The curate UI probe omits them (defaults 0 / None)."""
    payload = request.get_json(silent=True) or {}
    query = (payload.get("query") or "").strip()
    if not query:
        return jsonify({"error": "query required"}), 400
    hits = memory.recall(query, reinforce=False, **_recall_kwargs(payload))
    # Route-time topic suppression is computed here (not in the model-free
    # recall hook) because it needs the warm embedder: the hook passes the
    # topic it keyword-routed to and withholds the banner if we say so.
    route_topic_id = payload.get("route_topic_id") or None
    topic_suppress = bool(route_topic_id) and \
        memory.get_store().topic_route_suppressed(route_topic_id, query)
    return jsonify({"hits": [
        {**h.memory, "score": h.score, "score_kind": h.score_kind}
        for h in hits
    ], "topic_suppress": topic_suppress})


def _recall_kwargs(payload: dict) -> dict:
    """Map the recall-probe request body onto `memory.recall` keyword args,
    applying each field's default. Kept separate so the route stays a thin
    parse → call → serialize."""
    return {
        "top_k": min(int(payload.get("top_k") or 5), 20),
        "scope": payload.get("scope") or None,
        "mode": payload.get("mode") or "auto",
        "include_tests": bool(payload.get("include_tests")),
        "min_overlap": int(payload.get("min_overlap") or 0),
        "boost_topic_node_id": payload.get("boost_topic_node_id") or None,
    }


@memory_bp.route("/api/memory/reflect", methods=["POST"])
def api_memory_reflect():
    payload = request.get_json(silent=True) or {}
    result = memory.reflect(dry_run=bool(payload.get("dry_run")))
    return jsonify({
        "examined": result.examined, "merged": result.merged,
        "contradictions": result.contradictions,
        "obsoleted": result.obsoleted,
        "pairs_checked": result.pairs_checked,
        "dream_skipped": result.dream_skipped,
        "promoted": result.promoted, "held": result.held,
        "dropped": result.dropped,
        "embedded": result.embedded,
        "forgotten": result.forgotten, "decayed": result.decayed,
        "synthesized": result.synthesized,
        "edges": result.edges, "topics": result.topics,
        "flagged_stale": result.flagged_stale,
        "dry_run": result.dry_run, "actions": result.actions,
    })


@memory_bp.route("/api/memory/wiki-recalls")
def api_memory_wiki_recalls():
    """Per-topic wiki recall stats for the Wikis panel: `exposure` (index_fetch
    surfaced the path) + distinct-session `read` counts, `last_read`, label, and
    whether the wiki file still exists. Pass `?sync=1` to recompute the read
    signal from the trace first (the panel's manual refresh); it auto-refreshes
    at SessionEnd otherwise."""
    from pathlib import Path
    from lib.settings import settings
    from lib.memory.wiki_reads import sync_wiki_reads, wiki_recall_rows
    if _bool_arg("sync"):
        sync_wiki_reads()
    root = str(settings.project_root)
    return jsonify({"rows": wiki_recall_rows(root), "repo": Path(root).name})


@memory_bp.route("/api/memory/topics")
def api_memory_topics():
    """Named topic nodes (synthesis clusters), most-recent first, with their
    member counts — the grouping surface for the Memory view."""
    return jsonify({"topics": memory.get_store().list_topics()})


@memory_bp.route("/api/memory/topic-feedback")
def api_memory_topic_feedback():
    """The topic-routing feedback loop: per-topic relevance summary (verdicts
    from the `InjectedRelated` grade aspect + which routes are suppressed) and
    the most recent `<topic_context>` injections."""
    store = memory.get_store()
    try:
        limit = max(1, min(200, int(request.args.get("limit", 30))))
    except (TypeError, ValueError):
        limit = 30
    return jsonify({"summary": store.topic_relevance_summary(),
                    "recent": store.list_topic_injections(limit=limit),
                    "embedder": store.has_embedder})


@memory_bp.route("/api/memory/exemplars", methods=["POST"])
def api_memory_exemplar_add():
    """Hand-curate a topic-route query exemplar (a 'case'): record `query` as a
    positive (+1, protect) or negative (-1, suppress) exemplar for `topic_id`.
    `polarity` is 'positive'|'negative'. Source is stamped 'manual'. Returns
    rows written (0 when no embedder / blank query)."""
    payload = request.get_json(silent=True) or {}
    query = (payload.get("query") or "").strip()
    polarity = _parse_polarity(payload.get("polarity"))
    if not query:
        return jsonify({"error": "query required"}), 400
    if polarity is None:
        return jsonify({"error": "polarity must be positive | negative"}), 400
    store = memory.get_store()
    topic_id = payload.get("topic_id")
    sess = payload.get("session_id") or "manual"
    if not topic_id:
        return jsonify({"error": "topic_id required"}), 400
    written = store.add_topic_exemplars(
        sess, [(topic_id, query)], polarity, source="manual")
    return jsonify({"written": written})


@memory_bp.route("/api/memory/topic-route-preview", methods=["POST"])
def api_topic_route_preview():
    """Topic-route playground: for a probe `query`, what the route hook would
    inject and which topics' query-exemplars lean on it. Body: {query, repo?}.

    Returns `routed` (the keyword `match_topic` result + its suppress verdict,
    or null), `candidates` (every topic carrying exemplars, with the query's
    pos/neg max-cosine, counts, decision, and suppress verdict — ranked by the
    stronger signal), `topics` (all authoritative {id,label} for the manual
    picker), and the active `threshold`. The write side is the existing
    `POST /api/memory/exemplars` (topic_id + query + polarity)."""
    from lib.settings import settings
    payload = request.get_json(silent=True) or {}
    query = (payload.get("query") or "").strip()
    if not query:
        return jsonify({"error": "query required"}), 400
    repo = payload.get("repo") or str(settings.project_root)
    return jsonify(_route_preview(memory.get_store(), repo, query))


def _route_preview(store, repo: str, query: str) -> dict:
    """Assemble the topic-route-preview payload (see `api_topic_route_preview`):
    keyword route + per-topic exemplar signals over the authoritative graph."""
    from lib.settings import settings
    topics = _authoritative_topics(repo)
    labels = {t["id"]: t["label"] for t in topics}
    route = _keyword_route(repo, query)
    routed_id = route["id"]
    signals = store.topic_query_signals(query)
    if routed_id and routed_id not in signals:
        signals.update(store.topic_query_signals(query, [routed_id]))
    candidates = sorted(
        ({"id": tid, "label": labels.get(tid, tid), **sig}
         for tid, sig in signals.items()),
        key=lambda c: -max(c["pos_sim"], c["neg_sim"]))
    routed = None
    if routed_id:
        routed = {"id": routed_id, "label": labels.get(routed_id, routed_id),
                  "strategy": route["strategy"], "keywords": route["keywords"],
                  **signals.get(routed_id, {})}
    if routed_id and routed_id not in labels:
        topics = topics + [{"id": routed_id, "label": routed_id}]
    return {"query": query, "routed": routed, "candidates": candidates,
            "topics": topics,
            "threshold": settings.agent_memory.topic_negative_suppress_sim}


def _authoritative_topics(repo: str) -> list:
    """`[{id, label}]` for every approved topic in `repo`'s graph, sorted by
    label — the playground's manual topic picker. Best-effort: [] on any graph
    fault so the probe still works on its keyword route + exemplar signals."""
    try:
        from lib.topics.route import topic_summary
        rows = topic_summary(repo)["topics"]
    except Exception:
        return []
    return sorted(
        ({"id": t["id"], "label": t.get("label") or t["id"]} for t in rows),
        key=lambda t: t["label"].lower())


def _keyword_route(repo: str, query: str) -> dict:
    """What `query` keyword-routes to (what the recall hook injects) plus the
    basis — `{id, strategy, keywords}`. Best-effort — mirrors the hook's
    `_route_topic` swallow, returning an empty route on any graph fault."""
    try:
        from lib.topics.route import route_explain
        return route_explain(repo, query)
    except Exception:
        return {"id": None, "strategy": None, "keywords": []}


@memory_bp.route("/api/memory/exemplars", methods=["DELETE"])
def api_memory_exemplar_remove():
    """Drop a topic's exemplars — one polarity ('positive'|'negative') or all
    when omitted. The undo for a curated case."""
    payload = request.get_json(silent=True) or {}
    raw = payload.get("polarity")
    polarity = _parse_polarity(raw) if raw else None
    if raw and polarity is None:
        return jsonify({"error": "polarity must be positive | negative"}), 400
    store = memory.get_store()
    topic_id = payload.get("topic_id")
    if not topic_id:
        return jsonify({"error": "topic_id required"}), 400
    removed = store.remove_topic_exemplars(topic_id, polarity)
    return jsonify({"removed": removed})


def _parse_polarity(raw) -> "int | None":
    """Map 'positive'/'negative' (or ±1) onto the signed polarity, or None."""
    if raw in ("positive", "pos", "+1", 1):
        return 1
    if raw in ("negative", "neg", "-1", -1):
        return -1
    return None


@memory_bp.route("/api/memory/exemplars/<kind>/<key>",
                 methods=["GET", "DELETE"])
def api_memory_exemplar_cases(kind, key):
    """Per-exemplar inspection + single-operation revert. `kind` is 'topic'.

    GET  — `key` is the topic_id; returns `{exemplars: [...]}`, each row
           carrying its id, query text, polarity, source, origin session, and
           timestamp (the case list behind the drill-down).
    DELETE — `key` is the exemplar *id*; removes that one row (`{removed}`) —
             the undo for a single mislabel, finer-grained than the
             polarity-wide DELETE /api/memory/exemplars."""
    if kind != "topic":
        return jsonify({"error": "kind must be topic"}), 400
    store = memory.get_store()
    if request.method == "DELETE":
        try:
            removed = store.delete_exemplar(int(key), kind)
        except (TypeError, ValueError):
            return jsonify({"error": "exemplar id must be an integer"}), 400
        return jsonify({"removed": 1 if removed else 0})
    return jsonify({"exemplars": store.list_topic_exemplars(key)})


@memory_bp.route("/api/memory/topic-feedback/<topic_id>/decision",
                 methods=["POST"])
def api_memory_topic_decision(topic_id):
    """The human gate over topic suppression: set the standing routing
    decision for `topic_id`. `decision` is 'suppressed' (approve withholding),
    'allowed' (pin the route on / reject a proposal), or 'auto' (clear)."""
    payload = request.get_json(silent=True) or {}
    decision = payload.get("decision")
    if decision not in ("suppressed", "allowed", "auto"):
        return jsonify(
            {"error": "decision must be suppressed | allowed | auto"}), 400
    try:
        memory.get_store().set_topic_decision(
            topic_id, decision, note=payload.get("note"))
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    from lib.grader.topic_notify import resolve_proposal
    resolve_proposal(topic_id)  # clear any open inbox proposal for this topic
    return jsonify({"ok": True, "topic_id": topic_id, "decision": decision})


@memory_bp.route("/api/memory/topics/<topic_id>")
def api_memory_topic(topic_id):
    """One topic with its live members serialized."""
    data = memory.get_store().get_topic(
        topic_id, include_tests=_bool_arg("include_tests"))
    if data is None:
        return jsonify({"error": "not found"}), 404
    return jsonify(data)


@memory_bp.route("/api/memory/taxonomy")
def api_memory_taxonomy():
    """The authoritative topic taxonomy (`.regin/topics/topics/`) as a
    navigable tree — the WebUI mirror of the `index_root`/`index_expand` MCP
    walk. The graph is small (~30 nodes), so the whole tree ships in one
    payload and the frontend drills client-side: `{roots, nodes}` where each
    node carries its label, router blurb, child/ref counts, subtree memory
    count, child ids, and whether a curated wiki page exists. Optional
    `scope` (e.g. `repo:regin`) filters the memory counts."""
    from lib.settings import settings
    from lib.topics.graph_io import load_authoritative_graph
    from lib.topics.meta_roots import merge_meta_roots
    from lib.topics.tree import build_tree, node_card, subtree_ids
    from lib.topics.wiki import wiki_dir
    scope = request.args.get("scope") or None
    # The repo dropdown selects which repo's OWN taxonomy to load, not just a
    # count filter over the serving repo's tree.
    root = _scope_repo_root(scope)
    graph = merge_meta_roots(load_authoritative_graph(root))
    if not graph or not graph.get("topics"):
        return jsonify({"roots": [], "nodes": {}})
    store = memory.get_store()
    tree = build_tree(graph)
    children = tree["children"]
    wdir = wiki_dir(root)
    nodes: dict = {}
    # Every declared topic, plus any root the tree surfaces without a node
    # (the reserved `unclassified` bucket can hold quarantined leaves even
    # when it was never declared).
    for nid in list(graph["topics"]) + tree["roots"]:
        if nid in nodes:
            continue
        card = node_card(graph, nid) or {
            "id": nid, "label": nid, "blurb": "",
            "ref_count": 0, "child_count": len(children.get(nid, []))}
        card["children"] = children.get(nid, [])
        # parent_id + cross-topic edges let the WebUI graph view render the
        # full relation set (tree links + related-topic edges) from this one
        # payload, instead of a detail fetch per node.
        topic = graph["topics"].get(nid) or {}
        card["parent_id"] = topic.get("parent_id")
        card["edges"] = topic.get("edges") or []
        # Global meta-root nodes (the skills/preferences overlay) hold
        # cross-repo memories, so a repo `scope` must NOT filter their counts —
        # they stay visible (with their global count) under a repo filter,
        # while repo nodes narrow to the scope.
        card["meta"] = bool(topic.get("meta"))
        card["mem_count"] = _taxonomy_mem_count(
            store, graph, nid, is_meta=card["meta"], scope=scope)
        card["has_wiki"] = (wdir / f"{nid}.md").exists()
        nodes[nid] = card
    roots = list(tree["roots"])
    orphans = store.orphaned_memory_ids(scope=scope)
    if orphans:
        nodes[_ORPHAN_NODE_ID] = _orphan_card(len(orphans))
        roots.append(_ORPHAN_NODE_ID)
    return jsonify({"roots": roots, "nodes": nodes})


def _scope_repo_root(scope):
    """Filesystem root of the repo whose OWN taxonomy the Tree view loads for
    `scope`. A `repo:<name>` scope matching a registered repo (basename of a
    `settings.repo_paths` entry) switches the tree to that repo's graph; All
    repositories / global / an unregistered scope fall back to the serving
    project root."""
    import os
    from lib.settings import settings
    if scope and scope.startswith("repo:"):
        name = scope.split(":", 1)[1]
        for rp in settings.repo_paths:
            root = os.path.realpath(str(rp))
            if os.path.basename(root) == name:
                return root
    return str(settings.project_root)


def _taxonomy_mem_count(store, graph, nid, *, is_meta, scope):
    """Subtree memory count for a taxonomy node. Meta-root (global) nodes
    ignore a repo `scope` so they stay visible under a repo filter; repo nodes
    narrow to the scope."""
    from lib.topics.tree import subtree_ids
    return len(store.memories_for_topic_subtree(
        subtree_ids(graph, nid), scope=None if is_meta else scope))


def _orphan_card(count: int) -> dict:
    """The synthetic taxonomy node listing un-filed active memories. Carries
    no children/edges/refs and no wiki — it is a working bucket, not a topic."""
    return {"id": _ORPHAN_NODE_ID, "label": _ORPHAN_LABEL,
            "blurb": _ORPHAN_BLURB, "children": [], "parent_id": None,
            "edges": [], "ref_count": 0, "has_wiki": False,
            "mem_count": count}


def _taxonomy_top_k() -> int:
    try:
        return max(1, min(int(request.args.get("top_k", 30)), 100))
    except (TypeError, ValueError):
        return 30


def _mem_headline(m: dict) -> dict:
    """Importance-ranked memory address for the taxonomy detail pane — a
    headline the user clicks to open the full memory, not a body dump.

    `snippet` is a short body-derived fallback so an untitled memory still
    renders readable text in the tree (the bare `kind` word otherwise);
    capped, not the full body."""
    from lib.memory.store import title_from_body
    return {"id": m["id"], "kind": m["kind"], "title": m.get("title"),
            "snippet": title_from_body(m.get("body") or "", max_len=100),
            "importance": m.get("importance"), "scope": m.get("scope"),
            "veracity": m.get("veracity")}


def _taxonomy_detail(node_id: str, node: dict, ids: list,
                     mems: list, body) -> dict:
    """Assemble the taxonomy node detail payload (see
    `api_memory_taxonomy_node`) — pure shaping, no I/O."""
    from lib.topics.tree import blurb_of
    return {
        "id": node_id,
        "label": node.get("label") or node_id,
        "blurb": blurb_of(node),
        "refs": node.get("refs") or [],
        "edges": node.get("edges") or [],
        "wiki": {"path": f".regin/topics/wiki/{node_id}.md",
                 "exists": body is not None, "body": body},
        "memory_total": len(ids),
        "memories": [_mem_headline(m) for m in mems if m],
    }


@memory_bp.route("/api/memory/taxonomy/<node_id>")
def api_memory_taxonomy_node(node_id):
    """One taxonomy node, expanded for reading — the WebUI mirror of
    `index_fetch`, but it returns *contents* (this is a human surface): the
    node blurb, its source-file refs + related edges, the rendered wiki
    narrative (`body`, or null when the node has no curated page), and its
    subtree memories as importance-ranked headlines. `top_k` caps the memory
    list (default 30); `scope` filters it."""
    from lib.settings import settings
    from lib.topics.graph_io import load_authoritative_graph
    from lib.topics.meta_roots import merge_meta_roots
    from lib.topics.tree import subtree_ids
    from lib.topics.wiki import wiki_dir
    store = memory.get_store()
    scope = request.args.get("scope") or None
    if node_id == _ORPHAN_NODE_ID:
        ids = store.orphaned_memory_ids(scope=scope)
        mems = [store.get_dict(mid) for mid in ids[:_taxonomy_top_k()]]
        synthetic = {"label": _ORPHAN_LABEL, "blurb": _ORPHAN_BLURB,
                     "refs": [], "edges": []}
        return jsonify(
            _taxonomy_detail(node_id, synthetic, ids, mems, None))
    root = _scope_repo_root(scope)
    graph = merge_meta_roots(load_authoritative_graph(root) or {})
    node = graph.get("topics", {}).get(node_id)
    if node is None:
        return jsonify({"error": "not found"}), 404
    # Meta-root nodes are global; a repo scope must not filter their memories.
    node_scope = None if node.get("meta") else scope
    ids = store.memories_for_topic_subtree(
        subtree_ids(graph, node_id), scope=node_scope)
    mems = [store.get_dict(mid) for mid in ids[:_taxonomy_top_k()]]
    wiki_path = wiki_dir(root) / f"{node_id}.md"
    body = wiki_path.read_text() if wiki_path.exists() else None
    return jsonify(_taxonomy_detail(node_id, node, ids, mems, body))


@memory_bp.route("/api/memory/topic-nodes")
def api_memory_topic_nodes():
    """Authoritative topic nodes as `{id, label}` — the picker/label source
    for the manual file-a-memory affordances (taxonomy detail + MemoryDetail).
    Sorted by label; [] on any graph fault (mirrors `_authoritative_topics`)."""
    from lib.settings import settings
    return jsonify({"topics": _authoritative_topics(str(settings.project_root))})


@memory_bp.route("/api/memory/<memory_id>/topics", methods=["POST"])
def api_memory_topic_link(memory_id):
    """File a memory under an authoritative topic node. Body: {node_id}.
    Validates the memory and node both exist; idempotent (a repeat link is a
    no-op that still returns ok). Returns the memory's full link set."""
    from lib.settings import settings
    from lib.topics.graph_io import load_authoritative_graph
    store = memory.get_store()
    if store.get_dict(memory_id) is None:
        return jsonify({"error": "not found"}), 404
    node_id = (request.get_json(silent=True) or {}).get("node_id")
    graph = load_authoritative_graph(str(settings.project_root)) or {}
    if not node_id or node_id not in graph.get("topics", {}):
        return jsonify({"error": "unknown topic node"}), 404
    store.link_authoritative_topic(memory_id, node_id, source="manual")
    return jsonify({"ok": True,
                    "authoritative_topics": store.authoritative_topics_of(
                        memory_id)})


@memory_bp.route("/api/memory/<memory_id>/topics/<node_id>",
                 methods=["DELETE"])
def api_memory_topic_unlink(memory_id, node_id):
    """Unfile a memory from an authoritative topic node. Idempotent —
    `removed` is False when there was no link. Returns the remaining set."""
    store = memory.get_store()
    removed = store.unlink_authoritative_topic(memory_id, node_id)
    return jsonify({"ok": True, "removed": removed,
                    "authoritative_topics": store.authoritative_topics_of(
                        memory_id)})


@memory_bp.route("/api/memory/link-orphans", methods=["POST"])
def api_memory_link_orphans():
    """Agentically file the unfiled memories under authoritative topic nodes —
    the WebUI trigger for `regin memory link-topics --orphans-only`. Body:
    {scope?}. Runs the editable `memory-topic-classify` surface over the active
    memories with no topic link (narrowed to `scope` when given), links what the
    agent returns (source='agent'), and returns the run counts plus the residual
    orphan count so the caller can refresh. Synchronous, mirroring POST
    /api/memory/reflect. Fail-loud: 409 when no external classifier agent is
    configured — never a silent no-op. The graph classified against is `scope`'s
    own repo taxonomy (meta-roots merged), so a repo-scoped run is exact."""
    from lib.topics.graph_io import load_authoritative_graph
    from lib.topics.meta_roots import merge_meta_roots
    from lib.memory.adapters import resolve_topic_classifier
    from lib.memory.topic_classify import (classify_and_link,
                                           ClassifierUnavailable)
    scope = (request.get_json(silent=True) or {}).get("scope") or None
    store = memory.get_store()
    rows = [m for m in (store.get_dict(mid)
                        for mid in store.orphaned_memory_ids(scope=scope)) if m]
    if not rows:
        return jsonify({"ok": True, "memories": 0, "placed": 0, "linked": 0,
                        "orphaned": 0})
    graph = merge_meta_roots(load_authoritative_graph(_scope_repo_root(scope)))
    stats: dict = {}
    try:
        result = classify_and_link(
            store, rows, graph, resolve_topic_classifier(), stats=stats)
    except ClassifierUnavailable as exc:
        return jsonify({"error": str(exc)}), 409
    return jsonify({
        "ok": True,
        "memories": stats.get("memories", len(rows)),
        "placed": stats.get("placed", 0),
        "linked": result["linked"],
        "batches": stats.get("batches", 0),
        "unparsed": stats.get("unparsed", 0),
        "orphaned": len(store.orphaned_memory_ids(scope=scope)),
    })


@memory_bp.route("/api/memory/graph")
def api_memory_graph():
    """The `related` edge graph for visualization: `{edges: [{src, dst,
    weight}]}`. Nodes are resolved client-side from the memory list."""
    return jsonify({"edges": memory.get_store().list_edges()})


@memory_bp.route("/api/memory/<memory_id>")
def api_memory_get(memory_id):
    row = memory.get_store().get_dict(memory_id)
    if row is None:
        return jsonify({"error": "not found"}), 404
    return jsonify({"memory": row})


_EDITABLE = {"title", "body", "kind", "scope", "tags", "importance",
             "veracity", "status", "valid_until"}


@memory_bp.route("/api/memory/<memory_id>", methods=["PATCH"])
def api_memory_update(memory_id):
    payload = request.get_json(silent=True) or {}
    fields = {k: v for k, v in payload.items() if k in _EDITABLE}
    if not fields:
        return jsonify({"error": "no editable fields supplied"}), 400
    try:
        updated = memory.update(memory_id, **fields)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    if not updated:
        return jsonify({"error": "not found"}), 404
    return jsonify({"ok": True})


@memory_bp.route("/api/memory/<memory_id>/approve", methods=["POST"])
def api_memory_approve(memory_id):
    """Promote a distill proposal to active."""
    if not memory.update(memory_id, status="active"):
        return jsonify({"error": "not found"}), 404
    memory.get_store().record_validation(
        memory_id, validator="user", action="approved")
    return jsonify({"ok": True})


@memory_bp.route("/api/memory/<memory_id>/retire", methods=["POST"])
def api_memory_retire(memory_id):
    payload = request.get_json(silent=True) or {}
    fields = {"status": "retired"}
    if payload.get("wrong"):
        fields["veracity"] = "false"
    if not memory.update(memory_id, **fields):
        return jsonify({"error": "not found"}), 404
    memory.get_store().record_validation(
        memory_id, validator="user", action="retired",
        note="marked wrong" if payload.get("wrong") else None)
    return jsonify({"ok": True})


@memory_bp.route("/api/memory/<memory_id>/restore", methods=["POST"])
def api_memory_restore(memory_id):
    """Bring a retired memory back to active (reactivate + clear the
    supersede link so recall surfaces it again)."""
    if not memory.restore(memory_id):
        return jsonify({"error": "not found"}), 404
    return jsonify({"ok": True})


@memory_bp.route("/api/memory/<memory_id>", methods=["DELETE"])
def api_memory_forget(memory_id):
    if not memory.forget(memory_id):
        return jsonify({"error": "not found"}), 404
    return jsonify({"ok": True})
