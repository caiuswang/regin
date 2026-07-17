"""Topics endpoints split by purpose."""

from __future__ import annotations

from pathlib import Path

from flask import jsonify, request
from sqlmodel import select

from lib.orm import SessionLocal
from lib.orm.models import Repo
from lib.prompt_templates import default_template_slugs_for
from lib.topics.proposal_providers import list_proposal_providers
from lib.topics import (
    TopicGraphError,
    bootstrap,
    scan,
    topic_detail,
    topic_dir,
    topic_summary,
    update_topic,
)
from lib.topics.proposals import (
    add_proposal_feedback_comment,
    create_proposal_feedback_thread,
    delete_proposal_feedback_comment,
    delete_proposal_run,
    dismiss_content_drift_thread,
    downgrade_topic_to_proposal,
    ignore_proposed_topic,
    list_proposal_feedback_threads,
    list_proposal_runs,
    load_proposal,
    load_proposal_status,
    restore_proposal_to_revision,
    set_proposal_feedback_thread_resolution,
    set_proposal_review_state,
    start_external_proposal_run,
    stop_proposal_run,
    update_proposal_feedback_comment,
    update_proposed_topic,
)
from lib.topics.wiki import generate_wiki

from web.blueprints import topics as _pkg
from web.blueprints.topics import topics_bp
from web.blueprints.topics._helpers import (
    _repo_path_or_404, _error, _ensure_topic_graph, _proposal_wiki,
    _proposal_provider_from_payload, _proposal_complexity_from_payload,
    _proposal_run_row, _proposal_topic_row, _wiki_workspace_payload,
    _proposal_workspace_payload, _workspace_summary_payload,
)


@topics_bp.route("/api/repos/<name>/topics/proposals", methods=["POST"])
def api_repo_topic_proposals_create(name):
    repo_path = _repo_path_or_404(name)
    if repo_path is None:
        return jsonify({"error": "not found"}), 404
    payload = request.get_json(silent=True) or {}
    # None  -> fall back to provider defaults
    # []    -> caller explicitly opted out of all templates
    # [...] -> caller picked a specific set
    if "prompt_template_ids" in payload:
        raw_ids = payload.get("prompt_template_ids") or []
        prompt_template_ids = [str(slug) for slug in raw_ids if slug]
    else:
        prompt_template_ids = default_template_slugs_for("external-agent")
    try:
        paths = start_external_proposal_run(
            repo_path,
            agent=payload.get("agent"),
            topic_request=payload.get("topic_request") or payload.get("topic"),
            prompt_template_ids=prompt_template_ids,
        )
    except (ValueError, TopicGraphError) as exc:
        return _error(exc)
    return jsonify({
        "ok": True,
        "proposal": {
            "id": paths["dir"].name,
            "path": str(paths["dir"]),
            "wiki": str(paths["wiki"]),
            "status": load_proposal_status(repo_path, paths["dir"].name),
        },
    })


@topics_bp.route("/api/repos/<name>/topics/proposals/<proposal_id>")
def api_repo_topic_proposal_detail(name, proposal_id):
    repo_path = _repo_path_or_404(name)
    if repo_path is None:
        return jsonify({"error": "not found"}), 404
    try:
        proposal = load_proposal(repo_path, proposal_id)
        status = load_proposal_status(repo_path, proposal_id)
    except OSError:
        return jsonify({"error": "not found"}), 404
    except TopicGraphError as exc:
        return _error(exc, 404)
    wiki_path = topic_dir(repo_path) / "proposals" / proposal_id / "wiki.md"
    wiki = wiki_path.read_text() if wiki_path.exists() else ""
    return jsonify({
        "proposal": proposal,
        "wiki": wiki,
        "status": status,
        "feedback_threads": list_proposal_feedback_threads(repo_path, proposal_id),
    })


@topics_bp.route("/api/repos/<name>/topics/proposals/<proposal_id>/status")
def api_repo_topic_proposal_status(name, proposal_id):
    repo_path = _repo_path_or_404(name)
    if repo_path is None:
        return jsonify({"error": "not found"}), 404
    try:
        return jsonify({"ok": True, "status": load_proposal_status(repo_path, proposal_id)})
    except TopicGraphError as exc:
        return _error(exc, 404)


