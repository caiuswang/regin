"""Default port adapters regin supplies for the memory engine.

This module is the *edge*: it is the only place under `lib/memory/` that
may import concrete providers (`lib.skills.skill_router`, subprocess
commands, …). The engine modules (`store`, `reflect`) accept ports via
constructor injection and never import this file — swapping or removing
an adapter is a zero-diff change to them.

Beyond the bare `EmbeddingProvider` port, `SkillRouterEmbedding` also
exposes optional `embed_queries` / `rerank` extensions (SkillRouter is an
asymmetric bi-encoder + cross-encoder pair; queries carry an instruction
prefix documents don't). The store discovers both via `getattr`, so a
minimal symmetric embedder still satisfies the port.
"""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

from lib.activity_log import get_activity_logger
from lib.settings import settings


@dataclass(frozen=True)
class SpawnSpec:
    """How to launch a configured external agent as a *detached* subprocess
    (the async analogue of `ExternalAgentLLM.complete`, which blocks). The
    caller Popens `argv`, pipes the prompt to stdin, and never waits on the
    result inline — the agent reports back through its own side channel."""

    argv: list[str]
    timeout: int
    cwd: str | None
    surface_id: str | None

log = get_activity_logger("memory")


class SkillRouterEmbedding:
    """EmbeddingProvider over the same SkillRouter models that power
    `pattern_router` / `skill_router`. Degrades to disabled (model_id
    None, embed → None) when torch/transformers are missing or
    `settings.agent_memory.dense_enabled` is off."""

    def _available(self) -> bool:
        if not settings.agent_memory.dense_enabled:
            return False
        from lib.skills import skill_router
        try:
            skill_router.ensure_deps()
        except skill_router.DependencyError:
            return False
        return True

    @property
    def model_id(self) -> "str | None":
        from lib.skills import skill_router
        return skill_router.EMBEDDING_MODEL_ID if self._available() else None

    def embed(self, texts: list[str]) -> "list[list[float]] | None":
        if not texts or not self._available():
            return None
        from lib.skills import skill_router
        return skill_router.embed(texts).tolist()

    def embed_queries(self, texts: list[str]) -> "list[list[float]] | None":
        if not texts or not self._available():
            return None
        from lib.skills import skill_router
        formatted = [skill_router.format_query(t) for t in texts]
        return skill_router.embed(formatted).tolist()

    def rerank(self, query: str, candidates: list[dict]) -> "list[float] | None":
        """Cross-encoder scores (raw logit differences) for candidates
        shaped {name, description, body}, or None when unavailable."""
        if not candidates or not self._available():
            return None
        from lib.skills import skill_router
        return skill_router.rerank(query, candidates)


class ExternalAgentLLM:
    """LLMProvider over a configured external command, mirroring the
    topic-proposal external-agent harness: the prompt goes to stdin,
    stdout comes back. Uses the first entry in
    `settings.topic_proposal_external_agents`; with none configured,
    `complete` returns None and callers fall back to heuristics.

    `extra_args` are appended to the agent's argv — the hook for granting
    the distiller its read-only trace tools (`--allowedTools …`) so it can
    self-fetch evidence, the same mechanism the grader's judge uses."""

    def __init__(self, *, extra_args: "list[str] | None" = None,
                 surface_id: "str | None" = None):
        self._extra_args = list(extra_args or [])
        self._surface_id = surface_id

    def _agent(self, surface_id: "str | None" = None):
        agents = settings.topic_proposal_external_agents
        if not agents:
            return None
        from lib.prompts import surface_agent
        bound = surface_agent(surface_id or self._surface_id)
        if bound and bound in agents:
            return agents[bound]
        return next(iter(agents.values()))

    def complete(
        self, prompt: str, *, max_tokens: int = 1024,
        cwd: "str | Path | None" = None,
        surface_id: "str | None" = None,
    ) -> "str | None":
        """`cwd` is the caller's target repo (e.g. a proposal reviewer's
        `repo_path`) — used only when the agent config has no explicit `cwd`
        override, which otherwise always wins.

        `surface_id` overrides the instance binding for this one call, so a
        shared LLM (reflect's synthesis/digest/contradiction) still honors each
        goal-prompt's own agent binding."""
        agent = self._agent(surface_id)
        if agent is None:
            return None
        # Tag the spawned agent's env with the surface so its session hooks
        # can mark the resulting trace as an LLM-stage run, not a real
        # interactive session (sessions.origin='llm-stage').
        resolved = surface_id or self._surface_id
        env = ({**os.environ, "REGIN_LLM_SURFACE": resolved}
               if resolved else None)
        try:
            proc = subprocess.run(
                [agent.command, *agent.args, *self._extra_args],
                input=prompt.encode("utf-8"),
                capture_output=True,
                timeout=agent.timeout_seconds,
                env=env,
                cwd=(
                    str(agent.cwd.expanduser()) if agent.cwd
                    else (str(cwd) if cwd else None)
                ),
            )
        except (OSError, subprocess.SubprocessError):
            log.error("llm_adapter_failed", exc_info=True)
            return None
        if proc.returncode != 0:
            log.error("llm_adapter_nonzero_exit", returncode=proc.returncode)
            return None
        return proc.stdout.decode("utf-8", errors="replace")[: max_tokens * 8]

    def spawn_spec(self, *, surface_id: "str | None" = None) -> "SpawnSpec | None":
        """The launch spec for running this agent detached (vs. `complete`,
        which blocks and reads stdout). Returns None when no agent is
        configured, so a caller falls back to no-op just like `complete`."""
        agent = self._agent(surface_id)
        if agent is None:
            return None
        return SpawnSpec(
            argv=[agent.command, *agent.args, *self._extra_args],
            timeout=agent.timeout_seconds,
            cwd=str(agent.cwd.expanduser()) if agent.cwd else None,
            surface_id=surface_id or self._surface_id,
        )


