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

import difflib
import hashlib
import json
import math
import re
import statistics
from dataclasses import dataclass, field as dc_field
from typing import Optional

from lib.activity_log import get_activity_logger
from lib.settings import settings

log = get_activity_logger("memory")


@dataclass
class ReflectResult:
    examined: int = 0
    merged: int = 0
    contradictions: int = 0
    obsoleted: int = 0
    pairs_checked: int = 0
    dream_skipped: int = 0
    promoted: int = 0
    held: int = 0
    dropped: int = 0
    embedded: int = 0
    forgotten: int = 0
    decayed: int = 0
    synthesized: int = 0
    edges: int = 0
    topics: int = 0
    flagged_stale: int = 0
    ref_renames: int = 0
    dry_run: bool = False
    actions: list[str] = dc_field(default_factory=list)


def _doc_text(mem: dict) -> str:
    title = mem.get("title") or ""
    return f"{title}\n{mem['body']}" if title else mem["body"]


def _content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _text_similarity(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, a.lower(), b.lower()).ratio()


def _cosine_matrix(embedder, texts: list[str]):
    """[N, N] cosine matrix, or None when the embedder can't deliver."""
    if embedder is None:
        return None
    vecs = embedder.embed(texts)
    if not vecs:
        return None
    import numpy as np
    mat = np.asarray(vecs, dtype="float32")
    return mat @ mat.T


def _pair_similarities(working: list[dict], pool: list[dict],
                       embedder) -> list[tuple[dict, dict, float]]:
    """(newcomer, candidate-keeper, similarity) for every working row
    against every other pool row. The pool includes episodic rows so a
    re-learned lesson folds into its consolidated ancestor."""
    texts = [_doc_text(m) for m in pool]
    cos = _cosine_matrix(embedder, texts)
    index_of = {m["id"]: i for i, m in enumerate(pool)}
    out: list[tuple[dict, dict, float]] = []
    for w in working:
        wi = index_of[w["id"]]
        for other in pool:
            if other["id"] == w["id"]:
                continue
            if cos is not None:
                sim = float(cos[wi][index_of[other["id"]]])
            else:
                sim = _text_similarity(texts[wi], texts[index_of[other["id"]]])
            out.append((w, other, sim))
    return out


def _keeper_of(a: dict, b: dict) -> tuple[dict, dict]:
    """(keeper, loser) — episodic beats working, then more-recalled, then
    older (the row that accumulated context first)."""
    tier_rank = {"episodic": 1, "working": 0}

    def _rank(m: dict) -> tuple[int, int]:
        return (tier_rank.get(m["tier"], 0), m.get("recall_count") or 0)

    ka, kb = _rank(a), _rank(b)
    if ka == kb:
        return (a, b) if a["created_at"] <= b["created_at"] else (b, a)
    return (a, b) if ka > kb else (b, a)


def _merge_pair(store, keeper: dict, loser: dict, *, dry_run: bool,
                result: ReflectResult) -> None:
    result.merged += 1
    result.actions.append(f"merge {loser['id'][:8]} -> {keeper['id'][:8]}")
    if dry_run:
        return
    bumped = max(keeper["importance"], loser["importance"])
    store.update(keeper["id"], importance=bumped)
    store.update(loser["id"], status="retired", superseded_by=keeper["id"])
    store.record_validation(loser["id"], validator="reflect", action="merged",
                            note=f"near-duplicate of {keeper['id']}")


def _time_ordered(a: dict, b: dict) -> tuple[dict, dict]:
    """(older, newer) by created_at."""
    return ((a, b) if (a["created_at"] or "") <= (b["created_at"] or "")
            else (b, a))


def _retire_older(store, older: dict, newer: dict, *, falsify: bool,
                  dry_run: bool, result: ReflectResult) -> None:
    """Retire the OLDER half of a judged pair via supersede. `falsify` is the
    CONTRADICT case (incompatible claim → veracity='false'); OBSOLETE is
    relocation-in-time, not falsity, so veracity stays untouched. Callers
    order the pair via `_time_ordered` before calling."""
    verb = "contradiction" if falsify else "obsolete"
    if falsify:
        result.contradictions += 1
    else:
        result.obsoleted += 1
    result.actions.append(
        f"{verb}: retire {older['id'][:8]} in favor of {newer['id'][:8]}")
    if dry_run:
        return
    fields = {"status": "retired", "superseded_by": newer["id"]}
    if falsify:
        fields["veracity"] = "false"
    store.update(older["id"], **fields)
    store.record_validation(
        older["id"], validator="reflect",
        action="veracity_false" if falsify else "obsoleted",
        note=(f"contradicted by {newer['id']}" if falsify
              else f"obsoleted by {newer['id']}"))


def _reinforced_importance(mem: dict) -> float:
    boost = 0.05 * math.log1p(mem.get("recall_count") or 0)
    return min(1.0, max(0.0, mem["importance"] + boost))


def _promote(store, mem: dict, *, now_field: str, dry_run: bool,
             result: ReflectResult) -> None:
    result.promoted += 1
    result.actions.append(f"promote {mem['id'][:8]} -> episodic")
    if dry_run:
        return
    store.update(mem["id"], tier="episodic", consolidated_at=now_field,
                 importance=_reinforced_importance(mem))


def _hold(mem: dict, *, result: ReflectResult) -> None:
    result.held += 1
    result.actions.append(f"hold {mem['id'][:8]} (working)")


def _drop(store, mem: dict, *, dry_run: bool, result: ReflectResult) -> None:
    result.dropped += 1
    result.actions.append(f"drop {mem['id'][:8]} (judged low-value)")
    if dry_run:
        return
    store.update(mem["id"], status="retired")
    store.record_validation(mem["id"], validator="reflect", action="dropped",
                            note="promote judge: low-value")


