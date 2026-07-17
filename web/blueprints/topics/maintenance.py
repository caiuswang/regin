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
    delete_topic,
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


@topics_bp.route("/api/repos/<name>/topics/<topic_id>/downgrade", methods=["POST"])
def api_repo_topic_downgrade(name, topic_id):
    repo_path = _repo_path_or_404(name)
    if repo_path is None:
        return jsonify({"error": "not found"}), 404
    try:
        result = downgrade_topic_to_proposal(repo_path, topic_id)
        return jsonify({"ok": True, **result})
    except TopicGraphError as exc:
        return _error(exc)


@topics_bp.route("/api/repos/<name>/topics/<topic_id>/delete", methods=["POST"])
def api_repo_topic_delete(name, topic_id):
    repo_path = _repo_path_or_404(name)
    if repo_path is None:
        return jsonify({"error": "not found"}), 404
    try:
        result = delete_topic(repo_path, topic_id)
        return jsonify({"ok": True, **result})
    except TopicGraphError as exc:
        return _error(exc)


@topics_bp.route("/api/repos/<name>/topics/import", methods=["POST"])
def api_repo_topics_import(name):
    """Sync git-shipped topics into the local snapshot AND rebuild the
    dense wiki search index. The graph reseeds on any page read, but the
    search index does not — so this button does the part a passive view
    can't.
    """
    repo_path = _repo_path_or_404(name)
    if repo_path is None:
        return jsonify({"error": "not found"}), 404
    from lib.patterns.wiki_indexer import index_wikis
    from lib.skills import skill_router
    from lib.topics.graph_io import import_from_disk
    from lib.topics.proposals import backfill_disk_proposals_to_orm
    try:
        result = import_from_disk(repo_path, reason="web")
    except TopicGraphError as exc:
        return _error(exc)
    # Pull any on-disk proposal directories into the proposal_runs ORM
    # table so feedback threads / status updates can find them. Best
    # effort: a missing or malformed status.json shouldn't fail sync.
    try:
        proposal_backfill = backfill_disk_proposals_to_orm(repo_path)
    except Exception as exc:  # noqa: BLE001
        proposal_backfill = {"error": str(exc)}
    # Rebuild the dense wiki index; best-effort so a missing embedding
    # model never fails the import itself.
    wiki_index = None
    try:
        with SessionLocal() as s:
            repo = s.exec(select(Repo).where(Repo.path == str(Path(repo_path).resolve()))).first()
        if repo is not None:
            wiki_index = index_wikis(repo)
    except skill_router.DependencyError as exc:
        wiki_index = {"error": "embedding deps missing", "detail": str(exc)}
    except Exception as exc:  # noqa: BLE001 — never fail the import on indexing
        wiki_index = {"error": str(exc)}
    return jsonify({
        "ok": True, **result,
        "wiki_index": wiki_index,
        "proposal_backfill": proposal_backfill,
    })


@topics_bp.route("/api/repos/<name>/topics/wiki", methods=["POST"])
def api_repo_topics_wiki(name):
    repo_path = _repo_path_or_404(name)
    if repo_path is None:
        return jsonify({"error": "not found"}), 404
    try:
        paths = generate_wiki(repo_path)
    except (TopicGraphError, ValueError) as exc:
        return _error(exc)
    return jsonify({"ok": True, "paths": [str(path) for path in paths], "count": len(paths)})
