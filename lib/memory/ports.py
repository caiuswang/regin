"""Ports — the decoupling seam of the memory engine.

The engine depends on these four interfaces and nothing else; concrete
providers (the SkillRouter embedder, an external LLM agent, a Hindsight
sink, …) are adapters constructed at the edge (hook wiring, CLI, web) and
injected. No module under `lib/memory/` may import a concrete provider or
external service — removing any adapter must be a zero-diff change to the
engine.

Every port degrades gracefully:
  * `EmbeddingProvider` disabled / returning None → FTS-only recall.
  * `LLMProvider` unconfigured / returning None → reflect + distill run
    deterministic heuristics.
  * `MemorySink` absent → no outbound export (the default).
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from lib.memory.models import Memory, MemoryHit, MemoryInput


@runtime_checkable
class EmbeddingProvider(Protocol):
    def embed(self, texts: list[str]) -> "list[list[float]] | None":
        """Encode texts into L2-normalized vectors, or None when the
        provider is disabled or its dependencies are missing."""
        ...

    @property
    def model_id(self) -> "str | None":
        """Identifier stored next to each vector; None when disabled."""
        ...


@runtime_checkable
class LLMProvider(Protocol):
    def complete(self, prompt: str, *, max_tokens: int = 1024,
                 surface_id: "str | None" = None) -> "str | None":
        """One-shot completion, or None when unconfigured / failed.

        ``surface_id`` optionally names the goal-prompt surface this call serves
        so the provider can route to that surface's *bound* agent; ``None`` uses
        the provider's default agent (backward-compatible)."""
        ...


@runtime_checkable
class MemoryStore(Protocol):
    def remember(self, mem: MemoryInput) -> str: ...

    def recall(self, query: str, *, top_k: int = 5,
               scope: "str | None" = None) -> list[MemoryHit]: ...

    def get(self, memory_id: str) -> "Memory | None": ...

    def update(self, memory_id: str, **fields) -> bool: ...

    def supersede(self, old_id: str, new: MemoryInput) -> str: ...

    def restore(self, memory_id: str) -> bool: ...

    def forget(self, memory_id: str) -> bool: ...


@runtime_checkable
class MemorySink(Protocol):
    """Optional outbound export (e.g. a Hindsight adapter). The engine
    never constructs one; the default is no sink at all."""

    def export(self, mem: Memory) -> None: ...


__all__ = [
    "EmbeddingProvider", "LLMProvider", "MemoryStore", "MemorySink",
]
