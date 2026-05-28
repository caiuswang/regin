"""Tag management and auto-tagging rules.

All auto-tag rules are user-curated data loaded from
`$AUTO_TAG_RULES_PATH` (default
`~/.local/share/regin/config/auto_tag_rules.yaml`). The code is
language-agnostic: nothing is hardcoded about Java, Spring, or any other
stack. A public clone with no YAML gets empty dicts and auto-tagging
becomes a no-op until the user authors their own rules.

YAML schema (all sections optional):

    layers:               # category/layer name -> tags
      entity: [entity, persistence]
      ...

    repo_patterns:        # regex -> tags (applied to repo_name)
      example-service: [example]
      ...

    annotations:          # string found in content/annotations array -> tags
      '@Cacheable': [cache]
      ...

    metadata_patterns:    # generic "field in metadata contains substring" -> tags
      extends:            # (works for any field name: extends, imports,
        BaseEntity: [base-class]   #  decorators, traits, …)
      imports:
        'myproject/foo': [foo-user]

    layer_combinations:   # topics with *any* listed layer get these tags
      - any_of: [entity, repository, service-impl]
        tags: [persistence]
"""

import os
import re

import yaml
from sqlalchemy import func
from sqlmodel import select

from lib.settings import settings
from lib.orm import SessionLocal
from lib.orm.models import DocTag, Tag


def _load_rules() -> dict:
    path = str(settings.auto_tag_rules_path)
    if not os.path.isfile(path):
        return {}
    with open(path) as f:
        data = yaml.safe_load(f)
    return data or {}


_RULES = _load_rules()

# Public re-exports for back-compat with older imports.
LAYER_AUTO_TAGS: dict = _RULES.get('layers') or {}
ANNOTATION_TAGS: dict = _RULES.get('annotations') or {}
REPO_DOMAIN_TAGS: dict = _RULES.get('repo_patterns') or {}
_METADATA_PATTERNS: dict = _RULES.get('metadata_patterns') or {}
_LAYER_COMBINATIONS: list = _RULES.get('layer_combinations') or []


def _tags_from_annotations(annotations_iter) -> set:
    tags = set()
    for ann in annotations_iter or []:
        if ann in ANNOTATION_TAGS:
            tags.update(ANNOTATION_TAGS[ann])
    return tags


def _tags_from_metadata_patterns(metadata: dict) -> set:
    """For each configured metadata field, match substrings in its value."""
    tags = set()
    for field, substring_map in _METADATA_PATTERNS.items():
        value = metadata.get(field)
        if value is None:
            continue
        # Accept both strings ('extends': 'BaseEntity') and iterables
        # ('imports': ['a.b.Foo', ...]).
        haystack = value if isinstance(value, str) else ' '.join(value)
        for substring, pattern_tags in substring_map.items():
            if substring in haystack:
                tags.update(pattern_tags)
    return tags


def _tags_from_repo(repo_name: str) -> set:
    tags = set()
    for pattern, domain_tags in REPO_DOMAIN_TAGS.items():
        if re.match(pattern, repo_name):
            tags.update(domain_tags)
    return tags


def auto_tag_doc(category: str, metadata: dict, repo_name: str) -> list:
    """Determine tags for a pattern doc based on classification, content, and repo."""
    tags = set()
    if category in LAYER_AUTO_TAGS:
        tags.update(LAYER_AUTO_TAGS[category])
    tags |= _tags_from_annotations(metadata.get('annotations', []))
    tags |= _tags_from_metadata_patterns(metadata)
    tags |= _tags_from_repo(repo_name)
    return sorted(tags)


def auto_tag_domain(layers: dict, repo_name: str) -> list:
    """Determine tags for a domain doc based on its layers and content.

    Args:
        layers: {layer_name: {'path': str, 'content': str}}
        repo_name: Source repo name
    """
    tags = set()
    for layer_name in layers:
        if layer_name in LAYER_AUTO_TAGS:
            tags.update(LAYER_AUTO_TAGS[layer_name])
    for info in layers.values():
        content = info.get('content', '') if isinstance(info, dict) else ''
        for ann, ann_tags in ANNOTATION_TAGS.items():
            if ann in content:
                tags.update(ann_tags)
    tags |= _tags_from_repo(repo_name)
    layer_names = set(layers.keys())
    for combo in _LAYER_COMBINATIONS:
        required = set(combo.get('any_of') or [])
        if layer_names & required:
            tags.update(combo.get('tags') or [])
    return sorted(tags)


def ensure_tags_exist(tag_names: list) -> None:
    """Ensure all tag names exist in the database, creating as 'concept' if new."""
    with SessionLocal() as session:
        existing = {
            row.name
            for row in session.exec(
                select(Tag).where(Tag.name.in_(list(tag_names)))
            ).all()
        }
        for name in tag_names:
            if name in existing:
                continue
            session.add(Tag(
                name=name, category="concept",
                description=f"Auto-created tag: {name}",
            ))
        session.commit()


def list_tags(category: str = None) -> list:
    """List all tags with pattern counts."""
    with SessionLocal() as session:
        stmt = (
            select(
                Tag.id, Tag.name, Tag.category, Tag.description,
                func.count(DocTag.doc_id).label("doc_count"),
            )
            .outerjoin(DocTag, DocTag.tag_id == Tag.id)
            .group_by(Tag.id)
            .order_by(Tag.category, Tag.name)
        )
        if category:
            stmt = stmt.where(Tag.category == category)
        return [dict(r._mapping) for r in session.exec(stmt).all()]


def add_tag(name: str, category: str, description: str = None) -> None:
    """Add a new tag. INSERT OR IGNORE semantics via a pre-check."""
    with SessionLocal() as session:
        existing = session.exec(select(Tag).where(Tag.name == name)).first()
        if existing is not None:
            return
        session.add(Tag(name=name, category=category, description=description))
        session.commit()
