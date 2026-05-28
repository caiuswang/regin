"""Search pattern documents by query, tag, and category."""

from __future__ import annotations

import os
from typing import Optional

from sqlalchemy import func
from sqlmodel import select

from lib.settings import settings
from lib.orm import SessionLocal
from lib.orm.models import DocTag, PatternDoc, Tag


def _pattern_to_dict(pd: PatternDoc) -> dict:
    return {
        "id": pd.id, "slug": pd.slug, "title": pd.title,
        "file_path": pd.file_path, "category": pd.category,
        "content_hash": pd.content_hash,
        "created_at": pd.created_at, "updated_at": pd.updated_at,
    }


def search_patterns(query: str, tag: Optional[str] = None,
                    category: Optional[str] = None) -> list:
    """Search patterns by text query, optional tag, and optional category.

    Returns list of dicts with: title, slug, category, file_path, tags,
    snippet. Text-search runs in Python (searches title / slug / tags /
    file content) after the tag + category filters narrow the set.
    """
    with SessionLocal() as session:
        stmt = select(PatternDoc).order_by(PatternDoc.title)
        if tag:
            stmt = stmt.join(DocTag, DocTag.doc_id == PatternDoc.id).join(
                Tag, Tag.id == DocTag.tag_id,
            ).where(Tag.name == tag)
        if category:
            stmt = stmt.where(PatternDoc.category == category)
        rows = session.exec(stmt).all()

        # Second round-trip: fetch tag names per pattern in one query.
        doc_ids = [r.id for r in rows if r.id is not None]
        tags_by_doc: dict[int, list[str]] = {}
        if doc_ids:
            tag_stmt = (
                select(DocTag.doc_id, Tag.name)
                .join(Tag, Tag.id == DocTag.tag_id)
                .where(DocTag.doc_id.in_(doc_ids))
            )
            for doc_id, tag_name in session.exec(tag_stmt).all():
                tags_by_doc.setdefault(doc_id, []).append(tag_name)

    results: list[dict] = []
    query_lower = query.lower() if query else ""
    for r in rows:
        d = _pattern_to_dict(r)
        d["tags"] = tags_by_doc.get(r.id, []) if r.id is not None else []

        if query_lower:
            searchable = " ".join([
                d.get("title") or "", d.get("slug") or "",
                " ".join(d.get("tags") or []),
            ]).lower()

            file_match = False
            abs_path = os.path.join(str(settings.patterns_dir), d["file_path"])
            if os.path.exists(abs_path):
                try:
                    with open(abs_path, "r") as f:
                        file_content = f.read()
                    if query_lower in file_content.lower():
                        file_match = True
                        d["snippet"] = _extract_snippet(file_content, query)
                except IOError:
                    pass

            if query_lower not in searchable and not file_match:
                continue

        results.append(d)

    return results


def _extract_snippet(content: str, query: str, context_chars: int = 100) -> str:
    """Extract a snippet around the first match of query in content."""
    idx = content.lower().find(query.lower())
    if idx == -1:
        return ""
    start = max(0, idx - context_chars)
    end = min(len(content), idx + len(query) + context_chars)
    snippet = content[start:end].strip()
    if start > 0:
        snippet = "..." + snippet
    if end < len(content):
        snippet = snippet + "..."
    return snippet