def _stale_embedding_todo(store, model_id: str) -> list[tuple[dict, str]]:
    """Active rows whose (content hash, model) doesn't match the stored
    vector — i.e. rows needing a (re-)embed. Both tiers on purpose: a
    fresh working-tier lesson is exactly what the next similar task
    needs, and leaving it unembedded until promotion made it invisible
    to dense recall for its most useful days (FTS only finds it on
    lexical luck). The tier split governs lifecycle, not visibility."""
    rows = store.list_memories(status="active",
                               include_tests=True, limit=10_000)
    meta = store.embedding_meta()
    todo = []
    for mem in rows:
        h = _content_hash(_doc_text(mem))
        if meta.get(mem["id"]) != (h, model_id):
            todo.append((mem, h))
    return todo


def _forget_stale(store, episodic: list[dict], *, dry_run: bool,
                  result: ReflectResult) -> None:
    """Retire episodic memories aged past `forget_after_days` that were
    never *deliberately* recalled (recall_count == 0). Speculative auto-
    inject doesn't reinforce, so a long-lived row with no recalls has
    never proven useful — the negative half of the usefulness loop. 0
    days disables; fresh rows can't be stale, so reflect stays effectively
    idempotent on a young store."""
    from datetime import datetime, timedelta
    days = settings.agent_memory.forget_after_days
    if days <= 0 or not episodic:
        return
    cutoff = (datetime.now() - timedelta(days=days)).isoformat()
    for mem in episodic:
        if (mem.get("recall_count") or 0) > 0 or mem["created_at"] >= cutoff:
            continue
        result.forgotten += 1
        result.actions.append(f"forget stale {mem['id'][:8]}")
        if dry_run:
            continue
        store.update(mem["id"], status="retired")
        store.record_validation(mem["id"], validator="reflect",
                                action="forgotten",
                                note="never recalled, aged out")


# Decay magnitude per reflect run, and the importance floor it stops at
# (the two triggers and their counts are tunable via `settings.agent_memory`:
# `decay_ignored_threshold` / `decay_injected_threshold`, 0 to disable each).
_IGNORED_DECAY_STEP = 0.1
_IGNORED_DECAY_FLOOR = 0.1
# Validation actions that hard-spare a row from decay — explicit, high-trust
# signals (human approval, distill auto-approval, resurface reinforcement).
# 'engaged' is deliberately NOT here: once the pending sweep densifies the
# signal a single engagement is weak evidence, so engagement spares by *rate*
# (`_engagement_spare`) instead, letting a heavily-ignored memory still decay.
_POSITIVE_ACTIONS = ("approved", "auto_approved", "reinforced")


def _validation_action_counts(store, memory_id: str) -> dict[str, int]:
    """{action: count} from a memory's (trimmed) validation log. Reads the
    model directly — the store keeps no aggregate, and the log is capped at
    a handful of rows, so this is cheap."""
    from sqlmodel import select

    from lib.memory.engine import MemorySessionLocal
    from lib.memory.models import MemoryValidation

    with MemorySessionLocal() as session:
        rows = session.exec(
            select(MemoryValidation.action)
            .where(MemoryValidation.memory_id == memory_id)).all()
    counts: dict[str, int] = {}
    for action in rows:
        counts[action] = counts.get(action, 0) + 1
    return counts


def _decay_spared(mem: dict, counts: dict[str, int]) -> bool:
    """A memory is spared from decay by any earned-usefulness signal — a
    deliberate recall or any `_POSITIVE_ACTIONS` validation — or once it has
    already sunk to the floor (nothing left to take)."""
    if (mem.get("recall_count") or 0) > 0:
        return True
    if any(counts.get(a) for a in _POSITIVE_ACTIONS):
        return True
    return (mem.get("importance") or 0.0) <= _IGNORED_DECAY_FLOOR


def _engagement_spare(engagement: tuple[int, int, int]) -> "bool | None":
    """Rate-based verdict on a memory's recorded engagement, or None when
    there is too little signal to decide (defer to the other triggers).

    Decay is driven by the *decisive* signal — `engaged` vs `hard_ignored`.
    `soft_ignored` (referents matched downstream but only generically, no idf
    credit) is generic contact: it neither credits nor condemns, so it is
    excluded from the rate and treated as benefit-of-the-doubt. This stops the
    idf gate from retiring a memory whose real value leaves no specific
    referent (matched but never idf-engaged) — absence of *specific* evidence
    is not evidence of uselessness.

      True  — engaged/(engaged+hard) ≥ `engage_spare_rate` at ≥
              `engage_min_volume` decisive injects (proven useful); or any
              positive contact (engaged or soft) below the decisive floor.
      False — a low rate *at* decisive volume (proven low-value → decay even
              if the memory once reinforced).
      None  — no scored injects, or only sub-floor hard ignores to go on.
    `engage_spare_rate == 0` disables the rate path entirely (None)."""
    spare_rate = settings.agent_memory.engage_spare_rate
    if not spare_rate:
        return None
    engaged, soft, hard = engagement
    if engaged + soft + hard == 0:
        return None
    decisive = engaged + hard
    if decisive < settings.agent_memory.engage_min_volume:
        return True if (engaged + soft) > 0 else None
    return engaged / decisive >= spare_rate


