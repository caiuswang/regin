"""ExternalAgentLLM.complete's cwd resolution and spawn environment.

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


# ── spawn environment ───────────────────────────────────────────


def _spawn_env(monkeypatch, surface_id=None) -> dict:
    """Capture the environment `complete` hands its child process."""
    from lib.memory import adapters
    seen = {}

    class _Fake:
        def run(self, argv, **kwargs):
            del argv
            seen.update(kwargs.get("env") or {})
            return type("P", (), {"returncode": 0, "stdout": b""})()

    monkeypatch.setattr(adapters, "subprocess", _Fake())
    monkeypatch.setattr(settings, "topic_proposal_external_agents", {
        "claude": TopicProposalExternalAgent(command="pwd"),
    })
    ExternalAgentLLM(surface_id=surface_id).complete("prompt")
    return seen


def test_the_spawned_agent_never_inherits_the_servers_bridge_optin(monkeypatch):
    """`regin serve` runs in a tmux pane with REGIN_BRIDGE=1; a child that
    inherited it would register the *server's* pane as its own bridge pane."""
    monkeypatch.setenv("REGIN_BRIDGE", "1")
    monkeypatch.setenv("REGIN_SPAWN_ENV_CANARY", "kept")

    env = _spawn_env(monkeypatch, surface_id="memory-distill")

    assert env["REGIN_BRIDGE"] == "0"
    assert env["REGIN_LLM_SURFACE"] == "memory-distill"
    assert env["REGIN_SPAWN_ENV_CANARY"] == "kept"


def test_an_unsurfaced_agent_also_gets_the_bridge_off(monkeypatch):
    """Without a surface id this path used to pass `env=None` (pure inherit),
    which carried the server's REGIN_BRIDGE straight through."""
    monkeypatch.setenv("REGIN_BRIDGE", "1")
    monkeypatch.delenv("REGIN_LLM_SURFACE", raising=False)

    env = _spawn_env(monkeypatch)

    assert env["REGIN_BRIDGE"] == "0"
    assert "REGIN_LLM_SURFACE" not in env
