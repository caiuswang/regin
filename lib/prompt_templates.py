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

from sqlalchemy.exc import OperationalError
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
        "kind": row.kind or "fragment",
        "variables": _decode_variables(row.variables),
        "applies_to": _decode_list(row.applies_to),
        "default_for_providers": _decode_list(row.default_for_providers),
        "tags": _decode_list(row.tags),
        "agent": row.agent or None,
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


def _normalize_tags(values: Iterable[str] | None) -> list[str]:
    """Validated custom session-tag slugs from a payload, order-preserving,
    deduped. Each is run through `normalize_custom_slug` so a builtin-colliding
    or bad-charset slug is dropped rather than stored — the same rule the tag
    CRUD endpoints and the prompt-marker parser use, so a surface can't declare
    a tag the Sessions list would reject."""
    from lib.trace.session_tags import normalize_custom_slug

    out: list[str] = []
    for raw in values or []:
        slug = normalize_custom_slug(raw)
        if slug and slug not in out:
            out.append(slug)
    return out


def _decode_variables(blob: str | None) -> list[dict[str, Any]]:
    if not blob:
        return []
    try:
        value = json.loads(blob)
    except json.JSONDecodeError:
        return []
    return [item for item in value if isinstance(item, dict)]


def _encode_variables(values: Iterable[dict[str, Any]] | None) -> str:
    if not values:
        return "[]"
    return json.dumps([item for item in values if isinstance(item, dict)])


def _clean_str(payload: dict[str, Any], key: str) -> str:
    return (payload.get(key) or "").strip()


def _apply_text_field(
    row: PromptTemplate, payload: dict[str, Any], key: str, *, required: bool
) -> None:
    """Apply an optional text field from ``payload`` to ``row`` in place.

    Absent key → unchanged. Present-but-empty → ``PromptTemplateError`` when
    ``required``, else stored as NULL.
    """
    if key not in payload:
        return
    value = (payload[key] or "").strip()
    if required and not value:
        raise PromptTemplateError(f"{key} cannot be empty")
    setattr(row, key, value if (value or required) else None)


def _validate_agent_binding(value: Any) -> str | None:
    """Normalize an ``agent`` binding from a PATCH payload. Empty / null clears
    the binding (→ default agent). A non-empty value must name a currently
    configured external agent, else the edit is rejected — a typo'd binding
    would otherwise silently fall back to the default and look bound in the UI."""
    agent_id = (value or "").strip() if isinstance(value, str) else ""
    if not agent_id:
        return None
    from lib.prompts.agents import is_configured_agent

    if not is_configured_agent(agent_id):
        raise PromptTemplateError(f"unknown external agent: {agent_id}")
    return agent_id


def _unique_slug(slug: str, existing: set[str]) -> str:
    if slug not in existing:
        return slug
    suffix = 2
    while f"{slug}-{suffix}" in existing:
        suffix += 1
    return f"{slug}-{suffix}"


def list_templates(kind: str | None = None) -> list[dict[str, Any]]:
    with SessionLocal() as session:
        stmt = select(PromptTemplate)
        if kind:
            stmt = stmt.where(PromptTemplate.kind == kind)
        rows = session.exec(stmt.order_by(PromptTemplate.label)).all()
        return [_row_to_dict(row) for row in rows]


def get_template_by_slug(slug: str) -> dict[str, Any] | None:
    with SessionLocal() as session:
        row = session.exec(select(PromptTemplate).where(PromptTemplate.slug == slug)).first()
        return _row_to_dict(row) if row else None


