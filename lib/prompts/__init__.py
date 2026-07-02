"""Dynamic prompt templates: a composable model for the system/goal prompts
regin pipes to external agents.

Three layers:

- ``engine``   — pure ``{{variable}}`` / ``{{include:slug}}`` substitution over a
  template body (no DB, fully unit-testable).
- ``registry`` — every prompt *surface* regin builds (drafting, review, triage,
  memory, grader …) paired with its built-in ``default_body`` and the variables
  that body interpolates.
- ``resolve``  — ``render_surface(id, context)``: the DB-backed, fallback-safe
  entry point call sites use. A user-edited skeleton that renders cleanly wins;
  anything invalid degrades to the built-in default so a run never breaks.
"""

from __future__ import annotations

from lib.prompts.engine import (
    IncludeCycle,
    MissingInclude,
    PromptRenderError,
    UnknownVariable,
    includes_in,
    render,
    variables_in,
)
from lib.prompts.registry import (
    PromptSurface,
    PromptVariable,
    get_surface,
    list_surfaces,
    register_surface,
)
from lib.prompts.resolve import render_surface

__all__ = [
    "IncludeCycle",
    "MissingInclude",
    "PromptRenderError",
    "PromptSurface",
    "PromptVariable",
    "UnknownVariable",
    "get_surface",
    "includes_in",
    "list_surfaces",
    "register_surface",
    "render",
    "render_surface",
    "variables_in",
]
