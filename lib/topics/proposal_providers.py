"""Proposal provider discovery.

The only proposal provider is the external tool-using agent (configured
in `settings.topic_proposal_external_agents`); it explores the repo with
its own Read/Glob/Grep tools. The runner lives in
`lib/topics/proposal_external.py`.
"""

from __future__ import annotations

from typing import Any

from lib.settings import settings


def list_proposal_providers() -> list[dict[str, Any]]:
    """Available proposal providers — external agents only."""
    agents = sorted(settings.topic_proposal_external_agents.keys())
    return [
        {
            "id": "external-agent",
            "label": "External Agent",
            "network": False,
            "configured": bool(agents),
            "description": "Tool-using proposal provider backed by a configured agent command.",
            "agents": agents,
            "default_agent": (
                "claude"
                if "claude" in settings.topic_proposal_external_agents
                else "codex"
                if "codex" in settings.topic_proposal_external_agents
                else next(iter(settings.topic_proposal_external_agents), None)
            ),
        },
    ]
