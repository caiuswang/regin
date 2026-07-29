"""Run a `/goal-verified` agent-arm worker as a subprocess instead of a subagent.

The loop's judgment-heavy steps (1.5 refine, 3 build, 4 verify) delegate to
fresh-context workers. Claude Code supplies those through its subagent tool;
no other harness has one, which used to strand agent-arm mode on Claude while
the rest of the loop was already portable shell commands.

A fresh *process* is the isolation the arm actually needs — the subagent tool
is only the convenient way to get one. So this module spawns the configured
external agent (the same `topic_proposal_external_agents` subprocess contract
`lib/grader` and `lib/memory/distill` judges use) with the role's own prompt.

The role prompt and its tool grant are read from the **agent definition
markdown** rather than re-declared here, so the shell arm and the Claude
subagent arm cannot drift apart: both render the same file. That also keeps
the grant honest — a prompt that tells the verifier to run the gates is
granted the same `tools:` its subagent counterpart declares.
"""

from __future__ import annotations

import os
import subprocess
import uuid
from dataclasses import dataclass
from pathlib import Path

import yaml

from lib.activity_log import get_activity_logger
from lib.settings import settings

log = get_activity_logger("goal")

ROLES = ("refiner", "builder", "verifier")

# Where a role's agent definition may live, in precedence order: the
# git-tracked plugin copy first (the distributable source of truth), then the
# harness-local deployment, which is all a plugin-only install has.
_AGENT_DIRS = (
    Path("regin-plugin/plugins/regin-agents/agents"),
    Path(".claude/agents"),
)


class GoalSpawnError(RuntimeError):
    """The worker could not be defined, launched, or completed."""


@dataclass(frozen=True)
class RoleDefinition:
    """One worker role, as declared by its agent markdown."""

    role: str
    path: Path
    system_prompt: str
    tools: tuple[str, ...]


@dataclass(frozen=True)
class SpawnResult:
    """What a finished worker returned, plus how to attribute it."""

    role: str
    agent_id: str
    session_id: str
    allowed_tools: tuple[str, ...]
    stdout: str
    stderr: str


def _split_frontmatter(content: str) -> tuple[dict, str]:
    if not content.startswith("---"):
        return {}, content
    parts = content.split("---", 2)
    if len(parts) < 3:
        return {}, content
    meta = yaml.safe_load(parts[1]) or {}
    return (meta if isinstance(meta, dict) else {}), parts[2].lstrip("\n")


def _agent_file(role: str) -> Path:
    for base in _AGENT_DIRS:
        candidate = settings.project_root / base / f"goal-{role}.md"
        if candidate.is_file():
            return candidate
    searched = ", ".join(str(b / f"goal-{role}.md") for b in _AGENT_DIRS)
    raise GoalSpawnError(
        f"no agent definition for role '{role}' under {settings.project_root} "
        f"(looked in: {searched})"
    )


def role_definition(role: str) -> RoleDefinition:
    """The role's system prompt and tool grant, read from its agent markdown."""
    if role not in ROLES:
        raise GoalSpawnError(
            f"unknown role '{role}' — expected one of {', '.join(ROLES)}"
        )
    path = _agent_file(role)
    meta, body = _split_frontmatter(path.read_text(encoding="utf-8"))
    if not body.strip():
        raise GoalSpawnError(f"agent definition has an empty body: {path}")
    raw_tools = meta.get("tools") or ""
    tools = (tuple(raw_tools) if isinstance(raw_tools, list)
             else tuple(t.strip() for t in raw_tools.split(",") if t.strip()))
    return RoleDefinition(role=role, path=path, system_prompt=body.strip(),
                          tools=tools)


def compose_prompt(definition: RoleDefinition, task: str) -> str:
    """The worker's full prompt: its role charter, then the orchestrator's
    payload (goal + roadmap + recall block + diff) as one tagged section, so a
    worker that only skims still sees where its instructions end."""
    return (
        f"{definition.system_prompt}\n\n"
        f"<task>\n{task.strip()}\n</task>\n"
    )