@topics_bp.route("/api/repos/<name>/topics/proposals/<proposal_id>/regenerate", methods=["POST"])
def api_repo_topic_proposal_regenerate(name, proposal_id):
    repo_path = _repo_path_or_404(name)
    if repo_path is None:
        return jsonify({"error": "not found"}), 404
    # Optional caller-chosen scope: regenerate only these topics' wikis (the
    # rest are preserved verbatim). Absent/empty ⇒ full re-draft, or the
    # drift-derived scope when the run carries open content-drift notes.
    payload = request.get_json(silent=True) or {}
    raw_ids = payload.get("topic_ids")
    topic_ids = [t for t in raw_ids if isinstance(t, str) and t] if isinstance(raw_ids, list) else None
    try:
        paths = _pkg.regenerate_proposal_run(repo_path, proposal_id, topic_ids=topic_ids)
    except (ValueError, TopicGraphError) as exc:
        return _error(exc)
    # The run has started (state=queued) once regenerate_proposal_run returns.
    # The proposal/wiki payload is best-effort: a run that failed before it
    # drafted has no wiki.md on disk, and reading it must NOT turn a
    # successfully-started regenerate into a 404 — that leaves the UI stuck on
    # the stale 'failed' badge while the run is actually live, forcing a manual
    # refresh to see it running.
    proposal = None
    try:
        proposal = load_proposal(repo_path, proposal_id)
    except (OSError, ValueError, TopicGraphError):
        # ValueError covers a corrupt topics.json on the disk-fallback path
        # (json.JSONDecodeError); none of these may 500 a *started* run.
        proposal = None
    try:
        wiki = paths["wiki"].read_text()
    except OSError:
        wiki = ""
    return jsonify({"ok": True, "proposal": proposal, "wiki": wiki})


@topics_bp.route("/api/repos/<name>/topics/proposals/<proposal_id>/review-note", methods=["POST"])
def api_repo_topic_proposal_review_note(name, proposal_id):
    """Generate an LLM review note for a proposal on demand (ungated — this is
    an explicit user action). Returns the created feedback thread, or ok with
    thread=None when no external agent is configured / the run has no draft."""
    repo_path = _repo_path_or_404(name)
    if repo_path is None:
        return jsonify({"error": "not found"}), 404
    from lib.topics.proposal_review import generate_review_note
    try:
        thread = generate_review_note(repo_path, proposal_id)
        return jsonify({"ok": True, "thread": thread})
    except OSError:
        return jsonify({"error": "not found"}), 404
    except (ValueError, TopicGraphError) as exc:
        return _error(exc)


@topics_bp.route("/api/repos/<name>/topics/proposals/<proposal_id>/review-state", methods=["POST"])
def api_repo_topic_proposal_review_state(name, proposal_id):
    repo_path = _repo_path_or_404(name)
    if repo_path is None:
        return jsonify({"error": "not found"}), 404
    payload = request.get_json(silent=True) or {}
    try:
        review_state = str(payload.get("review_state") or "")
        if review_state not in {"pending_review", "changes_requested", "ready_to_apply"}:
            raise TopicGraphError(
                "review_state must be one of ['changes_requested', 'pending_review', 'ready_to_apply']"
            )
        proposal = set_proposal_review_state(
            repo_path,
            proposal_id,
            review_state,
        )
        return jsonify({"ok": True, "proposal": proposal})
    except OSError:
        return jsonify({"error": "not found"}), 404
    except TopicGraphError as exc:
        return _error(exc)


@topics_bp.route("/api/repos/<name>/topics/proposals/<proposal_id>/restore", methods=["POST"])
def api_repo_topic_proposal_restore(name, proposal_id):
    repo_path = _repo_path_or_404(name)
    if repo_path is None:
        return jsonify({"error": "not found"}), 404
    payload = request.get_json(silent=True) or {}
    try:
        revision_id = int(payload.get("revision_id") or 0)
    except (TypeError, ValueError):
        revision_id = 0
    if revision_id <= 0:
        return _error(TopicGraphError("revision_id is required"))
    try:
        proposal = restore_proposal_to_revision(repo_path, proposal_id, revision_id)
        return jsonify({"ok": True, "proposal": proposal})
    except OSError:
        return jsonify({"error": "not found"}), 404
    except TopicGraphError as exc:
        return _error(exc)


