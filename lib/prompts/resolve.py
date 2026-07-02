"""``render_surface`` — the DB-backed, fallback-safe entry point call sites use.

Resolution order for a surface id:

1. the stored ``prompt_templates`` row body (a user edit, or the seeded default);
2. rendered with ``{{var}}`` from ``context`` and ``{{include:slug}}`` expanded
   from other rows;
3. on **any** render error, the built-in ``default_body`` from the registry,
   logged as a warning — so a broken user edit degrades instead of crashing a run.

Only if the built-in default itself fails to render (a regin bug, not a user
edit) does the error propagate.
"""

from __future__ import annotations

from typing import Mapping

from sqlalchemy.exc import OperationalError

from lib.activity_log import get_activity_logger
from lib.prompts.engine import PromptRenderError, render
from lib.prompts.registry import get_surface

log = get_activity_logger("prompts")


def _stored_body(slug: str) -> str | None:
    """The stored template body for ``slug`` (None if no row / empty body).

    Imported lazily to keep ``lib.prompts`` importable before the ORM is set up.
    A missing/unreadable ``prompt_templates`` table (an abnormally initialized
    DB) degrades to the built-in default rather than crashing the run — the
    same never-break guarantee we give a broken user edit.
    """
    from lib.prompt_templates import get_template_by_slug

    try:
        row = get_template_by_slug(slug)
    except OperationalError:
        log.write("prompt_store_unavailable", surface=slug)
        return None
    if not row:
        return None
    body = row.get("body")
    return body if body else None


def _include_loader(slug: str) -> str | None:
    """Resolve ``{{include:slug}}`` — a stored row body, else a registered
    surface's built-in default, else None (→ MissingInclude → fallback)."""
    body = _stored_body(slug)
    if body is not None:
        return body
    surface = get_surface(slug)
    return surface.default_body() if surface else None


def render_surface(surface_id: str, context: Mapping[str, object] | None = None) -> str:
    """Render the prompt for ``surface_id`` against ``context``.

    Never raises for a bad *user edit*: falls back to the built-in default and
    logs. Raises ``KeyError`` only for an unregistered surface id.
    """
    surface = get_surface(surface_id)
    if surface is None:
        raise KeyError(f"unknown prompt surface: {surface_id}")
    ctx = context or {}
    default_body = surface.default_body()
    stored = _stored_body(surface_id)
    body = stored if stored is not None else default_body
    try:
        return render(body, ctx, include_loader=_include_loader)
    except PromptRenderError as exc:
        if stored is None:
            # The built-in default is broken — a regin bug, not a user edit.
            log.error("prompt_default_render_failed", surface=surface_id, error=str(exc), exc_info=True)
            raise
        log.write("prompt_fell_back_to_default", surface=surface_id, error=str(exc))
        return render(default_body, ctx, include_loader=_include_loader)


__all__ = ["render_surface"]