def _decay_reason(mem: dict, counts: dict[str, int],
                  injection: tuple[int, int],
                  engagement: tuple[int, int, int]) -> "str | None":
    """Why this memory should lose importance this run, or None to spare it.

    Checked in order:
      - hard spares (`_decay_spared`): deliberate recall, an explicit
        high-trust validation, or already at the floor.
      - engaged-*rate* (`_engagement_spare`): the dense always-on signal.
        A high rate spares outright; a low rate at volume *forces*
        `low_engagement` decay (gated once per memory by a prior
        `decayed_low_engagement` validation, so it can't decay every run).
      - `ignored` — `decay_ignored_threshold`+ feedback 'ignored' verdicts.
        Self-limiting: writing `decayed_ignored` evicts an older row from the
        capped log, so the count falls back under threshold until fresh
        ignores arrive. Largely subsumed by the rate path now; kept for the
        grade-time-only signal.
      - `injected` — `decay_injected_threshold`+ auto-injects with zero
        reinforcement, for rows the sweep has no engagement verdict on yet.
        Gated once per memory (a prior `decayed_injected` blocks it).
    Returns the trigger name (used in the validation note) so the triggers
    stay distinguishable in the log."""
    if _decay_spared(mem, counts):
        return None
    spare = _engagement_spare(engagement)
    if spare is not None:
        # The dense engagement signal is strictly better evidence than the
        # raw count thresholds, so when it reaches a verdict it *owns* the
        # decision: a high rate spares; a low rate decays once (then spares,
        # gated by `decayed_low_engagement`) rather than also tripping the
        # injected trigger on the same memory.
        if spare:
            return None
        return None if counts.get("decayed_low_engagement") else "low_engagement"
    return _threshold_decay_reason(counts, injection)


def _threshold_decay_reason(counts: dict[str, int],
                            injection: tuple[int, int]) -> "str | None":
    """The pre-rate count thresholds, for rows the engagement signal can't
    yet decide: enough grade-time 'ignored' verdicts, or enough zero-
    reinforcement injects (each gated/self-limiting; see `_decay_reason`)."""
    ignored_t = settings.agent_memory.decay_ignored_threshold
    if ignored_t and counts.get("ignored", 0) >= ignored_t:
        return "ignored"
    injected, reinforced = injection
    injected_t = settings.agent_memory.decay_injected_threshold
    if (injected_t and reinforced == 0 and injected >= injected_t
            and not counts.get("decayed_injected")):
        return "injected"
    return None


def _score_pending(store, *, dry_run: bool) -> None:
    """Densify the positive signal before decay reads it: stamp engagement
    verdicts on finished, unscored injects so high-engaged memories are
    spared and chronic low-rate ones are caught this same pass. A write, so
    skipped on dry-run; gated by `score_pending_on_reflect`."""
    if dry_run or not settings.agent_memory.score_pending_on_reflect:
        return
    from lib.memory.feedback import (rebuild_session_referent_df,
                                     score_pending_sessions)
    # Refresh the session-span df cache first so the sweep's idf-weighted
    # verdicts read current ubiquity, not last pass's.
    rebuild_session_referent_df(store)
    score_pending_sessions(store)
    _refresh_query_df()


def _refresh_query_df() -> None:
    """Rebuild the topic router's query-log term-frequency cache off the same
    routed-prompt corpus the recall hook logs. Best-effort and isolated: a
    topics-side failure must never break the memory reflect sweep."""
    try:
        from lib.topics.term_weights import rebuild_query_df
        rebuild_query_df(settings.project_root)
    except Exception:  # noqa: BLE001 - cache refresh is non-critical
        log.error("query_df_refresh_failed", exc_info=True)


def _decay_note(reason: str, injection: tuple[int, int],
                engagement: tuple[int, int, int]) -> str:
    """Human-readable provenance for a decay validation, per trigger."""
    if reason == "ignored":
        return "ignored on injection, never recalled"
    if reason == "low_engagement":
        engaged, _soft, hard = engagement
        return f"engaged {engaged}/{engaged + hard} decisive injects, low rate"
    return f"injected {injection[0]}x, never reinforced or recalled"


def _decay_chronically_ignored(store, episodic: list[dict], *, dry_run: bool,
                               result: ReflectResult) -> None:
    """The negative half of the inject→usefulness loop: a memory that earned
    no positive signal and either drew `decay_ignored_threshold`+ 'ignored'
    verdicts or was injected `decay_injected_threshold`+ times without ever
    being reinforced loses `_IGNORED_DECAY_STEP` importance (floored, not
    retired). See `_decay_reason` for the per-trigger cadence guarantees.
    Complements `lib.memory.feedback`'s positive 'engaged' reward."""
    injection = store.injection_counts()
    engagement = store.engagement_match_counts()
    for mem in episodic:
        counts = _validation_action_counts(store, mem["id"])
        reason = _decay_reason(mem, counts, injection.get(mem["id"], (0, 0)),
                               engagement.get(mem["id"], (0, 0, 0)))
        if reason is None:
            continue
        decayed = max(_IGNORED_DECAY_FLOOR,
                      (mem.get("importance") or 0.0) - _IGNORED_DECAY_STEP)
        result.decayed += 1
        result.actions.append(f"decay {reason} {mem['id'][:8]} -> {decayed:.2f}")
        if dry_run:
            continue
        note = _decay_note(reason, injection.get(mem["id"], (0, 0)),
                           engagement.get(mem["id"], (0, 0, 0)))
        store.update(mem["id"], importance=decayed)
        store.record_validation(mem["id"], validator="reflect",
                                action=f"decayed_{reason}", note=note)


# Re-verification: a memory that names a concrete repo file path goes stale
# when the code moves and the path stops resolving — the structural complement
# to `valid_until` time-expiry. We flag it (a 'stale_ref' validation + a
# veracity demote true→unknown), never retire or falsify: a regex + filesystem
# check is a heuristic, and the named code may have merely moved while the
# lesson holds. Bare filenames (no slash) are skipped — too ambiguous to verify
# without false hits; precision over recall, as elsewhere in this store.
_REF_PATH_RE = re.compile(
    r"\b([\w.-]+(?:/[\w.-]+)+\."
    r"(?:py|vue|js|ts|tsx|jsx|md|sql|sh|toml|ya?ml|css|html|json))\b")


