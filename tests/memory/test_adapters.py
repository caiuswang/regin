"""ExternalAgentLLM.complete's cwd resolution.

A proposal reviewer must inspect the *target* repo, not wherever the host
process happens to be running from. `complete(..., cwd=repo_path)` is the
caller-supplied fallback used only when the agent config has no explicit
`cwd` override, which always wins.
"""

from __future__ import annotations

from pathlib import Path

from lib.memory.adapters import ExternalAgentLLM
from lib.settings import TopicProposalExternalAgent, settings


def test_complete_falls_back_to_caller_cwd(tmp_path, monkeypatch, allow_subprocess_spawn):
    monkeypatch.setattr(settings, "topic_proposal_external_agents", {
        "claude": TopicProposalExternalAgent(command="pwd"),
    })
    out = ExternalAgentLLM().complete("prompt", cwd=tmp_path)
    assert out.strip() == str(tmp_path.resolve())


def test_complete_config_cwd_overrides_caller_cwd(tmp_path, monkeypatch, allow_subprocess_spawn):
    configured = tmp_path / "configured"
    configured.mkdir()
    other = tmp_path / "other"
    other.mkdir()
    monkeypatch.setattr(settings, "topic_proposal_external_agents", {
        "claude": TopicProposalExternalAgent(command="pwd", cwd=configured),
    })
    out = ExternalAgentLLM().complete("prompt", cwd=other)
    assert out.strip() == str(configured.resolve())


def test_complete_expands_tilde_in_configured_cwd(tmp_path, monkeypatch, allow_subprocess_spawn):
    """`TopicProposalExternalAgent.cwd` is a pydantic `Path`, which does not
    expand `~` on its own — subprocess.run would otherwise receive the
    literal string and fail to find the directory."""
    home = tmp_path / "home"
    (home / "configured").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(settings, "topic_proposal_external_agents", {
        "claude": TopicProposalExternalAgent(
            command="pwd", cwd=Path("~/configured"),
        ),
    })
    out = ExternalAgentLLM().complete("prompt")
    assert out.strip() == str((home / "configured").resolve())


def test_complete_with_no_cwd_or_config_inherits_none(monkeypatch, allow_subprocess_spawn):
    """No caller cwd, no config override → cwd=None (unchanged behavior for
    resolve_distiller / resolve_topic_classifier, which never pass cwd)."""
    monkeypatch.setattr(settings, "topic_proposal_external_agents", {
        "claude": TopicProposalExternalAgent(command="pwd"),
    })
    out = ExternalAgentLLM().complete("prompt")
    assert out.strip()  # ran with whatever the test process's cwd is
