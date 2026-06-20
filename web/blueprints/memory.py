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


def _bool_arg(name: str, default: bool = False) -> bool:
    raw = request.args.get(name)
    if raw is None:
        return default
    return raw.lower() in ("1", "true", "yes")


@memory_bp.route("/api/memory")
def api_memory_list():
    """Memories, newest-updated first, paginated (offset-limit). Filters:
    tier/status/kind/scope/q. Returns the standard `{items, pagination}`
    envelope plus a `stats` extra for the category bar."""
    page_idx = clamp_page(request.args.get("page"))
    size = clamp_size(request.args.get("size"), default=50)
    result = memory.get_store().list_memories_page(
        tier=request.args.get("tier") or None,
        status=request.args.get("status") or None,
        kind=request.args.get("kind") or None,
        scope=request.args.get("scope") or None,
        q=request.args.get("q") or None,
        include_tests=_bool_arg("include_tests"),
        page=page_idx, size=size,
    )
    return jsonify({**result.to_envelope(), "stats": memory.stats()})


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


_BULK_ACTIONS = {"approve", "retire", "forget"}


@memory_bp.route("/api/memory/bulk", methods=["POST"])
def api_memory_bulk():
    """Apply one curate action to many memories. Body: {ids: [...],
    action: approve|retire|forget}. Returns per-id success counts."""
    payload = request.get_json(silent=True) or {}
    action = payload.get("action")
    ids = payload.get("ids") or []
    if action not in _BULK_ACTIONS:
        return jsonify({"error": "unknown action"}), 400
    if not isinstance(ids, list) or not ids:
        return jsonify({"error": "ids required"}), 400
    done = [mid for mid in ids if _apply_bulk(action, mid)]
    return jsonify({"ok": True, "applied": len(done), "ids": done})


def _apply_bulk(action: str, memory_id: str) -> bool:
    if action == "forget":
        return memory.forget(memory_id)
    status = "active" if action == "approve" else "retired"
    if not memory.update(memory_id, status=status):
        return False
    memory.get_store().record_validation(
        memory_id, validator="user",
        action="approved" if action == "approve" else "retired")
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
        "promoted": result.promoted, "embedded": result.embedded,
        "forgotten": result.forgotten, "decayed": result.decayed,
        "synthesized": result.synthesized,
        "edges": result.edges, "topics": result.topics,
        "flagged_stale": result.flagged_stale,
        "dry_run": result.dry_run, "actions": result.actions,
    })


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


@memory_bp.route("/api/memory/exemplars")
def api_memory_exemplars():
    """Exemplar inspection: the configured demotion/boost weights plus a
    per-memory count of positive and negative query exemplars (the contextual
    recall re-ranking loop). Empty `summary` with both weights 0 means the
    feature is off."""
    from lib.settings import settings
    store = memory.get_store()
    try:
        limit = max(1, min(200, int(request.args.get("limit", 50))))
    except (TypeError, ValueError):
        limit = 50
    return jsonify({
        "neg_weight": settings.agent_memory.negative_demotion_weight,
        "pos_weight": settings.agent_memory.positive_boost_weight,
        "summary": store.exemplar_summary(limit=limit)})


@memory_bp.route("/api/memory/exemplars", methods=["POST"])
def api_memory_exemplar_add():
    """Hand-curate a query exemplar (a 'case'): record `query` as a positive
    (+1, boost) or negative (-1, demote) exemplar for `memory_id` *or*
    `topic_id`. `polarity` is 'positive'|'negative'. Source is stamped
    'manual'. Returns rows written (0 when no embedder / blank query)."""
    payload = request.get_json(silent=True) or {}
    query = (payload.get("query") or "").strip()
    polarity = _parse_polarity(payload.get("polarity"))
    if not query:
        return jsonify({"error": "query required"}), 400
    if polarity is None:
        return jsonify({"error": "polarity must be positive | negative"}), 400
    store = memory.get_store()
    memory_id = payload.get("memory_id")
    topic_id = payload.get("topic_id")
    sess = payload.get("session_id") or "manual"
    if memory_id:
        written = store.add_query_exemplars(
            sess, [(memory_id, query)], polarity, source="manual")
    elif topic_id:
        written = store.add_topic_exemplars(
            sess, [(topic_id, query)], polarity, source="manual")
    else:
        return jsonify({"error": "memory_id or topic_id required"}), 400
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
    """Drop a memory's or topic's exemplars — one polarity
    ('positive'|'negative') or all when omitted. The undo for a curated case."""
    payload = request.get_json(silent=True) or {}
    raw = payload.get("polarity")
    polarity = _parse_polarity(raw) if raw else None
    if raw and polarity is None:
        return jsonify({"error": "polarity must be positive | negative"}), 400
    store = memory.get_store()
    memory_id = payload.get("memory_id")
    topic_id = payload.get("topic_id")
    if memory_id:
        removed = store.remove_exemplars(memory_id, polarity)
    elif topic_id:
        removed = store.remove_topic_exemplars(topic_id, polarity)
    else:
        return jsonify({"error": "memory_id or topic_id required"}), 400
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
    """Per-exemplar inspection + single-operation revert. `kind` is
    'topic' | 'memory'.

    GET  — `key` is the topic_id / memory_id; returns `{exemplars: [...]}`,
           each row carrying its id, query text, polarity, source, origin
           session, and timestamp (the case list behind the drill-down).
    DELETE — `key` is the exemplar *id*; removes that one row (`{removed}`) —
             the undo for a single mislabel, finer-grained than the
             polarity-wide DELETE /api/memory/exemplars."""
    if kind not in ("topic", "memory"):
        return jsonify({"error": "kind must be topic | memory"}), 400
    store = memory.get_store()
    if request.method == "DELETE":
        try:
            removed = store.delete_exemplar(int(key), kind)
        except (TypeError, ValueError):
            return jsonify({"error": "exemplar id must be an integer"}), 400
        return jsonify({"removed": 1 if removed else 0})
    lister = (store.list_topic_exemplars if kind == "topic"
              else store.list_memory_exemplars)
    return jsonify({"exemplars": lister(key)})


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
    if not memory.update(memory_id, **fields):
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


@memory_bp.route("/api/memory/<memory_id>", methods=["DELETE"])
def api_memory_forget(memory_id):
    if not memory.forget(memory_id):
        return jsonify({"error": "not found"}), 404
    return jsonify({"ok": True})