def _referenced_paths(mem: dict) -> set[str]:
    """Concrete repo file paths named in a memory (slash + known extension)."""
    return set(_REF_PATH_RE.findall(_doc_text(mem)))


def _repo_root_for_scope(scope: "str | None") -> "str | None":
    """Filesystem root of a `repo:<name>` scope (basename match against the
    registered repos), or None for global / unregistered scopes — whose
    references can't be verified, so the memory is left untouched."""
    import os
    if not scope or not scope.startswith("repo:"):
        return None
    name = scope.split(":", 1)[1]
    for repo_path in settings.repo_paths:
        root = os.path.realpath(str(repo_path))
        if os.path.basename(root) == name:
            return root
    return None


def _missing_refs(root: str, paths: set[str]) -> list[str]:
    import os
    return sorted(p for p in paths
                  if not os.path.exists(os.path.join(root, p)))


def _rename_follow(store, root: str, mem: dict, missing: list[str], *,
                   dry_run: bool, result: ReflectResult) -> list[str]:
    """High-confidence half of stale-ref handling: for missing paths that git
    history shows were *renamed* (not deleted), rewrite the memory body to the
    new path and leave veracity untouched — a rename is relocation, not
    staleness. Returns the residual genuinely-missing paths for the caller to
    flag. A no-op (returns `missing` unchanged) unless `mechanical_autoapply`
    is on; best-effort, so a git failure can't break the reflect pass."""
    if not settings.topic_evolution.mechanical_autoapply:
        return missing
    try:
        from lib.topics.drift import renames_from_history, rewrite_memory_body
        renames = renames_from_history(root, set(missing))
        if not renames:
            return missing
        if rewrite_memory_body(store, mem, renames, dry_run=dry_run):
            result.ref_renames += 1
            result.actions.append(
                f"rename-follow {mem['id'][:8]}: {len(renames)} path(s)")
        return [p for p in missing if p not in renames]
    except Exception:  # noqa: BLE001 - rename-follow must not break reflect
        log.error("reflect_rename_follow_failed", exc_info=True)
        return missing


def _check_one_stale(store, mem: dict, *, dry_run: bool,
                     result: ReflectResult) -> None:
    """Flag one memory if a concrete path it names no longer resolves. Skips
    rows already flagged (idempotency), unverifiable scopes, and rows with no
    path references or no missing paths. A renamed (not deleted) path is
    rewritten in place first — only the genuinely-deleted residual is flagged."""
    paths = _referenced_paths(mem)
    if not paths:
        return
    root = _repo_root_for_scope(mem.get("scope"))
    if root is None or _validation_action_counts(
            store, mem["id"]).get("stale_ref"):
        return
    missing = _missing_refs(root, paths)
    if not missing:
        return
    missing = _rename_follow(store, root, mem, missing,
                             dry_run=dry_run, result=result)
    if not missing:
        return
    result.flagged_stale += 1
    result.actions.append(
        f"stale ref {mem['id'][:8]}: {', '.join(missing[:3])}")
    if dry_run:
        return
    if mem.get("veracity") == "true":
        store.update(mem["id"], veracity="unknown")
    store.record_validation(mem["id"], validator="reflect", action="stale_ref",
                            note=f"unresolved path(s): {', '.join(missing[:5])}")


def _flag_stale_references(store, rows: list[dict], *, dry_run: bool,
                           result: ReflectResult) -> None:
    """The structural half of memory hygiene: flag active memories whose named
    repo file paths no longer exist, for human review at /memory. A no-op
    unless `verify_stale_refs` is on (filesystem-touching, off by default)."""
    if not settings.agent_memory.verify_stale_refs:
        return
    for mem in rows:
        _check_one_stale(store, mem, dry_run=dry_run, result=result)


def _extract_json_object(answer: str):
    """Parse a single JSON object out of model output, tolerating fences and
    surrounding prose. None when no object can be parsed."""
    text = re.sub(r"```(?:json)?", "", answer or "")
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        parsed = json.loads(text[start:end + 1])
    except (json.JSONDecodeError, ValueError):
        return None
    return parsed if isinstance(parsed, dict) else None


def _record_synthesis_topic(store, members, draft, mid, topic_scope,
                            embedder, result: ReflectResult) -> None:
    """Group the cluster under a topic. When
    `reflect_proposes_authoritative_topics` is on, feed the synthesised rule
    into the authoritative topic-proposal queue (merge onto an approved node
    or create a candidate); otherwise mint the orphan `memory_topic` as
    before (gated on `topics_enabled`)."""
    cfg = settings.agent_memory
    if cfg.reflect_proposes_authoritative_topics:
        from lib.memory.topic_attach import maybe_propose_authoritative
        decision = maybe_propose_authoritative(
            store, draft, scope=topic_scope, summary_memory_id=mid,
            embedder=embedder)
        if decision is not None:
            result.topics += 1
        return
    if cfg.topics_enabled:
        store.create_topic(name=draft["title"], summary=draft["body"][:280],
                           summary_memory_id=mid, scope=topic_scope,
                           member_ids=[m["id"] for m in members])
        result.topics += 1


def _retire_legacy_digests(store, *, dry_run: bool,
                           result: ReflectResult) -> None:
    """One-time cleanup for the removed digest stage: any still-active
    `kind='digest'` row is retired so the write-only briefings stop riding
    along. Idempotent — once retired, nothing matches again."""
    rows = store.list_memories(kind="digest", status="active",
                               include_tests=True, limit=100)
    for row in rows:
        result.actions.append(f"retire legacy digest {row['id'][:8]}")
        if dry_run:
            continue
        store.update(row["id"], status="retired")
        store.record_validation(row["id"], validator="reflect",
                                action="retired",
                                note="digest stage removed")


