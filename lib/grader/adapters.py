"""Concrete judge adapter — an external tool-using agent as the LLM.

Same subprocess contract as memory's `ExternalAgentLLM` (prompt on stdin,
completion on stdout), with grader-specific selection:
`settings.grader.external_agent` names a key in
`topic_proposal_external_agents`; unset → the first configured agent. The
external agent (typically `claude --print`) has its own tools, which is
what makes the deep tier *agentic*: the judge can re-read files or
re-fetch URLs to verify, rather than trusting the artifact.

Unconfigured → `resolve_judge()` returns None and the grader degrades to
its mechanical screen tier.
"""

from __future__ import annotations

import subprocess

from lib.activity_log import get_activity_logger
from lib.settings import settings

log = get_activity_logger("grader")


class ExternalAgentJudge:
    """One-shot completions against a configured external agent."""

    def __init__(self, agent_id: str | None = None,
                 extra_args: list[str] | None = None):
        self._agent_id = agent_id
        self._extra_args = list(extra_args or [])

    def _select(self):
        agents = settings.topic_proposal_external_agents
        if not agents:
            return None, None
        wanted = self._agent_id or settings.grader.external_agent
        if wanted and wanted in agents:
            return wanted, agents[wanted]
        key = next(iter(agents))
        return key, agents[key]

    @property
    def judge_id(self) -> str | None:
        key, _ = self._select()
        return key

    @property
    def selected_agent(self):
        """The resolved agent config object (or None when unconfigured)."""
        _, agent = self._select()
        return agent

    def _invocation(self, agent, prompt: str) -> tuple[list[str], bytes | None]:
        """Build (argv, stdin). When `args` carries a literal ``{prompt}``
        token, substitute the prompt there and send no stdin (Kimi's `-p`);
        otherwise append nothing and pipe the prompt on stdin (Claude/Codex)."""
        raw = [*agent.args, *self._extra_args]
        if any("{prompt}" in arg for arg in raw):
            argv = [agent.command] + [arg.replace("{prompt}", prompt)
                                      for arg in raw]
            return argv, None
        return [agent.command, *raw], prompt.encode("utf-8")

    def complete(self, prompt: str, *, max_tokens: int = 1024) -> str | None:
        key, agent = self._select()
        if agent is None:
            return None
        argv, stdin = self._invocation(agent, prompt)
        try:
            proc = subprocess.run(
                argv,
                input=stdin,
                capture_output=True,
                timeout=agent.timeout_seconds,
                cwd=str(agent.cwd) if agent.cwd else None,
            )
        except (OSError, subprocess.SubprocessError):
            log.error("judge_call_failed", agent=key, exc_info=True)
            return None
        if proc.returncode != 0:
            log.error("judge_nonzero_exit", agent=key,
                      returncode=proc.returncode)
            return None
        return proc.stdout.decode("utf-8", errors="replace")[:max_tokens * 8]


def resolve_judge(agent_id: str | None = None) -> ExternalAgentJudge | None:
    """The configured judge, or None when no external agent exists. Granted
    the read-only trace commands so it can self-fetch its evidence.

    `agent_id` overrides `settings.grader.external_agent` for one run (the UI's
    per-grade provider picker). The `--allowedTools` grant is suppressed for an
    agent that declares `supports_allowed_tools=False` (Kimi has no such flag);
    such agents must auto-approve the read-only trace commands themselves.
    """
    selected = ExternalAgentJudge(agent_id=agent_id).selected_agent
    if selected is None:
        return None
    tools = settings.grader.judge_allowed_tools
    grant = tools and getattr(selected, "supports_allowed_tools", True)
    extra = ["--allowedTools", ",".join(tools)] if grant else []
    return ExternalAgentJudge(agent_id=agent_id, extra_args=extra)


__all__ = ["ExternalAgentJudge", "resolve_judge"]
