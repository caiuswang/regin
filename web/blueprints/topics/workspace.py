"""Topics endpoints split by purpose."""

from __future__ import annotations

from pathlib import Path

from flask import jsonify, request
from sqlmodel import select

from lib.orm import SessionLocal
from lib.orm.models import Repo
from lib.topics.proposal_providers import list_proposal_providers
from lib.topics import (
    TopicGraphError,
    bootstrap,
    scan,
    topic_detail,
    topic_summary,
    update_topic,
)
from lib.topics.proposals import (
    accept_proposed_topic,
    create_proposal_run,
    delete_proposal_run,
    downgrade_topic_to_proposal,
    ignore_proposed_topic,
    list_proposal_runs,
    load_proposal,
    load_proposal_status,
    merge_proposed_topic,
    start_external_proposal_run,
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


@topics_bp.route("/api/repos/<name>/topics/proposals")
def api_repo_topic_proposals(name):
    repo_path = _repo_path_or_404(name)
    if repo_path is None:
        return jsonify({"error": "not found"}), 404
    return jsonify({"proposals": list_proposal_runs(repo_path)})


@topics_bp.route("/api/repos/<name>/topics/workspace/wiki")
def api_repo_topics_workspace_wiki(name):
    repo_path = _repo_path_or_404(name)
    if repo_path is None:
        return jsonify({"error": "not found"}), 404
    _ensure_topic_graph(repo_path)
    topic_id = request.args.get("topic_id")
    try:
        return jsonify(_wiki_workspace_payload(repo_path, topic_id))
    except TopicGraphError as exc:
        return _error(exc, 404)


@topics_bp.route("/api/repos/<name>/topics/workspace/summary")
def api_repo_topics_workspace_summary(name):
    repo_path = _repo_path_or_404(name)
    if repo_path is None:
        return jsonify({"error": "not found"}), 404
    _ensure_topic_graph(repo_path)
    try:
        return jsonify(_workspace_summary_payload(repo_path))
    except TopicGraphError as exc:
        return _error(exc, 404)


@topics_bp.route("/api/repos/<name>/topics/workspace/proposals")
def api_repo_topics_workspace_proposals(name):
    repo_path = _repo_path_or_404(name)
    if repo_path is None:
        return jsonify({"error": "not found"}), 404
    _ensure_topic_graph(repo_path)
    proposal_id = request.args.get("proposal_id")
    draft_topic_id = request.args.get("draft_topic_id")
    revision_id = request.args.get("revision_id")
    try:
        return jsonify(_proposal_workspace_payload(
            repo_path,
            selected_proposal_id=proposal_id,
            selected_draft_topic_id=draft_topic_id,
            selected_revision_id=revision_id,
        ))
    except TopicGraphError as exc:
        return _error(exc, 404)


@topics_bp.route("/api/repos/<name>/topics/proposal-providers")
def api_repo_topic_proposal_providers(name):
    repo_path = _repo_path_or_404(name)
    if repo_path is None:
        return jsonify({"error": "not found"}), 404
    return jsonify({"providers": list_proposal_providers()})
