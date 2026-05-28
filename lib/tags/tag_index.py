"""Generate the tag cross-reference index file."""

from __future__ import annotations

import os
from datetime import datetime, timezone

from sqlalchemy import func
from sqlmodel import select

from lib import settings as _settings_mod
from lib.orm import SessionLocal
from lib.orm.models import DocTag, PatternDeployment, PatternDoc, Repo, Tag


def generate_tag_index() -> str:
    """Generate patterns/_index/tag-index.md from SQLite data."""
    with SessionLocal() as session:
        categories = session.exec(
            select(Tag.category).distinct().order_by(Tag.category)
        ).all()

        sections: list[str] = []
        for cat in categories:
            tag_stmt = (
                select(Tag.name, func.count(DocTag.doc_id).label("doc_count"))
                .outerjoin(DocTag, DocTag.tag_id == Tag.id)
                .where(Tag.category == cat)
                .group_by(Tag.id)
                .having(func.count(DocTag.doc_id) > 0)
                .order_by(func.count(DocTag.doc_id).desc(), Tag.name)
            )
            tag_rows = session.exec(tag_stmt).all()
            if not tag_rows:
                continue

            lines = [f"## {cat.title()}\n"]
            for tag_name, doc_count in tag_rows:
                docs_stmt = (
                    select(PatternDoc.title, PatternDoc.file_path)
                    .join(DocTag, DocTag.doc_id == PatternDoc.id)
                    .join(Tag, Tag.id == DocTag.tag_id)
                    .where(Tag.name == tag_name)
                    .order_by(PatternDoc.title)
                )
                docs = session.exec(docs_stmt).all()

                lines.append(f"### {tag_name} ({doc_count} patterns)\n")
                for title, file_path in docs:
                    lines.append(f"- [{title}](../{file_path})")
                lines.append("")
            sections.append("\n".join(lines))

    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
    content = f"""---
title: Tag Cross-Reference Index
generated: "{now}"
---

# Tag Cross-Reference Index

{''.join(sections)}"""

    index_path = os.path.join(str(_settings_mod.settings.patterns_dir), "_index", "tag-index.md")
    os.makedirs(os.path.dirname(index_path), exist_ok=True)
    with open(index_path, "w") as f:
        f.write(content)
    return index_path


def generate_repo_index() -> str:
    """Generate patterns/_index/repo-index.md grouped by the repo each
    pattern is deployed to (project-scope pattern_deployments)."""
    with SessionLocal() as session:
        repos = session.exec(
            select(Repo.id, Repo.name)
            .join(PatternDeployment,
                  (PatternDeployment.project_id == Repo.id)
                  & (PatternDeployment.scope == "project"))
            .distinct()
            .order_by(Repo.name)
        ).all()

        sections: list[str] = []
        for repo_id, repo_name in repos:
            doc_stmt = (
                select(PatternDoc.title, PatternDoc.file_path, PatternDoc.category)
                .join(PatternDeployment,
                      PatternDeployment.pattern_slug == PatternDoc.slug)
                .where(PatternDeployment.scope == "project")
                .where(PatternDeployment.project_id == repo_id)
                .order_by(PatternDoc.category, PatternDoc.title)
            )
            docs = session.exec(doc_stmt).all()

            lines = [f"## {repo_name} ({len(docs)} patterns)\n"]
            current_cat: str | None = None
            for title, file_path, category in docs:
                if category != current_cat:
                    current_cat = category
                    lines.append(f"### {current_cat}\n")
                lines.append(f"- [{title}](../{file_path})")
            lines.append("")
            sections.append("\n".join(lines))

    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
    content = f"""---
title: Repository Index
generated: "{now}"
---

# Repository Index

{''.join(sections)}"""

    index_path = os.path.join(str(_settings_mod.settings.patterns_dir), "_index", "repo-index.md")
    os.makedirs(os.path.dirname(index_path), exist_ok=True)
    with open(index_path, "w") as f:
        f.write(content)
    return index_path
