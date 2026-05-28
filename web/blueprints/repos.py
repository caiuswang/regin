"""Repo management endpoints (list / add / remove / detail).

Backs the /repos page. Replaces the old auto-discovery model — each
repo is explicitly registered by absolute path. Mutations require
the `editor` role.
"""

from __future__ import annotations

from flask import Blueprint, jsonify, request
from sqlalchemy import func
from sqlmodel import select

from lib.auth import require_editor
from lib.orm import SessionLocal
from lib.orm.models import Branch, PatternDeployment, PatternDoc, Repo
from lib.sync.repo_discovery import RepoAddError, add_repo, remove_repo
from lib.topics.route import topic_summary


repos_bp = Blueprint("repos", __name__)


def _repo_to_dict(r: Repo) -> dict:
    return {
        "id": r.id, "name": r.name, "path": r.path,
        "description": r.description, "is_active": r.is_active,
        "default_branch": r.default_branch,
        "created_at": r.created_at, "updated_at": r.updated_at,
    }


def _branch_to_dict(b: Branch) -> dict:
    return {
        "id": b.id, "repo_id": b.repo_id, "name": b.name,
        "is_tracked": b.is_tracked, "created_at": b.created_at,
    }


def _pattern_to_dict(pd: PatternDoc) -> dict:
    return {
        "id": pd.id, "slug": pd.slug, "title": pd.title,
        "file_path": pd.file_path, "category": pd.category,
        "content_hash": pd.content_hash,
        "created_at": pd.created_at, "updated_at": pd.updated_at,
    }


def _wiki_to_dict(pd: PatternDoc, slug_prefix: str) -> dict:
    # topic_id is the slug tail after `wiki/<repo>/`; the Topics view
    # deep-links on it via `?topic=<id>`.
    topic_id = pd.slug[len(slug_prefix):] if pd.slug.startswith(slug_prefix) else pd.slug
    return {
        "id": pd.id, "slug": pd.slug, "title": pd.title,
        "topic_id": topic_id, "category": pd.category,
        "updated_at": pd.updated_at,
    }


@repos_bp.route("/api/repos")
def api_list_repos():
    """List every registered repo with branch + pattern_count."""
    with SessionLocal() as session:
        rows = session.exec(
            select(Repo, Branch)
            .outerjoin(Branch, (Branch.repo_id == Repo.id) & (Branch.is_tracked == 1))
            .where(Repo.is_active == 1)
            .order_by(Repo.name)
        ).all()
        out = []
        for r, b in rows:
            d = _repo_to_dict(r)
            d["branch_name"] = b.name if b else None
            d["pattern_count"] = session.exec(
                select(func.count(func.distinct(PatternDoc.id)))
                .select_from(PatternDeployment)
                .join(PatternDoc, PatternDoc.slug == PatternDeployment.pattern_slug)
                .where(PatternDeployment.scope == "project")
                .where(PatternDeployment.project_id == r.id)
            ).one()
            out.append(d)
    return jsonify({"repos": out})


@repos_bp.route("/api/repos", methods=["POST"])
@require_editor
def api_add_repo():
    """Register a single git repo by path.

    Body: ``{"path": "/abs/path/to/repo"}``.
    Returns 400 on invalid path, 409 on duplicate.
    """
    data = request.get_json(silent=True) or {}
    path = (data.get("path") or "").strip()
    if not path:
        return jsonify({"error": "path is required"}), 400
    try:
        info = add_repo(path)
    except RepoAddError as exc:
        # Use 409 for collisions, 400 for invalid paths.
        msg = str(exc)
        status = 409 if "already" in msg or "another repo" in msg else 400
        return jsonify({"error": msg}), status
    return jsonify({
        "ok": True,
        "repo": info,
        "msg": f"Added {info['name']}.",
    })


@repos_bp.route("/api/repos/<name>", methods=["DELETE"])
@require_editor
def api_remove_repo(name):
    """Unregister a repo by name.

    Drops it from `repo_paths` and the `repos`/`branches` tables. The
    on-disk source tree is untouched.
    """
    result = remove_repo(name)
    if not result["removed"]:
        return jsonify({"error": f"repo not found: {name}"}), 404
    return jsonify({
        "ok": True,
        "name": result["name"],
        "msg": f"Removed {name}.",
    })


@repos_bp.route("/api/repos/<name>")
def api_repo_detail(name):
    """Per-repo detail: branches + patterns."""
    with SessionLocal() as session:
        repo = session.exec(select(Repo).where(Repo.name == name)).first()
        if repo is None:
            return jsonify({"error": "not found"}), 404

        branches = session.exec(
            select(Branch).where(Branch.repo_id == repo.id)
        ).all()

        patterns = session.exec(
            select(PatternDoc)
            .join(PatternDeployment,
                  PatternDeployment.pattern_slug == PatternDoc.slug)
            .where(PatternDeployment.scope == "project")
            .where(PatternDeployment.project_id == repo.id)
            .order_by(PatternDoc.category, PatternDoc.title)
        ).all()

        # Approved-wiki pages live in pattern_docs as source_kind='wiki'
        # rows scoped by repo_id; they carry no deployment record, so the
        # join above can't reach them — query them separately.
        wiki = session.exec(
            select(PatternDoc)
            .where(PatternDoc.source_kind == "wiki")
            .where(PatternDoc.repo_id == repo.id)
            .order_by(PatternDoc.title)
        ).all()
        slug_prefix = f"wiki/{repo.name}/"

        # Approved topics live on disk in topic.json — read directly so the
        # repo page can tell the user "you have N topics but no indexed
        # wikis, press Re-index Wikis" when the dense index is empty.
        try:
            approved_topic_count = len(topic_summary(repo.path)["topics"])
        except Exception:  # noqa: BLE001 — topic.json may be absent on a brand-new repo
            approved_topic_count = 0

        return jsonify({
            "repo": _repo_to_dict(repo),
            "branches": [_branch_to_dict(b) for b in branches],
            "patterns": [_pattern_to_dict(p) for p in patterns],
            "wiki": [_wiki_to_dict(w, slug_prefix) for w in wiki],
            "approved_topic_count": approved_topic_count,
        })