def resolve_distiller() -> ExternalAgentLLM:
    """The distiller LLM, granted the read-only trace commands so it can
    investigate the session itself (agentic distill). Falls back to a
    plain `ExternalAgentLLM` when no tools are configured."""
    from lib.prompts.surfaces.memory import DISTILL_SURFACE_ID
    tools = settings.agent_memory.distill_allowed_tools
    extra = ["--allowedTools", ",".join(tools)] if tools else []
    return ExternalAgentLLM(extra_args=extra, surface_id=DISTILL_SURFACE_ID)


def resolve_dreamer() -> ExternalAgentLLM:
    """The reflect dream LLM, granted the read-only memory commands so it
    can pull evidence beyond the bounded pack (agentic consolidation).
    Mirrors `resolve_distiller`."""
    from lib.prompts.surfaces.memory import DREAM_SURFACE_ID
    tools = settings.agent_memory.dream_allowed_tools
    extra = ["--allowedTools", ",".join(tools)] if tools else []
    return ExternalAgentLLM(extra_args=extra, surface_id=DREAM_SURFACE_ID)


def resolve_topic_classifier() -> ExternalAgentLLM:
    """The LLM behind agentic `memory link-topics`: plain text in, JSON out —
    it reasons over the taxonomy and the memory body, granting no tools."""
    from lib.prompts.surfaces.memory import TOPIC_CLASSIFY_SURFACE_ID
    return ExternalAgentLLM(surface_id=TOPIC_CLASSIFY_SURFACE_ID)


def resolve_retitler() -> ExternalAgentLLM:
    """The LLM behind `memory retitle`: plain text in, JSON out — it reads a
    lesson body and returns a one-line rule title, granting no tools (nothing
    to fetch)."""
    from lib.prompts.surfaces.memory import RETITLE_SURFACE_ID
    return ExternalAgentLLM(surface_id=RETITLE_SURFACE_ID)


def resolve_proposal_reviewer() -> ExternalAgentLLM:
    """The LLM behind proposal review notes: granted read-only repo tools so
    it can verify the draft against the current refs itself (agentic review),
    rather than judging a pre-baked evidence pack. Returns None-yielding
    `complete` when no external agent is configured, so the caller no-ops."""
    from lib.prompts.surfaces.review import SURFACE_ID as REVIEW_SURFACE_ID
    return ExternalAgentLLM(
        extra_args=["--allowedTools", "Read,Glob,Grep"], surface_id=REVIEW_SURFACE_ID,
    )


def resolve_drift_judge() -> ExternalAgentLLM:
    """The LLM behind the batched content-drift judge. Its prompt hands over
    evidence pointers (baseline commit, wiki path) rather than embedded
    content, so beyond the reviewer's read-only tools it needs the read-only
    git commands the prompt instructs — diff/log/show against the baseline —
    or the pointers are dead weight."""
    from lib.prompts.surfaces.triage import JUDGE_BATCH_SURFACE_ID
    return ExternalAgentLLM(
        extra_args=["--allowedTools",
                    "Read,Glob,Grep,Bash(git diff:*),Bash(git log:*),"
                    "Bash(git show:*)"],
        surface_id=JUDGE_BATCH_SURFACE_ID,
    )


__all__ = ["SkillRouterEmbedding", "ExternalAgentLLM", "SpawnSpec",
           "resolve_distiller", "resolve_dreamer", "resolve_topic_classifier",
           "resolve_retitler", "resolve_proposal_reviewer",
           "resolve_drift_judge"]