def _cap_edges_per_node(pairs: list, cap: int) -> list:
    """Drop the weakest edges so no node keeps more than `cap`, preventing a
    dense cluster from fanning into a hairball. Greedy strongest-first."""
    deg: dict[str, int] = {}
    kept = []
    for a, b, w in sorted(pairs, key=lambda p: -p[2]):
        if deg.get(a, 0) >= cap or deg.get(b, 0) >= cap:
            continue
        deg[a] = deg.get(a, 0) + 1
        deg[b] = deg.get(b, 0) + 1
        kept.append((a, b, w))
    return kept


def _harvest_edges(store, embedder, *, dry_run: bool,
                   result: ReflectResult) -> None:
    """Persist the embedding-cosine neighbour graph as `related` edges,
    reusing the vectors `_embed_episodic` just wrote. Pairs in the synthesis
    band [`edge_floor`, dedup_threshold) — near-identical pairs are merged, not
    linked. Rebuilds the whole `related` set so it tracks the live store. Needs
    only an embedder; a no-op when edges are off or no embedder is present."""
    cfg = settings.agent_memory
    if not cfg.edges_enabled or embedder is None or embedder.model_id is None:
        return
    # Harvest over the full population (test rows included); edge_neighbors
    # filters tests at read time, mirroring neighbors()/recall.
    pairs = store.cosine_pairs(floor=cfg.edge_floor,
                               ceiling=cfg.dedup_cosine_threshold,
                               model_id=embedder.model_id, include_tests=True)
    if cfg.edge_max_per_node:
        pairs = _cap_edges_per_node(pairs, cfg.edge_max_per_node)
    result.edges = len(pairs)
    result.actions.append(f"harvest {len(pairs)} related edge(s)")
    if not dry_run:
        store.replace_related_edges(pairs)


def _embed_episodic(store, embedder, *, dry_run: bool,
                    result: ReflectResult) -> None:
    if embedder is None or embedder.model_id is None:
        return
    todo = _stale_embedding_todo(store, embedder.model_id)
    if not todo:
        return
    result.embedded = len(todo)
    result.actions.append(f"embed {len(todo)} episodic row(s)")
    if dry_run:
        return
    vecs = embedder.embed([_doc_text(m) for m, _ in todo])
    if not vecs:
        return
    for (mem, h), vec in zip(todo, vecs):
        store.set_embedding(mem["id"], vec, embedder.model_id, h)


def _dedup_merge(store, working: list[dict], pool: list[dict],
                 embedder, *, dry_run: bool,
                 result: ReflectResult) -> set[str]:
    """Mechanical dedup: collapse near-identical pairs at/above the dedup
    threshold; return ids consumed by a merge."""
    threshold = (settings.agent_memory.dedup_cosine_threshold
                 if embedder is not None and embedder.model_id is not None
                 else settings.agent_memory.dedup_text_threshold)
    consumed: set[str] = set()
    for newcomer, other, sim in _pair_similarities(working, pool, embedder):
        if newcomer["id"] in consumed or other["id"] in consumed:
            continue
        if sim >= threshold:
            keeper, loser = _keeper_of(newcomer, other)
            _merge_pair(store, keeper, loser, dry_run=dry_run, result=result)
            consumed.add(loser["id"])
    return consumed


def _bucket_pairs(bucket: list[dict], seen: set) -> list[tuple[dict, dict]]:
    """Time-ordered candidate pairs within one shared-path bucket: identical
    scope only (a cross-repo README.md is not the same referent), not already
    chained by supersede, deduped across buckets via `seen`."""
    out: list[tuple[dict, dict]] = []
    for i, a in enumerate(bucket):
        for b in bucket[i + 1:]:
            key = tuple(sorted((a["id"], b["id"])))
            if key in seen or a["scope"] != b["scope"]:
                continue
            if (a.get("superseded_by") == b["id"]
                    or b.get("superseded_by") == a["id"]):
                continue
            seen.add(key)
            out.append(_time_ordered(a, b))
    return out


def _shared_referent_pairs(rows: list[dict]) -> list[tuple[dict, dict]]:
    """Candidate (older, newer) pairs for the contradiction sweep: two
    same-scope rows naming at least one common concrete repo file path. No
    cosine gate — real contradictions are low-similarity. Built off an
    inverted path→rows index (pairs form only inside a bucket, not across
    the whole corpus). Newest newer-member first, so fresh knowledge is
    judged before the budget runs out."""
    by_path: dict[str, list[dict]] = {}
    for m in rows:
        for path in _referenced_paths(m):
            by_path.setdefault(path, []).append(m)
    seen: set = set()
    pairs: list[tuple[dict, dict]] = []
    for bucket in by_path.values():
        pairs.extend(_bucket_pairs(bucket, seen))
    pairs.sort(key=lambda p: p[1]["created_at"] or "", reverse=True)
    return pairs


# ── Dream: the single agentic LLM stage ─────────────────────────────────
_DREAM_NEIGHBOURS = 3         # co-retrieval neighbours shown per working row
_DREAM_PACK_CAP = 40          # total evidence entries in one pack
_DREAM_MAX_WORKING = 25       # working rows per dream; the rest defer a run
_DREAM_CLIP = 600             # chars of body per pack entry
_DREAM_ROW_ACTIONS = ("promote", "hold", "drop", "merge")
_DREAM_PAIR_ACTIONS = ("contradict", "obsolete", "distinct")
_DREAM_PYTHON = ".venv/bin/python"