@topics_bp.route("/api/repos/<name>/topics/proposals/<proposal_id>/stop", methods=["POST"])
def api_repo_topic_proposal_stop(name, proposal_id):
    """Cancel an in-flight proposal run (terminates the agent subprocess)."""
    repo_path = _repo_path_or_404(name)
    if repo_path is None:
        return jsonify({"error": "not found"}), 404
    try:
        result = stop_proposal_run(repo_path, proposal_id)
        return jsonify({"ok": True, "result": result})
    except TopicGraphError as exc:
        return _error(exc, 404)


@topics_bp.route("/api/repos/<name>/topics/proposals/<proposal_id>/delete", methods=["POST"])
def api_repo_topic_proposal_delete(name, proposal_id):
    repo_path = _repo_path_or_404(name)
    if repo_path is None:
        return jsonify({"error": "not found"}), 404
    try:
        result = delete_proposal_run(repo_path, proposal_id)
        return jsonify({"ok": True, "proposal": result})
    except TopicGraphError as exc:
        return _error(exc)


@topics_bp.route("/api/repos/<name>/topics/proposals/<proposal_id>/topics/<proposed_topic_id>", methods=["POST"])
def api_repo_topic_proposal_topic_update(name, proposal_id, proposed_topic_id):
    repo_path = _repo_path_or_404(name)
    if repo_path is None:
        return jsonify({"error": "not found"}), 404
    try:
        topic = update_proposed_topic(
            repo_path,
            proposal_id,
            proposed_topic_id,
            request.get_json(silent=True) or {},
        )
        return jsonify({"ok": True, "topic": topic})
    except OSError:
        return jsonify({"error": "not found"}), 404
    except TopicGraphError as exc:
        return _error(exc)


@topics_bp.route("/api/repos/<name>/topics/proposals/<proposal_id>/ignore", methods=["POST"])
def api_repo_topic_proposal_ignore(name, proposal_id):
    repo_path = _repo_path_or_404(name)
    if repo_path is None:
        return jsonify({"error": "not found"}), 404
    payload = request.get_json(silent=True) or {}
    proposed_topic_id = payload.get("proposed_topic_id")
    if not proposed_topic_id:
        return _error(TopicGraphError("proposed_topic_id is required"))
    try:
        # Human ignore: for a standalone content-drift refresh this means "the
        # change was unrelated to the wiki", so advance the drift baseline too.
        # Automated ignores (expiry / trivial-dismiss) deliberately do not.
        topic = ignore_proposed_topic(repo_path, proposal_id, proposed_topic_id,
                                      rebaseline_drift=True)
        return jsonify({"ok": True, "topic": topic})
    except OSError:
        return jsonify({"error": "not found"}), 404
    except TopicGraphError as exc:
        return _error(exc)


@topics_bp.route("/api/repos/<name>/topics/proposals/<proposal_id>/feedback-threads", methods=["POST"])
def api_repo_topic_proposal_feedback_threads_create(name, proposal_id):
    repo_path = _repo_path_or_404(name)
    if repo_path is None:
        return jsonify({"error": "not found"}), 404
    payload = request.get_json(silent=True) or {}
    try:
        thread = create_proposal_feedback_thread(
            repo_path,
            proposal_id,
            proposal_topic_id=payload.get("proposal_topic_id"),
            kind=payload.get("kind", "comment"),
            anchor_kind=payload.get("anchor_kind", "general"),
            anchor=payload.get("anchor") or {},
            quoted_text=payload.get("quoted_text"),
            body=payload.get("body") or "",
            created_by=payload.get("author_kind", "user"),
        )
        return jsonify({"ok": True, "feedback_thread": thread})
    except OSError:
        return jsonify({"error": "not found"}), 404
    except TopicGraphError as exc:
        return _error(exc)


