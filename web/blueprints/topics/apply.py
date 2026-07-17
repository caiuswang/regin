"""Phase B endpoints: /diff, /apply, /audit, /snapshots.

These sit ALONGSIDE the legacy /accept, /merge, /replace, /ignore
endpoints (in `proposals.py`); the old ones stay functional through
Phase D. Phase C's frontend rewrite migrates the UI to call /diff +
/apply exclusively; the old ones get removed in Phase E.

/diff and /apply are thin HTTP adapters over
`lib.topics.proposals.apply_service`, which owns the domain logic
(including the recompute-the-diff-server-side contract) so non-Flask
callers can reuse it.
"""

from __future__ import annotations

import json
from typing import Any

from flask import jsonify, request
from sqlmodel import select

from lib.orm import SessionLocal
from lib.orm.models import GraphSnapshot, Repo
from lib.topics import TopicGraphError
from lib.topics.graph_io import load_authoritative_graph
from lib.topics.apply import apply_diff
from lib.topics.bulk_fix import AUTO_FIXABLE_CODES, compose_fix
from lib.topics.diff import (
    GraphDiff,
    compute_topic_delta,
    serialize_issue,
)
from lib.topics.proposals.apply_service import (
    apply_proposal_topic,
    diff_proposal_topic,
)
from lib.topics.snapshots import (
    list_snapshots,
    resolve_or_create_repo,
    restore_preview,
    restore_snapshot,
)
from lib.topics.validation import audit_graph

from web.blueprints.topics import topics_bp
from web.blueprints.topics._helpers import _error, _repo_path_or_404


# ── /diff (side-effect-free) ────────────────────────────────────────


@topics_bp.route(
    "/api/repos/<name>/topics/proposals/<proposal_id>/topics/<proposed_topic_id>/diff",
    methods=["POST"],
)
def api_repo_topic_proposal_diff(name, proposal_id, proposed_topic_id):
    """Compute the prospective diff for a (proposal, topic, strategy).

    Side-effect-free. Returns the resolved diff + the items that would
    be silently dropped under the supplied (or default) options. UI
    uses this to render the side-by-side diff panel and the resolution
    checkboxes.
    """
    repo_path = _repo_path_or_404(name)
    if repo_path is None:
        return jsonify({"error": "not found"}), 404
    payload = request.get_json(silent=True) or {}
    try:
        result = diff_proposal_topic(
            repo_path, proposal_id, proposed_topic_id,
            strategy=payload.get("strategy", "create"),
            target_topic_id=payload.get("target_topic_id"),
            options=payload.get("options"),
        )
    except LookupError:
        return jsonify({"error": "not found"}), 404
    except (ValueError, TopicGraphError) as exc:
        return _error(exc)
    return jsonify({"ok": True, **result})


# ── /apply (commits) ────────────────────────────────────────────────


@topics_bp.route(
    "/api/repos/<name>/topics/proposals/<proposal_id>/topics/<proposed_topic_id>/apply",
    methods=["POST"],
)
def api_repo_topic_proposal_apply(name, proposal_id, proposed_topic_id):
    """Resolve + apply a proposed topic atomically.

    Request body: `{strategy, target_topic_id?, options}`. The diff is
    recomputed server-side — clients MUST NOT send the diff itself.

    Returns:
      200: `{ok, snapshot_id, applied_diff, dropped_items}`
      400 (unresolvable): `{ok=false, error="unresolvable_errors", diff, dropped_items}`
        — UI re-renders the same panel with the unresolved errors
        highlighted so the user can toggle more checkboxes.
    """
    repo_path = _repo_path_or_404(name)
    if repo_path is None:
        return jsonify({"error": "not found"}), 404
    payload = request.get_json(silent=True) or {}
    try:
        result = apply_proposal_topic(
            repo_path, proposal_id, proposed_topic_id,
            strategy=payload.get("strategy", "create"),
            target_topic_id=payload.get("target_topic_id"),
            options=payload.get("options"),
        )
    except LookupError:
        return jsonify({"error": "not found"}), 404
    except (ValueError, TopicGraphError) as exc:
        return _error(exc)
    if not result.get("ok"):
        return jsonify(result), 400
    return jsonify(result)