@dataclass
class _DreamPack:
    """The bounded, mechanically-built evidence one dream call sees."""

    working: list
    working_by_id: dict
    neighbours: dict              # working id -> [neighbour rows]
    pairs: list                   # [(older, newer)]
    by_id: dict                   # id -> row, for every entry in the pack


def _pack_entry(mem: dict) -> str:
    return (f"[{mem['id']}] tier={mem['tier']} scope={mem['scope']} "
            f"created={mem.get('created_at') or 'unknown'}\n"
            f"{_doc_text(mem)[:_DREAM_CLIP]}")


def _neighbour_k(pending_count: int) -> int:
    """Per-row neighbour context shrinks as the working backlog grows, so
    suspect pairs keep their share of the bounded pack."""
    if pending_count <= 8:
        return _DREAM_NEIGHBOURS
    return 2 if pending_count <= 16 else 1


def _cap_pending(pending: list[dict]) -> tuple[list[dict], list[dict]]:
    """(packed, deferred): newest-first cap on the working rows one dream
    sees. Deferred rows are untouched — excluded from the dream AND from
    the blind-promote fallback, so they stay working for the next run and
    the prompt stays bounded."""
    ordered = sorted(pending, key=lambda m: m.get("created_at") or "",
                     reverse=True)
    return ordered[:_DREAM_MAX_WORKING], ordered[_DREAM_MAX_WORKING:]


def _pack_neighbours(store, pending: list[dict], k: int) -> dict:
    """Top-k co-retrieval neighbours per working row via the store's own
    recall path — what the runtime would actually surface next to it, not a
    raw cosine band. Best-effort: a recall failure just means no context."""
    out: dict = {}
    for w in pending:
        try:
            hits = store.recall(_doc_text(w), top_k=k + 1,
                                include_tests=True, reinforce=False)
        except Exception:  # noqa: BLE001 - evidence is optional, never fatal
            log.error("dream_neighbour_recall_failed", exc_info=True)
            hits = []
        out[w["id"]] = [h.memory for h in hits
                        if h.memory["id"] != w["id"]][:k]
    return out


def _suspect_pairs(store, rows: list[dict]) -> list:
    """Up to `contradiction_budget` same-scope shared-referent pairs not yet
    judged by a dream (`memory_pair_checks` is the judged-pair ledger,
    loaded once; an offered-but-unjudged pair re-presents next run)."""
    checked = store.checked_pair_keys()
    budget = max(0, settings.agent_memory.contradiction_budget)
    pairs: list = []
    for older, newer in _shared_referent_pairs(rows):
        if len(pairs) >= budget:
            break
        if tuple(sorted((older["id"], newer["id"]))) in checked:
            continue
        pairs.append((older, newer))
    return pairs


def _fresh_episodic(store, episodic: list[dict], consumed: set, *,
                    dry_run: bool) -> list[dict]:
    """Active episodic rows as of NOW for the pack: a wet run re-lists from
    the store (rows merged/retired earlier this run drop out); a dry run
    wrote nothing, so it filters the start-of-run snapshot instead."""
    if dry_run:
        return [m for m in episodic if m["id"] not in consumed]
    return [m for m in store.list_memories(tier="episodic", status="active",
                                           include_tests=True, limit=10_000)
            if m["kind"] != "digest"]


def _trim_pack(pending: list, pairs: list, neighbours: dict) -> None:
    """Bound the pack: working rows are never dropped (each needs a
    verdict); neighbour context shrinks first, then the oldest suspect
    pairs fall off the end."""
    budget = _DREAM_PACK_CAP - len(pending)

    def rest() -> int:
        return sum(len(v) for v in neighbours.values()) + 2 * len(pairs)

    while rest() > budget and any(neighbours.values()):
        wid = max(neighbours, key=lambda k: len(neighbours[k]))
        neighbours[wid].pop()
    while rest() > budget and pairs:
        pairs.pop()


def _build_dream_pack(store, pending: list[dict],
                      episodic_rows: list[dict]) -> _DreamPack:
    pairs = _suspect_pairs(store, episodic_rows)
    neighbours = _pack_neighbours(store, pending, _neighbour_k(len(pending)))
    _trim_pack(pending, pairs, neighbours)
    by_id = {w["id"]: w for w in pending}
    for rows in neighbours.values():
        for n in rows:
            by_id.setdefault(n["id"], n)
    for a, b in pairs:
        by_id.setdefault(a["id"], a)
        by_id.setdefault(b["id"], b)
    return _DreamPack(working=pending,
                      working_by_id={w["id"]: w for w in pending},
                      neighbours=neighbours, pairs=pairs, by_id=by_id)


def _working_block(pack: _DreamPack) -> str:
    blocks = []
    for w in pack.working:
        lines = [_pack_entry(w)]
        near = pack.neighbours.get(w["id"]) or []
        if near:
            lines.append("nearest kept memories:")
            lines.extend(f"  {_pack_entry(n)}" for n in near)
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks) or "(none)"


def _pairs_block(pack: _DreamPack) -> str:
    blocks = [f"OLDER:\n{_pack_entry(older)}\nNEWER:\n{_pack_entry(newer)}"
              for older, newer in pack.pairs]
    return "\n\n".join(blocks) or "(none)"


def _extract_json_array(answer: str):
    """Parse a top-level JSON array out of model output, tolerating fences
    and surrounding prose. None when no array can be parsed."""
    text = re.sub(r"```(?:json)?", "", answer or "")
    start, end = text.find("["), text.rfind("]")
    if start < 0 or end <= start:
        return None
    try:
        parsed = json.loads(text[start:end + 1])
    except (json.JSONDecodeError, ValueError):
        return None
    return parsed if isinstance(parsed, list) else None


