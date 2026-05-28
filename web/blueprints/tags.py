"""Tag listing, detail, rename, and delete endpoints."""

from __future__ import annotations

import re

from flask import Blueprint, jsonify, request
from sqlalchemy import func
from sqlmodel import select

from lib.auth import require_editor
from lib.orm import SessionLocal
from lib.orm.models import DocTag, PatternDoc, Tag


tags_bp = Blueprint("tags", __name__)

_TAG_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")
_TAG_NAME_MAX = 64


def _tag_dict(tag: Tag) -> dict:
    return {
        "id": tag.id, "name": tag.name,
        "category": tag.category, "description": tag.description,
    }


def _pattern_dict(pd: PatternDoc) -> dict:
    return {
        "id": pd.id, "slug": pd.slug, "title": pd.title,
        "file_path": pd.file_path, "category": pd.category,
        "content_hash": pd.content_hash,
        "created_at": pd.created_at, "updated_at": pd.updated_at,
    }


@tags_bp.route("/api/tags")
def api_tags():
    with SessionLocal() as session:
        stmt = (
            select(
                Tag.id, Tag.name, Tag.category, Tag.description,
                func.count(DocTag.doc_id).label("doc_count"),
            )
            .outerjoin(DocTag, DocTag.tag_id == Tag.id)
            .group_by(Tag.id)
            .order_by(Tag.category, func.count(DocTag.doc_id).desc())
        )
        return jsonify([dict(r._mapping) for r in session.exec(stmt).all()])


@tags_bp.route("/api/tags/<name>")
def api_tag_detail(name):
    with SessionLocal() as session:
        tag = session.exec(select(Tag).where(Tag.name == name)).first()
        if tag is None:
            return jsonify({"error": "not found"}), 404

        docs_stmt = (
            select(PatternDoc)
            .join(DocTag, DocTag.doc_id == PatternDoc.id)
            .where(DocTag.tag_id == tag.id)
            .order_by(PatternDoc.category, PatternDoc.title)
        )
        docs = session.exec(docs_stmt).all()
        return jsonify({
            "tag": _tag_dict(tag),
            "docs": [_pattern_dict(d) for d in docs],
        })


@tags_bp.route("/api/tags/<name>/rename", methods=["POST"])
@require_editor
def api_tag_rename(name):
    data = request.get_json(silent=True) or {}
    new_name = (data.get("name") or "").strip().lower()
    if not new_name:
        return jsonify({"ok": False, "msg": "Name required"})
    if len(new_name) > _TAG_NAME_MAX:
        return jsonify({"ok": False, "msg": f"Name must be {_TAG_NAME_MAX} characters or fewer"})
    if not _TAG_NAME_RE.match(new_name):
        return jsonify({"ok": False, "msg": "Use lowercase letters, digits, and dashes (start with a letter or digit)"})
    with SessionLocal() as session:
        tag = session.exec(select(Tag).where(Tag.name == name)).first()
        if tag is None:
            return jsonify({"error": "not found"}), 404
        if new_name != tag.name:
            clash = session.exec(select(Tag).where(Tag.name == new_name)).first()
            if clash is not None:
                return jsonify({"ok": False, "msg": "Tag name already exists"})
        tag.name = new_name
        session.add(tag)
        session.commit()
        return jsonify({"ok": True, "msg": "Renamed", "new_name": new_name})


@tags_bp.route("/api/tags/<name>/delete", methods=["POST"])
@require_editor
def api_tag_delete(name):
    with SessionLocal() as session:
        tag = session.exec(select(Tag).where(Tag.name == name)).first()
        if tag is None:
            return jsonify({"error": "not found"}), 404

        doc_count = session.exec(
            select(func.count(DocTag.doc_id)).where(DocTag.tag_id == tag.id)
        ).one()
        if doc_count > 0:
            return jsonify({
                "ok": False,
                "msg": "Cannot delete tag with associated patterns",
            })

        session.delete(tag)
        session.commit()
        return jsonify({"ok": True, "msg": f"Deleted tag {name}"})