# ── /audit ──────────────────────────────────────────────────────────


@topics_bp.route("/api/repos/<name>/topics/audit", methods=["GET"])
def api_repo_topic_audit(name):
    """List the live graph's validation issues, grouped by code.

    Reads the approved graph from disk (still authoritative
    through Phase D); same machinery the diff layer uses to compute
    pre-existing rot. Powers the Audit workspace tab in Phase C.
    """
    repo_path = _repo_path_or_404(name)
    if repo_path is None:
        return jsonify({"error": "not found"}), 404
    try:
        graph = load_authoritative_graph(repo_path)
    except TopicGraphError as exc:
        return _error(exc, 404)

    issues = audit_graph(graph, repo_path=repo_path)
    by_code: dict[str, list[dict[str, Any]]] = {}
    for issue in issues:
        by_code.setdefault(issue.code, []).append(serialize_issue(issue))
    return jsonify({
        "ok": True,
        "issues": [serialize_issue(i) for i in issues],
        "by_code": by_code,
        "error_count": sum(1 for i in issues if i.severity == "error"),
        "warning_count": sum(1 for i in issues if i.severity == "warning"),
        "auto_fixable_codes": sorted(AUTO_FIXABLE_CODES),
    })


@topics_bp.route("/api/repos/<name>/topics/audit/fix", methods=["POST"])
def api_repo_topic_audit_fix(name):
    """Auto-fix the unambiguous audit issues.

    Body: `{issue_codes: [str]}`. Only codes in `AUTO_FIXABLE_CODES`
    (currently `graph.dead_ref`, `graph.orphan_edge_target`) are
    actioned; the rest are reported as `skipped_codes`. Returns one
    snapshot per affected topic.

    Why not duplicate_alias: picking which side of a collision to
    drop is destructive and provenance can't always tell you the
    right answer. Surface in the audit list, resolve manually via
    DiffPanel or by editing the proposal that introduced it.
    """
    repo_path = _repo_path_or_404(name)
    if repo_path is None:
        return jsonify({"error": "not found"}), 404
    payload = request.get_json(silent=True) or {}
    requested_codes = set(payload.get("issue_codes") or [])
    if not requested_codes:
        return _error(TopicGraphError("issue_codes is required"))
    skipped_codes = sorted(requested_codes - AUTO_FIXABLE_CODES)
    actionable_codes = requested_codes & AUTO_FIXABLE_CODES
    if not actionable_codes:
        return jsonify({
            "ok": True,
            "snapshot_ids": [],
            "fixed_counts": {},
            "skipped_codes": skipped_codes,
        })

    try:
        graph = load_authoritative_graph(repo_path)
    except TopicGraphError as exc:
        return _error(exc, 404)
    issues = audit_graph(graph, repo_path=repo_path)
    fixes = compose_fix(graph, issues, codes_to_fix=actionable_codes)
    if not fixes:
        return jsonify({
            "ok": True,
            "snapshot_ids": [],
            "fixed_counts": {c: 0 for c in actionable_codes},
            "skipped_codes": skipped_codes,
        })

    repo = resolve_or_create_repo(repo_path)
    snapshot_ids: list[int] = []
    fixed_counts: dict[str, int] = {c: 0 for c in actionable_codes}
    # Apply per-topic; each apply_diff sees the running prospective
    # graph from the previous fix so later issues are computed against
    # the corrected state.
    running_graph = json.loads(json.dumps(graph))
    for topic_id, cleaned, before in fixes:
        prospective = json.loads(json.dumps(running_graph))
        prospective.setdefault("topics", {})[topic_id] = cleaned
        delta = compute_topic_delta(
            topic_id_after=topic_id, kind="replace",
            before=before, after=cleaned,
        )
        diff = GraphDiff(
            topic_deltas=(delta,),
            graph_warnings=(),
            introduced_errors=(),
            valid_strategies_by_topic={topic_id: ("replace",)},
            strategy="replace",
            target_topic_id=None,
            proposed_topic_id=topic_id,
            prospective_graph=prospective,
        )
        try:
            result = apply_diff(repo.id, diff, reason="bulk_fix")
        except (ValueError, TopicGraphError) as exc:
            return _error(exc)
        snapshot_ids.append(result.snapshot_id)
        fixed_counts["graph.dead_ref"] = fixed_counts.get("graph.dead_ref", 0) + len(delta.ref_removes)
        fixed_counts["graph.orphan_edge_target"] = fixed_counts.get("graph.orphan_edge_target", 0) + len(delta.edge_removes)
        running_graph = prospective

    return jsonify({
        "ok": True,
        "snapshot_ids": snapshot_ids,
        "fixed_counts": fixed_counts,
        "skipped_codes": skipped_codes,
    })


