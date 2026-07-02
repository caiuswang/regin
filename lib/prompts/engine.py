"""Variable substitution + fragment composition for prompt templates.

Renders a template *body* against a runtime ``context`` dict:

- ``{{name}}``          → ``context["name"]`` (string-coerced).
- ``{{include:slug}}``  → the body of another template, loaded via
  ``include_loader(slug)`` and rendered recursively (cycle-guarded).
- single braces ``{`` / ``}`` and any ``{single}`` token pass through
  **verbatim** — only the double-brace ``{{ … }}`` form is a placeholder — so a
  body may hold literal JSON (``{"id": 1}``) or single-brace tokens (the
  grader's ``{trace_id}``) without escaping.

Every failure raises a ``PromptRenderError`` subclass. Call sites that need the
never-break-a-run guarantee go through ``lib.prompts.resolve.render_surface``,
which catches these and falls back to the built-in default.
"""

from __future__ import annotations

import re
from typing import Callable, Mapping, Optional

# Matches ``{{ token }}`` where token is a variable name or ``include:slug``.
_PLACEHOLDER_RE = re.compile(r"\{\{\s*([A-Za-z0-9_.:\-]+)\s*\}\}")
_INCLUDE_PREFIX = "include:"
_MAX_DEPTH = 6

IncludeLoader = Callable[[str], Optional[str]]


class PromptRenderError(Exception):
    """Base for all template render failures."""


class UnknownVariable(PromptRenderError):
    def __init__(self, name: str) -> None:
        super().__init__(f"unknown template variable: {{{{{name}}}}}")
        self.name = name


class MissingInclude(PromptRenderError):
    def __init__(self, slug: str) -> None:
        super().__init__(f"missing include template: {slug}")
        self.slug = slug


class IncludeCycle(PromptRenderError):
    def __init__(self, slug: str) -> None:
        super().__init__(f"include cycle through template: {slug}")
        self.slug = slug


def render(
    template: str,
    context: Mapping[str, object] | None = None,
    *,
    include_loader: IncludeLoader | None = None,
) -> str:
    """Substitute ``{{ … }}`` placeholders in ``template``. See module docstring."""
    return _render(template or "", context or {}, include_loader, depth=0, seen=())


def _render(
    template: str,
    context: Mapping[str, object],
    include_loader: IncludeLoader | None,
    *,
    depth: int,
    seen: tuple[str, ...],
) -> str:
    if depth > _MAX_DEPTH:
        raise IncludeCycle(seen[-1] if seen else "?")

    def _sub(match: "re.Match[str]") -> str:
        return _resolve(match.group(1), context, include_loader, depth, seen)

    return _PLACEHOLDER_RE.sub(_sub, template)


def _resolve(
    token: str,
    context: Mapping[str, object],
    include_loader: IncludeLoader | None,
    depth: int,
    seen: tuple[str, ...],
) -> str:
    if token.startswith(_INCLUDE_PREFIX):
        return _resolve_include(
            token[len(_INCLUDE_PREFIX):], context, include_loader, depth, seen
        )
    if token in context:
        return str(context[token])
    raise UnknownVariable(token)


def _resolve_include(
    slug: str,
    context: Mapping[str, object],
    include_loader: IncludeLoader | None,
    depth: int,
    seen: tuple[str, ...],
) -> str:
    if slug in seen:
        raise IncludeCycle(slug)
    if include_loader is None:
        raise MissingInclude(slug)
    body = include_loader(slug)
    if body is None:
        raise MissingInclude(slug)
    return _render(body, context, include_loader, depth=depth + 1, seen=seen + (slug,))


def _tokens(template: str) -> list[str]:
    return [m.group(1) for m in _PLACEHOLDER_RE.finditer(template or "")]


def variables_in(template: str) -> list[str]:
    """Distinct non-include variable names referenced by ``template`` (in order)."""
    out: list[str] = []
    for token in _tokens(template):
        if not token.startswith(_INCLUDE_PREFIX) and token not in out:
            out.append(token)
    return out


def includes_in(template: str) -> list[str]:
    """Distinct include slugs referenced by ``template`` (in order)."""
    out: list[str] = []
    for token in _tokens(template):
        if token.startswith(_INCLUDE_PREFIX):
            slug = token[len(_INCLUDE_PREFIX):]
            if slug not in out:
                out.append(slug)
    return out


__all__ = [
    "IncludeCycle",
    "MissingInclude",
    "PromptRenderError",
    "UnknownVariable",
    "includes_in",
    "render",
    "variables_in",
]
