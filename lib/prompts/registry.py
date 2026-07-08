"""Registry of prompt *surfaces* — every external-agent prompt regin builds.

A surface pairs a stable ``id`` (also the ``prompt_templates`` slug) with the
built-in ``default_body`` — the literal that used to be an f-string at the call
site, now carrying ``{{variable}}`` placeholders — and the ``variables`` that
body interpolates. Seeding turns each surface into a ``builtin`` skeleton row;
the call site renders it via ``render_surface`` and falls back to
``default_body`` when the stored row is edited into something invalid.

``default_body`` may be a plain string or a zero-arg callable (lazy) so a
surface whose default lives as a module constant elsewhere (the grader judges,
the memory prompts) can register without an import cycle.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Union

DefaultBody = Union[str, Callable[[], str]]


@dataclass(frozen=True)
class PromptVariable:
    """One ``{{name}}`` a surface's body interpolates — drives the UI palette."""

    name: str
    description: str = ""
    example: str = ""
    required: bool = True


@dataclass(frozen=True)
class PromptSurface:
    """An editable prompt regin pipes to an external agent."""

    id: str
    label: str
    area: str
    _default_body: DefaultBody
    description: str = ""
    kind: str = "skeleton"
    variables: tuple[PromptVariable, ...] = ()
    applies_to: tuple[str, ...] = ()

    def default_body(self) -> str:
        body = self._default_body
        return body() if callable(body) else body

    def variable_names(self) -> list[str]:
        return [v.name for v in self.variables]


_SURFACES: "dict[str, PromptSurface]" = {}


def register_surface(
    surface_id: str,
    *,
    label: str,
    area: str,
    default_body: DefaultBody,
    description: str = "",
    kind: str = "skeleton",
    variables: "tuple[PromptVariable, ...] | list[PromptVariable]" = (),
    applies_to: "tuple[str, ...] | list[str]" = (),
) -> PromptSurface:
    """Register (or replace) a surface. Idempotent by ``surface_id`` so a module
    re-import doesn't stack duplicates."""
    surface = PromptSurface(
        id=surface_id,
        label=label,
        area=area,
        _default_body=default_body,
        description=description,
        kind=kind,
        variables=tuple(variables),
        applies_to=tuple(applies_to),
    )
    _SURFACES[surface_id] = surface
    return surface


# SHA-256 of *superseded* default bodies, per surface id. `render_surface`
# prefers the stored row and the seeder only inserts missing slugs, so when a
# default body changes the old seed would silently pin the old prompt forever
# on existing installs. Registering the retired body's hash lets
# `seed_builtin_skeletons` recognise an un-edited stale row (its body still
# hashes to a retired default) and heal it to the current default; a
# user-edited body never matches and is left alone.
_RETIRED_DEFAULT_HASHES: "dict[str, set[str]]" = {}


def register_retired_default(surface_id: str, *, sha256: str) -> None:
    _RETIRED_DEFAULT_HASHES.setdefault(surface_id, set()).add(sha256)


def retired_default_hashes(surface_id: str) -> set[str]:
    _ensure_loaded()
    return _RETIRED_DEFAULT_HASHES.get(surface_id, set())


def get_surface(surface_id: str) -> PromptSurface | None:
    _ensure_loaded()
    return _SURFACES.get(surface_id)


def list_surfaces() -> list[PromptSurface]:
    """All registered surfaces, sorted by area then label."""
    _ensure_loaded()
    return sorted(_SURFACES.values(), key=lambda s: (s.area, s.label))


_loaded = False


def _ensure_loaded() -> None:
    """Import the surface-definition modules on first access so registration is
    lazy (avoids import cycles at package import time)."""
    global _loaded
    if _loaded:
        return
    _loaded = True
    from lib.prompts import surfaces  # noqa: F401  (import side effect: registration)


__all__ = [
    "PromptSurface",
    "PromptVariable",
    "get_surface",
    "list_surfaces",
    "register_surface",
    "register_retired_default",
    "retired_default_hashes",
]