# ── /snapshots ──────────────────────────────────────────────────────


def _snapshot_row(snap: GraphSnapshot) -> dict[str, Any]:
    summary = json.loads(snap.diff_summary_json or "{}")
    return {
        "id": snap.id,
        "taken_at": snap.taken_at,
        "reason": snap.reason,
        "triggering_run_id": snap.triggering_run_id,
        "is_latest": bool(snap.is_latest),
        "pinned": bool(snap.pinned),
        "summary": summary,
    }


def _repo_or_404(name: str) -> Repo | None:
    with SessionLocal() as s:
        return s.exec(select(Repo).where(Repo.name == name)).first()


@topics_bp.route("/api/repos/<name>/topics/snapshots", methods=["GET"])
def api_repo_topic_snapshots(name):
    repo = _repo_or_404(name)
    if repo is None:
        return jsonify({"error": "not found"}), 404
    try:
        limit = int(request.args.get("limit", "50"))
    except ValueError:
        limit = 50
    rows = list_snapshots(repo.id, limit=limit)
    return jsonify({
        "ok": True,
        "snapshots": [_snapshot_row(s) for s in rows],
        "latest_id": next((s.id for s in rows if s.is_latest), None),
    })


@topics_bp.route("/api/repos/<name>/topics/snapshots/<int:snapshot_id>/restore-preview", methods=["GET"])
def api_repo_topic_snapshot_restore_preview(name, snapshot_id):
    """Show what would change if `snapshot_id` were restored.

    Pure read; no mutation. Drives the "Preview before restore" panel.
    """
    repo = _repo_or_404(name)
    if repo is None:
        return jsonify({"error": "not found"}), 404
    with SessionLocal() as s:
        snap = s.get(GraphSnapshot, snapshot_id)
        if snap is None or snap.repo_id != repo.id:
            return jsonify({"error": "not found"}), 404
    try:
        preview = restore_preview(snapshot_id)
    except ValueError as exc:
        return _error(exc)
    return jsonify({"ok": True, "preview": preview})


