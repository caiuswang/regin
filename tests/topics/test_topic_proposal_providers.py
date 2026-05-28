"""Tests for external-agent proposal provider discovery."""

from __future__ import annotations

from lib.topics.proposal_external import default_external_agent_id
from lib.topics.proposal_providers import list_proposal_providers


def _agent_cfg(command, args=None):
    return type(
        "Cfg", (),
        {"command": command, "args": args or [], "timeout_seconds": 60, "cwd": None},
    )()


def test_list_proposal_providers_exposes_external_agent(monkeypatch):
    monkeypatch.setattr(
        "lib.topics.proposal_providers.settings.topic_proposal_external_agents",
        {"codex": _agent_cfg("codex", ["exec"])},
    )

    providers = list_proposal_providers()

    assert len(providers) == 1
    assert providers[0]["id"] == "external-agent"
    assert providers[0]["configured"] is True
    assert providers[0]["agents"] == ["codex"]
    assert providers[0]["default_agent"] == "codex"


def test_list_proposal_providers_unconfigured_without_agents(monkeypatch):
    monkeypatch.setattr(
        "lib.topics.proposal_providers.settings.topic_proposal_external_agents", {},
    )

    providers = list_proposal_providers()

    assert providers[0]["id"] == "external-agent"
    assert providers[0]["configured"] is False
    assert providers[0]["agents"] == []
    assert providers[0]["default_agent"] is None


def test_default_external_agent_id_prefers_claude_then_codex(monkeypatch):
    monkeypatch.setattr(
        "lib.topics.proposal_external.settings.topic_proposal_external_agents",
        {"codex": _agent_cfg("codex")},
    )
    assert default_external_agent_id() == "codex"

    monkeypatch.setattr(
        "lib.topics.proposal_external.settings.topic_proposal_external_agents",
        {"codex": _agent_cfg("codex"), "claude": _agent_cfg("claude")},
    )
    assert default_external_agent_id() == "claude"