def _select_agent(agent_id: str | None):
    agents = settings.topic_proposal_external_agents
    if not agents:
        raise GoalSpawnError(
            "no external agent configured — set "
            "`topic_proposal_external_agents` in settings before spawning a "
            "goal-verified worker"
        )
    if agent_id:
        if agent_id not in agents:
            raise GoalSpawnError(
                f"unknown external agent '{agent_id}' — configured: "
                f"{', '.join(agents)}"
            )
        return agent_id, agents[agent_id]
    key = next(iter(agents))
    return key, agents[key]


def _grant_args(agent, tools: tuple[str, ...]) -> list[str]:
    """`--allowedTools` for the role, suppressed for an agent with no such
    flag (Kimi) — those must auto-approve their tools themselves."""
    if not tools or not getattr(agent, "supports_allowed_tools", True):
        return []
    return ["--allowedTools", ",".join(tools)]


def _invocation(agent, prompt: str, extra_args: list[str]):
    """(argv, stdin). A literal ``{prompt}`` token in `args` means the CLI
    wants the prompt as an argument and no stdin (Kimi's `-p`); otherwise it
    goes on stdin (Claude/Codex). Mirrors `ExternalAgentJudge._invocation`."""
    raw = [*agent.args, *extra_args]
    if any("{prompt}" in arg for arg in raw):
        return [agent.command,
                *(arg.replace("{prompt}", prompt) for arg in raw)], None
    return [agent.command, *raw], prompt.encode("utf-8")


def _worker_env(role: str, session_id: str) -> dict[str, str]:
    """The worker gets its **own** session id, not the orchestrator's: the
    stage-scoped recall spans it leaves must be attributable to the worker
    that ran them. `regin session-id` inside the worker reads this first."""
    return {
        **os.environ,
        "REGIN_SESSION_ID": session_id,
        "REGIN_LLM_SURFACE": f"goal-{role}",
    }


def spawn_role(
    role: str,
    task: str,
    *,
    agent_id: str | None = None,
    cwd: str | Path | None = None,
    session_id: str | None = None,
    timeout: int | None = None,
) -> SpawnResult:
    """Run one goal-verified worker to completion and return its output."""
    definition = role_definition(role)
    key, agent = _select_agent(agent_id)
    prompt = compose_prompt(definition, task)
    argv, stdin = _invocation(agent, prompt, _grant_args(agent, definition.tools))
    worker_sid = session_id or f"goal-{role}-{uuid.uuid4()}"
    try:
        proc = subprocess.run(
            argv,
            input=stdin,
            capture_output=True,
            timeout=timeout or agent.timeout_seconds,
            env=_worker_env(role, worker_sid),
            cwd=(str(agent.cwd.expanduser()) if agent.cwd
                 else (str(cwd) if cwd else None)),
        )
    except (OSError, subprocess.SubprocessError) as exc:
        log.error("goal_spawn_failed", role=role, agent=key, exc_info=True)
        raise GoalSpawnError(f"{role} worker failed to run: {exc}") from exc
    stdout = proc.stdout.decode("utf-8", errors="replace")
    stderr = proc.stderr.decode("utf-8", errors="replace")
    if proc.returncode != 0:
        log.error("goal_spawn_nonzero_exit", role=role, agent=key,
                  returncode=proc.returncode)
        raise GoalSpawnError(
            f"{role} worker exited {proc.returncode}: {stderr.strip()[-500:]}"
        )
    log.write("goal_spawn_completed", role=role, agent=key,
              worker_session=worker_sid, output_chars=len(stdout))
    return SpawnResult(role=role, agent_id=key, session_id=worker_sid,
                       allowed_tools=definition.tools, stdout=stdout,
                       stderr=stderr)


__all__ = ["ROLES", "GoalSpawnError", "RoleDefinition", "SpawnResult",
           "compose_prompt", "role_definition", "spawn_role"]