def surface_agent_binding(surface_id: str) -> str | None:
    """The external agent a skeleton row is *bound* to, or ``None`` (unset row,
    missing row, or an abnormally initialized DB with no ``prompt_templates``
    table). Never raises — a missing binding must degrade to the default agent,
    the same never-break guarantee ``render_surface`` gives a broken edit."""
    try:
        with SessionLocal() as session:
            row = session.exec(
                select(PromptTemplate.agent).where(PromptTemplate.slug == surface_id)
            ).first()
    except OperationalError:
        return None
    return (row or None) if isinstance(row, str) else None


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
    label = _clean_str(payload, "label")
    body = _clean_str(payload, "body")
    if not label:
        raise PromptTemplateError("label is required")
    if not body:
        raise PromptTemplateError("body is required")
    base_slug = slugify(_clean_str(payload, "slug") or label)
    now = _now()

    with SessionLocal() as session:
        existing = {row.slug for row in session.exec(select(PromptTemplate)).all()}
        row = PromptTemplate(
            slug=_unique_slug(base_slug, existing),
            label=label,
            description=_clean_str(payload, "description") or None,
            body=body,
            kind=_clean_str(payload, "kind") or "fragment",
            variables=_encode_variables(payload.get("variables")),
            applies_to=_encode_list(payload.get("applies_to")),
            default_for_providers=_encode_list(payload.get("default_for_providers")),
            tags=_encode_list(_normalize_tags(payload.get("tags"))),
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
        _apply_text_field(row, payload, "label", required=True)
        _apply_text_field(row, payload, "description", required=False)
        _apply_text_field(row, payload, "body", required=True)
        if "variables" in payload:
            row.variables = _encode_variables(payload["variables"])
        if "applies_to" in payload:
            row.applies_to = _encode_list(payload["applies_to"])
        if "default_for_providers" in payload:
            row.default_for_providers = _encode_list(payload["default_for_providers"])
        if "tags" in payload:
            row.tags = _encode_list(_normalize_tags(payload["tags"]))
        if "agent" in payload:
            row.agent = _validate_agent_binding(payload["agent"])
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


def _surface_variables(surface: Any) -> list[dict[str, Any]]:
    """The variable palette for a registered surface, as JSON-ready dicts."""
    return [
        {
            "name": v.name,
            "description": v.description,
            "example": v.example,
            "required": v.required,
        }
        for v in surface.variables
    ]


def _body_sha256(body: str) -> str:
    import hashlib

    return hashlib.sha256((body or "").encode("utf-8")).hexdigest()


def _heal_stale_builtin(session, row: Any, surface: Any, now: str) -> bool:
    """Overwrite a stored builtin row still carrying a RETIRED default body
    with the current default. `render_surface` prefers the stored row, so
    without this an existing install would silently pin a superseded prompt
    forever. A user-edited body never hashes to a retired default and is
    left untouched. True when the row was healed."""
    from lib.prompts.registry import retired_default_hashes

    if not row.builtin:
        return False
    if _body_sha256(row.body) not in retired_default_hashes(surface.id):
        return False
    row.body = surface.default_body()
    row.variables = _encode_variables(_surface_variables(surface))
    row.updated_at = now
    session.add(row)
    return True


def _delete_dead_builtin(session, row: Any) -> bool:
    """Delete a builtin row ONLY when its slug was explicitly retired (it
    has registered retired-default hashes) AND its body is un-edited
    (hashes to one of them — the last-shipped defaults of dead slugs live
    on as retired hashes). An unregistered slug with NO retired hashes is
    not dead — it may belong to a surface that registers late — and a
    user-edited row for a dead slug is kept: deleting it would destroy the
    only copy of the user's work."""
    from lib.prompts.registry import retired_default_hashes

    if not row.builtin:
        return False
    hashes = retired_default_hashes(row.slug)
    if not hashes:                      # never explicitly retired → not dead
        return False
    if _body_sha256(row.body) not in hashes:
        return False
    session.delete(row)
    return True


def seed_builtin_skeletons() -> int:
    """Insert a ``builtin`` ``skeleton`` row for every registered prompt surface
    that has no row yet, heal existing builtin rows whose body is still a
    *retired* default (see `_heal_stale_builtin`), and delete un-edited
    builtin rows for slugs no longer registered (see `_delete_dead_builtin`).
    Idempotent by slug — a user-edited row is left untouched — mirroring the
    ``schema.sql`` fragment seed. Returns rows added, healed, or deleted."""
    from lib.prompts.registry import list_surfaces

    now = _now()
    changed = 0
    with SessionLocal() as session:
        existing = {row.slug: row
                    for row in session.exec(select(PromptTemplate)).all()}
        registered: set[str] = set()
        for surface in list_surfaces():
            registered.add(surface.id)
            row = existing.get(surface.id)
            if row is not None:
                changed += 1 if _heal_stale_builtin(session, row, surface, now) else 0
                continue
            session.add(
                PromptTemplate(
                    slug=surface.id,
                    label=surface.label,
                    description=surface.description or None,
                    body=surface.default_body(),
                    kind=surface.kind,
                    variables=_encode_variables(_surface_variables(surface)),
                    applies_to=_encode_list(surface.applies_to),
                    default_for_providers="[]",
                    tags=_encode_list(_normalize_tags(surface.tags)),
                    builtin=1,
                    created_at=now,
                    updated_at=now,
                )
            )
            changed += 1
        for slug, row in existing.items():
            if slug not in registered:
                changed += 1 if _delete_dead_builtin(session, row) else 0
        session.commit()
    return changed


def reset_skeleton_to_default(slug: str) -> dict[str, Any]:
    """Restore a skeleton row's body + variables to its registered built-in
    default (the UI's 'reset to default' affordance)."""
    from lib.prompts.registry import get_surface

    surface = get_surface(slug)
    if surface is None:
        raise PromptTemplateError(f"no built-in default for prompt: {slug}")
    return update_template(
        slug, {"body": surface.default_body(),
               "variables": _surface_variables(surface),
               "tags": list(surface.tags)}
    )


__all__ = [
    "PromptTemplateError",
    "create_template",
    "default_template_slugs_for",
    "delete_template",
    "get_template_by_slug",
    "get_templates_by_slugs",
    "list_templates",
    "reset_skeleton_to_default",
    "seed_builtin_skeletons",
    "slugify",
    "update_template",
]