@topics_bp.route("/api/repos/<name>/topics/snapshots/<int:snapshot_id>/restore", methods=["POST"])
def api_repo_topic_snapshot_restore(name, snapshot_id):
    repo = _repo_or_404(name)
    if repo is None:
        return jsonify({"error": "not found"}), 404
    try:
        with SessionLocal() as s:
            snap = s.get(GraphSnapshot, snapshot_id)
            if snap is None or snap.repo_id != repo.id:
                return jsonify({"error": "not found"}), 404
        restored = restore_snapshot(snapshot_id)
        # After restore, write to the overlay so legacy readers see the new
        # state without mutating the git-tracked base graph. The split
        # keeps merge(base, overlay) hash-equal to the restored snapshot.
        from lib.topics.graph_io import export_overlay_to_disk
        restored_graph = json.loads(restored.graph_json)
        export_overlay_to_disk(
            repo.path,
            restored_graph,
            json.loads(restored.wiki_pages_json or "{}"),
        )
        # Refresh the wiki dense index for the restored state in the
        # background — same rationale as apply_diff. Best-effort: a missing
        # embedding model never blocks the restore.
        #
        # Pass `graph=restored_graph` so the bg thread skips
        # `load_authoritative_graph`. Without it, a concurrent
        # apply_diff / restore between the bg thread's snapshot read and
        # disk read makes the bg thread observe `snap_hash != disk_hash`
        # and trigger `_auto_seed_snapshot`, inserting an extra
        # `is_latest=1` row. See `lib/topics/apply.py::_bg_reindex`.
        try:
            import threading
            from lib.patterns.wiki_indexer import index_wikis_best_effort
            threading.Thread(
                target=lambda: index_wikis_best_effort(repo, graph=restored_graph),
                name=f"wiki-index-restore-{repo.id}",
                daemon=True,
            ).start()
        except Exception:  # noqa: BLE001 — restore must not regress on indexing
            pass
    except (ValueError, TopicGraphError) as exc:
        return _error(exc)
    return jsonify({"ok": True, "snapshot": _snapshot_row(restored)})


@topics_bp.route("/api/repos/<name>/topics/snapshots/<int:snapshot_id>/pin", methods=["POST"])
def api_repo_topic_snapshot_pin(name, snapshot_id):
    return _set_pinned(name, snapshot_id, value=1)


@topics_bp.route("/api/repos/<name>/topics/snapshots/<int:snapshot_id>/unpin", methods=["POST"])
def api_repo_topic_snapshot_unpin(name, snapshot_id):
    return _set_pinned(name, snapshot_id, value=0)


def _set_pinned(name: str, snapshot_id: int, *, value: int):
    repo = _repo_or_404(name)
    if repo is None:
        return jsonify({"error": "not found"}), 404
    with SessionLocal() as s:
        snap = s.get(GraphSnapshot, snapshot_id)
        if snap is None or snap.repo_id != repo.id:
            return jsonify({"error": "not found"}), 404
        snap.pinned = value
        s.add(snap)
        s.commit()
        s.refresh(snap)
    return jsonify({"ok": True, "snapshot": _snapshot_row(snap)})


# ── /wiki/reindex ──────────────────────────────────────────────────
#
# Synchronous wiki dense-index refresh for a single repo. The accept
# path auto-runs the same indexer in a background thread, so this
# button exists for: (a) cold-start backfill on a fresh install,
# (b) explicit force-refresh when the user suspects drift, (c) a
# visible feedback signal that the index actually updated.
#
# Sync (not background) is intentional: the user clicked, so they're
# willing to wait through the cold-start ~10s model load on first
# call. Subsequent calls are sub-second per page.


@topics_bp.route("/api/repos/<name>/topics/wiki/reindex", methods=["POST"])
def api_repo_wiki_reindex(name):
    repo = _repo_or_404(name)
    if repo is None:
        return jsonify({"error": "not found"}), 404
    from lib.patterns.wiki_indexer import index_wikis
    from lib.skills import skill_router
    try:
        # force=True: this is the manual "force-refresh" button (see the
        # tooltip in RepoTopicsView). Without it, index_wikis short-circuits
        # every unchanged page on its content-hash skip gate and always
        # reports indexed=0. The CLI path (cli/commands/wiki.py) already
        # passes force; the web route used to omit it.
        counts = index_wikis(repo, force=True)
    except skill_router.DependencyError as exc:
        return jsonify({
            "ok": False,
            "error": "embedding dependencies missing",
            "detail": str(exc),
        }), 503
    except Exception as exc:  # noqa: BLE001 — surface the failure as 500
        return jsonify({"ok": False, "error": str(exc)}), 500
    return jsonify({"ok": True, "repo": name, "counts": counts})
