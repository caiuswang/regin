"""Agent *bindings* for prompt surfaces — which external agent a goal-prompt
dispatches to.

A surface (an editable ``skeleton`` row) may be *bound* to one of the agents in
``settings.topic_proposal_external_agents``. The binding lives on the row's
``agent`` column; this module resolves it against the live registry and provides
the small read-only registry views the API/UI render.

The golden rule (learned the hard way — a NULL discriminator that silently drops
its own rows): a binding is only ever an **override**. Unbound, or bound to an
agent no longer configured, resolves to ``None`` so each dispatch keeps its own
existing default agent — behavior is byte-identical to before any binding.
"""

from __future__ import annotations

from typing import Any

from lib.settings import settings


def configured_agents() -> list[dict[str, Any]]:
    """The configured external agents as ``{id, command}``, sorted by id — the
    read-only registry the binding UI offers as options."""
    agents = settings.topic_proposal_external_agents
    return [
        {"id": agent_id, "command": config.command}
        for agent_id, config in sorted(agents.items())
    ]


def is_configured_agent(agent_id: str | None) -> bool:
    """True when ``agent_id`` names a currently-configured external agent."""
    return bool(agent_id) and agent_id in settings.topic_proposal_external_agents


def default_agent_id() -> str | None:
    """The topic-proposal default agent (claude → codex → first configured), for
    UI display only. Individual dispatch paths keep their own defaults; this is
    never imposed on them."""
    agents = settings.topic_proposal_external_agents
    if "claude" in agents:
        return "claude"
    if "codex" in agents:
        return "codex"
    return next(iter(agents), None)


def surface_agent(surface_id: str | None) -> str | None:
    """The agent bound to ``surface_id``'s skeleton row, but only when it still
    names a configured agent; else ``None`` (the caller falls back to its own
    default). Never raises — a missing row / table degrades to ``None``."""
    if not surface_id:
        return None
    from lib.prompt_templates import surface_agent_binding

    bound = surface_agent_binding(surface_id)
    return bound if is_configured_agent(bound) else None


# The grader runs one judge agent per deep-grade session, not one per aspect, so
# every grader surface resolves to the same judge: a binding on *any* one wins.
# grader-correctness/-process are the standalone judges' (dead) surfaces, kept
# for back-compat with any existing binding; grader-combined-* are what
# combined_agentic.py's build_combined_prompt actually renders for a live deep
# grading run — omitting them would make a binding on those surfaces a silent
# no-op (see lib/prompts/surfaces/grader.py's module docstring).
GRADER_SURFACE_IDS = (
    "grader-correctness", "grader-process",
    "grader-combined-role", "grader-combined-correctness", "grader-combined-process",
)


def grader_bound_agent() -> str | None:
    """The agent bound to either grader surface (first wins), or ``None``."""
    for surface_id in GRADER_SURFACE_IDS:
        bound = surface_agent(surface_id)
        if bound:
            return bound
    return None


__all__ = [
    "GRADER_SURFACE_IDS",
    "configured_agents",
    "default_agent_id",
    "grader_bound_agent",
    "is_configured_agent",
    "surface_agent",
]
