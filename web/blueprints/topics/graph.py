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


@topics_bp.route("/api/repos/<name>/topics")
def api_repo_topics(name):
    repo_path = _repo_path_or_404(name)
    if repo_path is None:
        return jsonify({"error": "not found"}), 404
    _ensure_topic_graph(repo_path)
    try:
        return jsonify(topic_summary(repo_path))
    except TopicGraphError as exc:
        return _error(exc)


@topics_bp.route("/api/repos/<name>/topics/<topic_id>")
def api_repo_topic_detail(name, topic_id):
    repo_path = _repo_path_or_404(name)
    if repo_path is None:
        return jsonify({"error": "not found"}), 404
    try:
        return jsonify(topic_detail(repo_path, topic_id))
    except TopicGraphError as exc:
        return _error(exc, 404)


@topics_bp.route("/api/repos/<name>/topics/scan", methods=["POST"])
def api_repo_topics_scan(name):
    repo_path = _repo_path_or_404(name)
    if repo_path is None:
        return jsonify({"error": "not found"}), 404
    _ensure_topic_graph(repo_path)
    try:
        result = scan(repo_path)
        return jsonify({"ok": True, **result})
    except TopicGraphError as exc:
        return _error(exc)


@topics_bp.route("/api/repos/<name>/topics/<topic_id>", methods=["POST"])
def api_repo_topic_update(name, topic_id):
    repo_path = _repo_path_or_404(name)
    if repo_path is None:
        return jsonify({"error": "not found"}), 404
    try:
        topic = update_topic(repo_path, topic_id, request.get_json(silent=True) or {})
        return jsonify({"ok": True, "topic": topic})
    except TopicGraphError as exc:
        return _error(exc)


