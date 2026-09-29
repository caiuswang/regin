"""`reflect()` — the consolidation cycle (mnemopi's `sleep`).

Four stages, in order:

  1. **Mechanical pre-pass** — no model. Near-identical memories collapse
     into one row (the keeper survives: episodic preferred, then
     most-recalled, then oldest; similarity is embedding cosine when an
     `EmbeddingProvider` is injected, else a deterministic text ratio),
     the pending engagement sweep is scored, and any legacy `kind='digest'`
     rows are retired (the digest stage was removed).
  2. **Dream** — the ONE agentic LLM stage per run. A single
     `llm.complete` call receives a bounded evidence pack — every pending
     working row with its top co-retrieval neighbours, plus up to
     `contradiction_budget` suspect episodic pairs (same scope, sharing a
     concrete repo file path, not yet presented) — and returns one JSON
     plan: promote/hold/drop/merge per working row,
     contradict/obsolete/distinct per pair, and optional synthesize
     actions. The plan is applied deterministically with per-action
     validation; anything invalid is skipped and counted. No LLM,
     `dream_enabled` off, or an unparseable plan → every surviving working
     row is blind-promoted (a model outage never blocks consolidation).
  3. **Lifecycle decay** — forget never-recalled aged rows, decay
     chronically-ignored ones, flag stale file references.
  4. **Embed + edges** — active rows of both tiers get vectors
     (content-hash-skipped when unchanged) and the `related` edge graph is
     rebuilt, after the dream so fresh promotions/syntheses are covered.

`dry_run=True` builds the pack and calls the LLM, but applies nothing —
the would-be plan lands in `result.actions`.
"""

from __future__ import annotations

from lib.activity_log import get_activity_logger
from lib.settings import settings
from lib.memory.reflect_ops import (
    ReflectResult,
    _dedup_merge,
    _doc_text,
    _embed_episodic,
    _forget_stale,
    _harvest_edges,
    _retire_legacy_digests,
    _stale_embedding_todo,
    _text_similarity,
)
from lib.memory.reflect_decay import (
    _decay_chronically_ignored,
    _flag_stale_references,
    _score_pending,
    _validation_action_counts,
)
from lib.memory.reflect_dream import _dream

log = get_activity_logger("memory")


def reflect(store, embedder=None, llm=None, *,
            dry_run: bool = False) -> ReflectResult:
    """One consolidation pass: mechanical pre-pass → one agentic dream →
    lifecycle decay → embed/edges. Idempotent: a second run over an already-
    consolidated store finds nothing to do."""
    result = ReflectResult(dry_run=dry_run)
    working = store.list_memories(tier="working", status="active",
                                  include_tests=True, limit=10_000)
    # Legacy digests are episodic by storage but sit outside the learning
    # lifecycle; they're excluded from every stage (and retired below).
    episodic = [m for m in store.list_memories(tier="episodic", status="active",
                                               include_tests=True, limit=10_000)
                if m["kind"] != "digest"]
    result.examined = len(working)
    _retire_legacy_digests(store, dry_run=dry_run, result=result)
    consumed: set[str] = set()
    if working:
        consumed = _dedup_merge(store, working, working + episodic, embedder,
                                dry_run=dry_run, result=result)
    _score_pending(store, dry_run=dry_run)
    retired = _dream(store, working, consumed, episodic, embedder, llm,
                     dry_run=dry_run, result=result)
    episodic = [m for m in episodic if m["id"] not in retired
                and m["id"] not in consumed]
    _forget_stale(store, episodic, dry_run=dry_run, result=result)
    _decay_chronically_ignored(store, episodic, dry_run=dry_run, result=result)
    _flag_stale_references(store, working + episodic,
                           dry_run=dry_run, result=result)
    _embed_episodic(store, embedder, dry_run=dry_run, result=result)
    # After embed so freshly written synthesis/promotion rows are linkable.
    _harvest_edges(store, embedder, dry_run=dry_run, result=result)
    log.write("memory_reflected", examined=result.examined,
              merged=result.merged, contradictions=result.contradictions,
              obsoleted=result.obsoleted, pairs_checked=result.pairs_checked,
              dream_skipped=result.dream_skipped,
              promoted=result.promoted,
              held=result.held, dropped=result.dropped,
              embedded=result.embedded, forgotten=result.forgotten,
              decayed=result.decayed, synthesized=result.synthesized,
              edges=result.edges, topics=result.topics,
              flagged_stale=result.flagged_stale, ref_renames=result.ref_renames,
              dry_run=dry_run)
    if not dry_run:
        # Consolidation retires, merges and re-files memories, so the exported
        # tree an agent navigates is stale the moment this returns.
        from lib.memory.tree_io import export_tree_if_stale
        from lib.settings import settings
        export_tree_if_stale(str(settings.main_worktree))
    return result


__all__ = ["reflect", "ReflectResult", "stale_embedding_todo"]

# Public alias so callers (e.g. store.py) can import the staleness helper
# without duplicating the logic.  The private name stays for internal calls.
stale_embedding_todo = _stale_embedding_todo
