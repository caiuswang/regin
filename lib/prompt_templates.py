"""CRUD + lookup helpers for user-managed prompt templates.

Templates are reusable prompt fragments the user can inject into the
topic-proposal flow (and, later, other LLM/agent surfaces). Each
template has:

- ``slug``: stable identifier, also the URL key.
- ``applies_to``: list of provider ids the template can be combined
  with. An empty list means "all providers".
- ``default_for_providers``: subset of ``applies_to`` whose chips are
  pre-selected when the user picks that provider.

The ``builtin=1`` rows are seeded from ``db/schema.sql`` (an
``INSERT OR IGNORE`` keyed on slug, applied by both ``regin init`` and
``regin rebuild``). The body and label are editable; the row cannot be
deleted, so the ``gitnexus-usage`` template always remains discoverable.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any, Iterable

from sqlmodel import select

from lib.orm import SessionLocal
from lib.orm.models import PromptTemplate


_SLUG_RE = re.compile(r"[^a-z0-9]+")


class PromptTemplateError(Exception):
    """Raised for validation / not-found / delete-builtin failures."""


def slugify(value: str) -> str:
    cleaned = _SLUG_RE.sub("-", (value or "").strip().lower()).strip("-")
    return cleaned or "template"


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _row_to_dict(row: PromptTemplate) -> dict[str, Any]:
    return {
        "id": row.id,
        "slug": row.slug,
        "label": row.label,
        "description": row.description or "",
        "body": row.body,
        "applies_to": _decode_list(row.applies_to),
        "default_for_providers": _decode_list(row.default_for_providers),
        "builtin": bool(row.builtin),
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }


def _decode_list(blob: str | None) -> list[str]:
    if not blob:
        return []
    try:
        value = json.loads(blob)
    except json.JSONDecodeError:
        return []
    return [str(item) for item in value if isinstance(item, str)]


def _encode_list(values: Iterable[str] | None) -> str:
    if not values:
        return "[]"
    return json.dumps([str(v) for v in values])


def _unique_slug(slug: str, existing: set[str]) -> str:
    if slug not in existing:
        return slug
    suffix = 2
    while f"{slug}-{suffix}" in existing:
        suffix += 1
    return f"{slug}-{suffix}"


def list_templates() -> list[dict[str, Any]]:
    with SessionLocal() as session:
        rows = session.exec(select(PromptTemplate).order_by(PromptTemplate.label)).all()
        return [_row_to_dict(row) for row in rows]


def get_template_by_slug(slug: str) -> dict[str, Any] | None:
    with SessionLocal() as session:
        row = session.exec(select(PromptTemplate).where(PromptTemplate.slug == slug)).first()
        return _row_to_dict(row) if row else None


def get_templates_by_slugs(slugs: Iterable[str]) -> list[dict[str, Any]]:
    """Resolve a list of slugs to template dicts, preserving the input order.

    Unknown slugs are silently dropped so a deleted custom template
    doesn't break a saved proposal run. The caller can detect this by
    comparing lengths.
    """
    slugs = [s for s in slugs if s]
    if not slugs:
        return []
    with SessionLocal() as session:
        rows = session.exec(select(PromptTemplate).where(PromptTemplate.slug.in_(slugs))).all()
    by_slug = {row.slug: _row_to_dict(row) for row in rows}
    return [by_slug[s] for s in slugs if s in by_slug]


def default_template_slugs_for(provider: str) -> list[str]:
    if not provider:
        return []
    with SessionLocal() as session:
        rows = session.exec(select(PromptTemplate)).all()
    out: list[str] = []
    for row in rows:
        if provider in _decode_list(row.default_for_providers):
            out.append(row.slug)
    return out


def create_template(payload: dict[str, Any]) -> dict[str, Any]:
    label = (payload.get("label") or "").strip()
    body = (payload.get("body") or "").strip()
    if not label:
        raise PromptTemplateError("label is required")
    if not body:
        raise PromptTemplateError("body is required")
    slug_input = (payload.get("slug") or "").strip() or label
    base_slug = slugify(slug_input)
    applies_to = payload.get("applies_to") or []
    default_for = payload.get("default_for_providers") or []
    description = (payload.get("description") or "").strip() or None
    now = _now()

    with SessionLocal() as session:
        existing = {row.slug for row in session.exec(select(PromptTemplate)).all()}
        slug = _unique_slug(base_slug, existing)
        row = PromptTemplate(
            slug=slug,
            label=label,
            description=description,
            body=body,
            applies_to=_encode_list(applies_to),
            default_for_providers=_encode_list(default_for),
            builtin=0,
            created_at=now,
            updated_at=now,
        )
        session.add(row)
        session.commit()
        session.refresh(row)
        return _row_to_dict(row)


def update_template(slug: str, payload: dict[str, Any]) -> dict[str, Any]:
    now = _now()
    with SessionLocal() as session:
        row = session.exec(select(PromptTemplate).where(PromptTemplate.slug == slug)).first()
        if row is None:
            raise PromptTemplateError(f"prompt template not found: {slug}")
        if "label" in payload:
            label = (payload["label"] or "").strip()
            if not label:
                raise PromptTemplateError("label cannot be empty")
            row.label = label
        if "description" in payload:
            row.description = (payload["description"] or "").strip() or None
        if "body" in payload:
            body = (payload["body"] or "").strip()
            if not body:
                raise PromptTemplateError("body cannot be empty")
            row.body = body
        if "applies_to" in payload:
            row.applies_to = _encode_list(payload["applies_to"])
        if "default_for_providers" in payload:
            row.default_for_providers = _encode_list(payload["default_for_providers"])
        row.updated_at = now
        session.add(row)
        session.commit()
        session.refresh(row)
        return _row_to_dict(row)


def delete_template(slug: str) -> dict[str, Any]:
    with SessionLocal() as session:
        row = session.exec(select(PromptTemplate).where(PromptTemplate.slug == slug)).first()
        if row is None:
            raise PromptTemplateError(f"prompt template not found: {slug}")
        if row.builtin:
            raise PromptTemplateError(f"built-in template cannot be deleted: {slug}")
        snapshot = _row_to_dict(row)
        session.delete(row)
        session.commit()
        return snapshot


__all__ = [
    "PromptTemplateError",
    "create_template",
    "default_template_slugs_for",
    "delete_template",
    "get_template_by_slug",
    "get_templates_by_slugs",
    "list_templates",
    "slugify",
    "update_template",
]