def _dream_plan_actions(llm, pack: _DreamPack) -> "list | None":
    """The single LLM call of the run: render the pack into the dream
    surface, parse the plan. Accepts the documented `{"actions": [...]}`
    object or a bare top-level action array; None on no/unparseable
    answer."""
    from lib.prompts import render_surface
    from lib.prompts.surfaces.memory import DREAM_SURFACE_ID
    prompt = render_surface(DREAM_SURFACE_ID, {
        "working_block": _working_block(pack),
        "pairs_block": _pairs_block(pack),
        "python": _DREAM_PYTHON})
    answer = llm.complete(prompt, max_tokens=4000,
                          surface_id=DREAM_SURFACE_ID)
    plan = _extract_json_object(answer or "")
    if isinstance(plan, dict) and isinstance(plan.get("actions"), list):
        return plan["actions"]
    return _extract_json_array(answer or "")


@dataclass
class _PlanState:
    """Mutable bookkeeping while one dream plan is applied: `handled`
    working ids never fall through to the blind-promote tail; `retired`
    ids are dead for the rest of the plan (no merge keeper, no pair side);
    `judged` pair keys apply once; `merging` is the pre-scanned set of
    merge subjects, so a keeper that is itself merged away this plan is
    invalid (a circular merge would otherwise retire both rows)."""

    merging: frozenset
    handled: set = dc_field(default_factory=set)
    judged: set = dc_field(default_factory=set)
    retired: set = dc_field(default_factory=set)


def _hold_skipped(row: dict, state: _PlanState, *,
                  result: ReflectResult) -> None:
    """The uniform invalid-row-action rule: the model addressed this row
    but its action can't be applied — hold it (never blind-promote a row
    the model may have wanted retired) and count the skip."""
    result.dream_skipped += 1
    state.handled.add(row["id"])
    _hold(row, result=result)


def _merge_keeper(action: dict, pack: _DreamPack,
                  state: _PlanState) -> "dict | None":
    """The validated merge target: a distinct pack entry that is neither
    retired earlier in this plan nor itself a merge subject."""
    keeper = pack.by_id.get(action.get("keeper"))
    if keeper is None or keeper["id"] == action.get("id"):
        return None
    if keeper["id"] in state.retired or keeper["id"] in state.merging:
        return None
    return keeper


def _apply_row_action(store, action: dict, pack: _DreamPack,
                      state: _PlanState, *, now_field: str, dry_run: bool,
                      result: ReflectResult) -> None:
    """Row verdicts touch only pack working rows, once each; destructive
    verdicts (drop/merge) are honoured only under `promote_allow_retire`,
    else they degrade to hold. An invalid-but-addressed row is held, never
    blind-promoted."""
    mid = action.get("id")
    row = pack.working_by_id.get(mid)
    if row is None or mid in state.handled:
        result.dream_skipped += 1
        return
    kind = action["action"]
    allow = settings.agent_memory.promote_allow_retire
    if kind == "merge":
        keeper = _merge_keeper(action, pack, state)
        if keeper is None:
            _hold_skipped(row, state, result=result)
            return
        state.handled.add(mid)
        if allow:
            _merge_pair(store, keeper, row, dry_run=dry_run, result=result)
            state.retired.add(mid)
        else:
            _hold(row, result=result)
        return
    state.handled.add(mid)
    if kind == "promote":
        _promote(store, row, now_field=now_field,
                 dry_run=dry_run, result=result)
    elif kind == "drop" and allow:
        _drop(store, row, dry_run=dry_run, result=result)
        state.retired.add(mid)
    else:                          # hold, or drop without the retire opt-in
        _hold(row, result=result)


def _pair_rows(action: dict, pack: _DreamPack,
               state: _PlanState) -> "tuple[dict, dict] | None":
    """(older, newer) for a pair verdict over ANY two distinct same-scope
    pack entries — a working row may be judged against its neighbour, not
    only the pre-offered suspect pairs. None when either side is out of
    pack, scopes differ, a side is already retired this plan, or the pair
    was judged already."""
    a = pack.by_id.get(action.get("older"))
    b = pack.by_id.get(action.get("newer"))
    if a is None or b is None or a["id"] == b["id"]:
        return None
    if a["scope"] != b["scope"]:
        return None
    if a["id"] in state.retired or b["id"] in state.retired:
        return None
    key = tuple(sorted((a["id"], b["id"])))
    if key in state.judged:
        return None
    state.judged.add(key)
    return _time_ordered(a, b)


def _apply_pair_action(store, action: dict, pack: _DreamPack,
                       state: _PlanState, *, dry_run: bool,
                       result: ReflectResult) -> None:
    """The model-claimed older/newer order is never trusted — `created_at`
    decides. Every judged pair lands in the judged-pair ledger; a retired
    working row is marked handled so the fallback never re-promotes it."""
    pair = _pair_rows(action, pack, state)
    if pair is None:
        result.dream_skipped += 1
        return
    older, newer = pair
    result.pairs_checked += 1
    kind = action["action"]
    if kind in ("contradict", "obsolete"):
        _retire_older(store, older, newer, falsify=(kind == "contradict"),
                      dry_run=dry_run, result=result)
        state.retired.add(older["id"])
        if older["id"] in pack.working_by_id:
            state.handled.add(older["id"])
    else:
        result.actions.append(
            f"distinct: keep {older['id'][:8]} + {newer['id'][:8]}")
    if not dry_run:
        store.record_pair_check(older["id"], newer["id"], kind.upper())


