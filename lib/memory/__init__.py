"""Agent memory — cross-session experience for regin's agents.

Thin facade over the layered engine (capture → consolidate → recall →
reinforce). Callers use these module-level verbs; the lower layers stay
swappable behind the ports in `lib.memory.ports`:

    remember(body, kind=…, scope=…, …)     -> memory id
    recall(query, top_k=…, scope=…, mode=…) -> [MemoryHit]
    reflect(dry_run=…)                      -> ReflectResult
    get / update / forget / supersede / stats

The default store is SQLite on the separate, self-initializing memory DB
(`lib.memory.engine`) with the SkillRouter embedding adapter injected.
`mode='fts'` recalls without ever touching the models — the hook path.
Storage and lifecycle details: *Agent Memory* in `ARCHITECTURE.md`.
"""

from __future__ import annotations

from typing import Optional

from lib.memory.models import MemoryHit, MemoryInput  # noqa: F401 — re-export
from lib.memory.reflect import ReflectResult
from lib.memory.store import (  # noqa: F401 — re-export the shared orphan node
    ORPHAN_BLURB, ORPHAN_LABEL, ORPHAN_NODE_ID)
from lib.settings import settings

_store = None


def get_store():
    """Process-wide default store (SQLite + SkillRouter embedder)."""
    global _store
    if _store is None:
        from lib.memory.adapters import SkillRouterEmbedding
        from lib.memory.store import SqliteMemoryStore
        _store = SqliteMemoryStore(embedder=SkillRouterEmbedding())
    return _store


def reset_store() -> None:
    """Drop the cached store (tests that swap the DB path)."""
    global _store
    _store = None


def enabled() -> bool:
    return settings.agent_memory.enabled


def remember(body: str, *, kind: str = "lesson", title: Optional[str] = None,
             scope: str = "global", tags: Optional[list[str]] = None,
             importance: float = 0.5, veracity: str = "unknown",
             status: str = "active",
             source_trace_id: Optional[str] = None,
             source_span_id: Optional[str] = None,
             source_agent_id: Optional[str] = None,
             is_test: bool = False) -> str:
    return get_store().remember(MemoryInput(
        body=body, kind=kind, title=title, scope=scope, tags=tags or [],
        importance=importance, veracity=veracity, status=status,
        source_trace_id=source_trace_id, source_span_id=source_span_id,
        source_agent_id=source_agent_id, is_test=is_test))


def recall(query: str, *, top_k: int = 5, scope: Optional[str] = None,
           mode: str = "auto", include_tests: bool = False,
           reinforce: bool = True, min_overlap: int = 0,
           boost_topic_node_id: Optional[str] = None) -> list[MemoryHit]:
    return get_store().recall(query, top_k=top_k, scope=scope, mode=mode,
                              include_tests=include_tests,
                              reinforce=reinforce, min_overlap=min_overlap,
                              boost_topic_node_id=boost_topic_node_id)


def get(memory_id: str):
    return get_store().get(memory_id)


def update(memory_id: str, **fields) -> bool:
    return get_store().update(memory_id, **fields)


def forget(memory_id: str) -> bool:
    return get_store().forget(memory_id)


def supersede(old_id: str, new: MemoryInput) -> str:
    return get_store().supersede(old_id, new)


def restore(memory_id: str) -> bool:
    return get_store().restore(memory_id)


def reflect(*, dry_run: bool = False) -> ReflectResult:
    from lib.memory.adapters import SkillRouterEmbedding, resolve_dreamer
    from lib.memory.reflect import reflect as _reflect
    return _reflect(get_store(), embedder=SkillRouterEmbedding(),
                    llm=resolve_dreamer(), dry_run=dry_run)


def stats() -> dict:
    return get_store().stats()


def export_memory_tree(repo_path: str, *, out_dir: Optional[str] = None,
                       scope: Optional[str] = None) -> dict:
    from lib.memory.tree_io import export_memory_tree as _export_tree
    return _export_tree(repo_path, out_dir=out_dir, scope=scope)


def import_memory_tree(repo_path: str, *, in_dir: Optional[str] = None) -> dict:
    from lib.memory.tree_io import import_memory_tree as _import_tree
    return _import_tree(repo_path, in_dir=in_dir)


__all__ = [
    "MemoryHit", "MemoryInput", "ReflectResult",
    "get_store", "reset_store", "enabled",
    "remember", "recall", "get", "update", "forget", "supersede", "restore",
    "reflect", "stats", "export_memory_tree", "import_memory_tree",
]