@topics_bp.route(
    "/api/repos/<name>/topics/proposals/<proposal_id>/feedback-threads/<int:feedback_thread_id>/resolution",
    methods=["POST"],
)
def api_repo_topic_proposal_feedback_thread_resolution(name, proposal_id, feedback_thread_id):
    repo_path = _repo_path_or_404(name)
    if repo_path is None:
        return jsonify({"error": "not found"}), 404
    payload = request.get_json(silent=True) or {}
    try:
        thread = set_proposal_feedback_thread_resolution(
            repo_path,
            proposal_id,
            feedback_thread_id,
            resolution_state=payload.get("resolution_state") or "",
        )
        return jsonify({"ok": True, "feedback_thread": thread})
    except OSError:
        return jsonify({"error": "not found"}), 404
    except TopicGraphError as exc:
        return _error(exc)


@topics_bp.route(
    "/api/repos/<name>/topics/proposals/<proposal_id>/feedback-threads/<int:feedback_thread_id>/dismiss-drift",
    methods=["POST"],
)
def api_repo_topic_proposal_feedback_thread_dismiss_drift(name, proposal_id, feedback_thread_id):
    """Dismiss a content-drift note as unrelated to the wiki *and* re-baseline
    the topic's ref digests so it doesn't re-fire on the next evolve pass —
    the escape hatch plain 'resolve' can't provide."""
    repo_path = _repo_path_or_404(name)
    if repo_path is None:
        return jsonify({"error": "not found"}), 404
    try:
        result = dismiss_content_drift_thread(
            repo_path, proposal_id, feedback_thread_id)
        return jsonify({"ok": True, **result})
    except OSError:
        return jsonify({"error": "not found"}), 404
    except TopicGraphError as exc:
        return _error(exc)


@topics_bp.route(
    "/api/repos/<name>/topics/proposals/<proposal_id>/feedback-threads/<int:feedback_thread_id>/comments",
    methods=["POST"],
)
def api_repo_topic_proposal_feedback_comments_create(name, proposal_id, feedback_thread_id):
    repo_path = _repo_path_or_404(name)
    if repo_path is None:
        return jsonify({"error": "not found"}), 404
    payload = request.get_json(silent=True) or {}
    try:
        thread = add_proposal_feedback_comment(
            repo_path,
            proposal_id,
            feedback_thread_id,
            body=payload.get("body") or "",
            author_kind=payload.get("author_kind", "user"),
        )
        return jsonify({"ok": True, "feedback_thread": thread})
    except OSError:
        return jsonify({"error": "not found"}), 404
    except TopicGraphError as exc:
        return _error(exc)


@topics_bp.route(
    "/api/repos/<name>/topics/proposals/<proposal_id>"
    "/feedback-threads/<int:feedback_thread_id>/comments/<int:comment_id>/update",
    methods=["POST"],
)
def api_repo_topic_proposal_feedback_comment_update(name, proposal_id, feedback_thread_id, comment_id):
    repo_path = _repo_path_or_404(name)
    if repo_path is None:
        return jsonify({"error": "not found"}), 404
    payload = request.get_json(silent=True) or {}
    try:
        thread = update_proposal_feedback_comment(
            repo_path,
            proposal_id,
            feedback_thread_id,
            comment_id,
            body=payload.get("body") or "",
        )
        return jsonify({"ok": True, "feedback_thread": thread})
    except OSError:
        return jsonify({"error": "not found"}), 404
    except TopicGraphError as exc:
        return _error(exc)


@topics_bp.route(
    "/api/repos/<name>/topics/proposals/<proposal_id>"
    "/feedback-threads/<int:feedback_thread_id>/comments/<int:comment_id>/delete",
    methods=["POST"],
)
def api_repo_topic_proposal_feedback_comment_delete(name, proposal_id, feedback_thread_id, comment_id):
    repo_path = _repo_path_or_404(name)
    if repo_path is None:
        return jsonify({"error": "not found"}), 404
    try:
        result = delete_proposal_feedback_comment(
            repo_path,
            proposal_id,
            feedback_thread_id,
            comment_id,
        )
        if result.get("deleted_thread"):
            return jsonify({"ok": True, "deleted_thread": True, "feedback_thread_id": feedback_thread_id})
        return jsonify({"ok": True, "feedback_thread": result})
    except OSError:
        return jsonify({"error": "not found"}), 404
    except TopicGraphError as exc:
        return _error(exc)