def _synthesis_rows_eligible(store, rows: list[dict]) -> bool:
    """All EPISODIC (a working row's fate belongs to its own row action),
    ONE scope, and none already folded into a prior synthesis (the
    `synthesized` validation is the idempotency marker)."""
    if any(r["tier"] != "episodic" for r in rows):
        return False
    if len({r["scope"] for r in rows}) != 1:
        return False
    return not any(
        _validation_action_counts(store, r["id"]).get("synthesized")
        for r in rows)


def _synthesis_sources(store, action: dict,
                       pack: _DreamPack) -> "list | None":
    """The validated source rows for one synthesize action: ≥3 distinct,
    resolvable pack ids clearing `_synthesis_rows_eligible` — else None."""
    ids = list(dict.fromkeys(action.get("source_ids") or []))
    rows = [pack.by_id.get(i) for i in ids]
    if len(rows) < 3 or any(r is None for r in rows):
        return None
    return rows if _synthesis_rows_eligible(store, rows) else None


def _apply_synthesize(store, action: dict, pack: _DreamPack, embedder, *,
                      dry_run: bool, result: ReflectResult) -> None:
    """Code-enforced synthesis constraints: validated same-scope episodic
    sources, a real title+body, importance = median of the sources (an
    abstraction must earn rank, never outrank its evidence by
    construction), and the distill approval gate decides
    proposed-vs-active."""
    rows = _synthesis_sources(store, action, pack)
    title = str(action.get("title") or "").strip()
    body = str(action.get("body") or "").strip()
    if rows is None or len(title) < 10 or len(body) < 60:
        result.dream_skipped += 1
        return
    importance = min(1.0, float(statistics.median(
        r["importance"] for r in rows)))
    status = ("active" if importance >=
              settings.agent_memory.auto_approve_importance else "proposed")
    result.synthesized += 1
    result.actions.append(
        f"synthesize {len(rows)} -> «{title[:48]}» ({status})")
    if dry_run:
        return
    from lib.memory.models import MemoryInput
    scope = rows[0]["scope"]
    mid = store.remember(MemoryInput(
        body=body[:2000], title=title[:120], kind="lesson",
        tier="episodic", status=status, scope=scope,
        tags=["synthesis"], importance=importance))
    for r in rows:
        store.record_validation(r["id"], validator="reflect",
                                action="synthesized", note=f"into {mid}")
    _record_synthesis_topic(store, rows, {"title": title, "body": body},
                            mid, scope, embedder, result)


def _merge_subject_ids(actions: list) -> frozenset:
    return frozenset(a.get("id") for a in actions
                     if isinstance(a, dict) and a.get("action") == "merge")


def _apply_dream_plan(store, actions: list, pack: _DreamPack, embedder, *,
                      now_field: str, dry_run: bool,
                      result: ReflectResult) -> set[str]:
    """Deterministic application of the parsed plan. Working rows the plan
    never mentions fall back to a blind promote (a partial plan must not
    strand fresh lessons in the working tier); rows the plan addressed —
    even invalidly — never do (see `_hold_skipped`)."""
    state = _PlanState(merging=_merge_subject_ids(actions))
    for action in actions:
        kind = action.get("action") if isinstance(action, dict) else None
        if kind in _DREAM_ROW_ACTIONS:
            _apply_row_action(store, action, pack, state,
                              now_field=now_field, dry_run=dry_run,
                              result=result)
        elif kind in _DREAM_PAIR_ACTIONS:
            _apply_pair_action(store, action, pack, state,
                               dry_run=dry_run, result=result)
        elif kind == "synthesize":
            _apply_synthesize(store, action, pack, embedder,
                              dry_run=dry_run, result=result)
        else:
            result.dream_skipped += 1
    for row in pack.working:
        if row["id"] not in state.handled:
            _promote(store, row, now_field=now_field,
                     dry_run=dry_run, result=result)
    return state.retired


def _blind_promote(store, pending: list[dict], *, now_field: str,
                   dry_run: bool, result: ReflectResult) -> None:
    for mem in pending:
        _promote(store, mem, now_field=now_field,
                 dry_run=dry_run, result=result)


def _dream(store, working: list[dict], consumed: set, episodic: list[dict],
           embedder, llm, *, dry_run: bool,
           result: ReflectResult) -> set[str]:
    """The single agentic LLM stage: one `llm.complete` per reflect run over
    a bounded evidence pack; the returned plan is applied deterministically.
    No LLM / disabled / unparseable → blind promote (consolidation never
    blocks on a model outage). Working rows beyond the pack cap are
    deferred untouched to the next run. Returns the ids retired by pair
    verdicts."""
    from datetime import datetime
    now_field = datetime.now().isoformat()
    pending_all = [m for m in working if m["id"] not in consumed]
    if llm is None or not settings.agent_memory.dream_enabled:
        _blind_promote(store, pending_all, now_field=now_field,
                       dry_run=dry_run, result=result)
        return set()
    pending, deferred = _cap_pending(pending_all)
    if deferred:
        result.actions.append(
            f"defer {len(deferred)} working row(s) to the next run")
    if not dry_run:
        store.prune_pair_checks()
    rows = _fresh_episodic(store, episodic, consumed, dry_run=dry_run)
    pack = _build_dream_pack(store, pending, rows)
    if not pack.working and not pack.pairs:
        return set()
    actions = _dream_plan_actions(llm, pack)
    if actions is None:
        _blind_promote(store, pack.working, now_field=now_field,
                       dry_run=dry_run, result=result)
        return set()
    return _apply_dream_plan(store, actions, pack, embedder,
                             now_field=now_field, dry_run=dry_run,
                             result=result)


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
    return result


__all__ = ["reflect", "ReflectResult", "stale_embedding_todo"]

# Public alias so callers (e.g. store.py) can import the staleness helper
# without duplicating the logic.  The private name stays for internal calls.
stale_embedding_todo = _stale_embedding_todo
