"""`reflect()` — the consolidation cycle (mnemopi's `sleep`).

Walks the `working` tier and, in order:

  1. **Dedup** — near-identical memories collapse into one row: the
     keeper survives (episodic preferred, then most-recalled, then
     oldest), the newcomer is retired with `superseded_by` pointing at
     it. Similarity is embedding cosine when an `EmbeddingProvider` is
     injected, else a deterministic text ratio — reflect never *requires*
     a model.
  2. **Contradiction check** — pairs in the similarity gray zone are put
     to the `LLMProvider` when one is configured; a judged contradiction
     retires the older row with `veracity='false'`. No LLM → the pair is
     left alone (veracity stays `unknown` rather than guessed).
  3. **Promote** — surviving working rows become `episodic`, stamped
     `consolidated_at`, importance nudged by the reinforcement signal
     (`recall_count`).
  4. **Synthesize** — clusters of *related but distinct* episodic rows are
     handed to the `LLMProvider` (when one is configured alongside an
     embedder) to abstract a single higher-order rule — Generative-Agents
     reflection, the step beyond dedup/GC. Sources are kept and marked
     `synthesized`; recall favours the more general, higher-importance
     synthesis. No embedder or no LLM → skipped.
  5. **Digest** — (opt-in, `digest_enabled`) roll each scope's most
     important episodic rows into ONE maintained briefing, refreshed in
     place via supersede. The structure layer: standing context read by
     scope, excluded from similarity recall and the lifecycle. Needs only
     an LLM. Runs after synthesis so a fresh card can feed it the same pass.
  6. **Embed** — active rows of both tiers get vectors (content-hash-
     skipped when unchanged) so the dense recall leg can see them; a
     fresh working-tier lesson must be dense-visible before promotion.

`dry_run=True` reports what would happen without writing.
"""

from __future__ import annotations

import difflib
import hashlib
import json
import math
import re
from dataclasses import dataclass, field as dc_field
from typing import Optional

from lib.activity_log import get_activity_logger
from lib.settings import settings

log = get_activity_logger("memory")

# Gray zone below the dedup threshold where two memories are suspiciously
# close but not mergeable — candidates for the LLM contradiction check.
_GRAY_ZONE_FLOOR = 0.75


@dataclass
class ReflectResult:
    examined: int = 0
    merged: int = 0
    contradictions: int = 0
    promoted: int = 0
    embedded: int = 0
    forgotten: int = 0
    decayed: int = 0
    synthesized: int = 0
    edges: int = 0
    topics: int = 0
    digests: int = 0
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


def _llm_says_contradiction(llm, a: dict, b: dict) -> bool:
    from lib.prompts import render_surface
    from lib.prompts.surfaces.memory import CONTRADICTION_SURFACE_ID
    prompt = render_surface(CONTRADICTION_SURFACE_ID, {
        "memory_a": _doc_text(a)[:1500], "memory_b": _doc_text(b)[:1500]})
    answer = llm.complete(prompt, max_tokens=8, surface_id=CONTRADICTION_SURFACE_ID)
    return bool(answer) and "CONTRADICT" in answer.upper()


def _resolve_contradiction(store, a: dict, b: dict, *, dry_run: bool,
                           result: ReflectResult) -> None:
    older, newer = (a, b) if a["created_at"] <= b["created_at"] else (b, a)
    result.contradictions += 1
    result.actions.append(
        f"contradiction: retire {older['id'][:8]} in favor of {newer['id'][:8]}")
    if dry_run:
        return
    store.update(older["id"], status="retired", veracity="false",
                 superseded_by=newer["id"])
    store.record_validation(older["id"], validator="reflect",
                            action="veracity_false",
                            note=f"contradicted by {newer['id']}")


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


# Synthesis (Generative-Agents-style reflection): cluster related episodic
# memories and abstract ONE higher-order rule from each cluster. The cluster
# band is [floor, dedup_threshold) cosine — related but not duplicates (those
# are merged). Sources are kept (their specifics still matter; recall favours
# the more general, higher-importance synthesis) and marked 'synthesized' so a
# second pass over the same cluster is a no-op.
_SYNTHESIS_FLOOR = 0.55
_SYNTHESIS_MIN_CLUSTER = 3
_SYNTHESIS_MAX_CLUSTERS = 3   # bound LLM calls per reflect run

# The synthesis prompt now lives as the editable `memory-reflect-synthesis`
# surface (lib/prompts/surfaces/memory.py::_DEFAULT_BODY_SYNTHESIS);
# `_llm_synthesis` wires the clustered entries into its `{{entries}}` slot.


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


def _unsynthesized(store, episodic: list[dict]) -> list[dict]:
    """Episodic rows not already folded into a synthesis. Skipping members
    that carry a 'synthesized' validation is what keeps the pass idempotent —
    a re-run over the same cluster finds no fresh members."""
    return [m for m in episodic
            if not _validation_action_counts(store, m["id"]).get("synthesized")]


def _cluster_neighbours(rows: list[dict], cos, i: int,
                        clustered: set[str], dedup_t: float) -> list[dict]:
    """The seed at `i` plus every not-yet-clustered row whose cosine to it
    sits in the synthesis band [`_SYNTHESIS_FLOOR`, dedup_threshold)."""
    members = [rows[i]]
    for j, other in enumerate(rows):
        if i == j or other["id"] in clustered:
            continue
        if _SYNTHESIS_FLOOR <= float(cos[i][j]) < dedup_t:
            members.append(other)
    return members


def _synthesis_clusters(rows: list[dict], embedder) -> list[list[dict]]:
    """Greedy clusters of related-but-distinct rows (each at least
    `_SYNTHESIS_MIN_CLUSTER` members, no row in two clusters)."""
    cos = _cosine_matrix(embedder, [_doc_text(m) for m in rows])
    if cos is None:
        return []
    dedup_t = settings.agent_memory.dedup_cosine_threshold
    clustered: set[str] = set()
    clusters: list[list[dict]] = []
    for i, seed in enumerate(rows):
        if seed["id"] in clustered:
            continue
        members = _cluster_neighbours(rows, cos, i, clustered, dedup_t)
        if len(members) >= _SYNTHESIS_MIN_CLUSTER:
            clustered.update(m["id"] for m in members)
            clusters.append(members)
    return clusters


def _llm_synthesis(llm, members: list[dict]) -> "dict | None":
    """Ask the LLM to abstract one rule from a cluster. None when it declines
    (NONE), the output is unparseable, or the draft is too thin to keep."""
    from lib.prompts import render_surface
    from lib.prompts.surfaces.memory import SYNTHESIS_SURFACE_ID
    entries = "\n\n".join(f"[{i + 1}] {_doc_text(m)[:600]}"
                          for i, m in enumerate(members))
    answer = llm.complete(render_surface(SYNTHESIS_SURFACE_ID, {"entries": entries}),
                          max_tokens=400, surface_id=SYNTHESIS_SURFACE_ID)
    if not answer or answer.strip().upper().startswith("NONE"):
        return None
    draft = _extract_json_object(answer)
    if not draft:
        return None
    title = str(draft.get("title") or "").strip()
    body = str(draft.get("body") or "").strip()
    if len(title) < 10 or len(body) < 60:
        return None
    return {"title": title[:120], "body": body[:2000]}


def _write_synthesis(store, members: list[dict], draft: dict, *,
                     dry_run: bool, result: ReflectResult,
                     embedder=None) -> None:
    """Write the synthesised rule as a new episodic memory and stamp each
    source with a 'synthesized' validation (the idempotency marker)."""
    from lib.memory.models import MemoryInput
    result.synthesized += 1
    result.actions.append(
        f"synthesize {len(members)} -> «{draft['title'][:48]}»")
    if dry_run:
        return
    scopes = {m["scope"] for m in members}
    topic_scope = scopes.pop() if len(scopes) == 1 else "global"
    importance = min(1.0, max(m["importance"] for m in members) + 0.05)
    mid = store.remember(MemoryInput(
        body=draft["body"], title=draft["title"], kind="lesson",
        tier="episodic", status="active",
        scope=topic_scope, tags=["synthesis"], importance=importance))
    for m in members:
        store.record_validation(m["id"], validator="reflect",
                                action="synthesized", note=f"into {mid}")
    _record_synthesis_topic(store, members, draft, mid, topic_scope,
                            embedder, result)


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


def _synthesize(store, episodic: list[dict], embedder, llm, *,
                dry_run: bool, result: ReflectResult) -> None:
    """Cluster related episodic rows and abstract a higher-order rule from
    each. A no-op without both an embedder (to cluster) and an LLM (to
    abstract), or when `synthesis_enabled` is off — synthesis is the one
    consolidation step that genuinely needs both models."""
    if (not settings.agent_memory.synthesis_enabled
            or embedder is None or embedder.model_id is None or llm is None):
        return
    candidates = _unsynthesized(store, episodic)
    if len(candidates) < _SYNTHESIS_MIN_CLUSTER:
        return
    clusters = _synthesis_clusters(candidates, embedder)[:_SYNTHESIS_MAX_CLUSTERS]
    for members in clusters:
        draft = _llm_synthesis(llm, members)
        if draft is not None:
            _write_synthesis(store, members, draft,
                             dry_run=dry_run, result=result,
                             embedder=embedder)


# Digest (the structure layer): roll each scope's most important episodic
# memories into ONE maintained briefing, refreshed in place via supersede.
# Distinct from synthesis (which abstracts a rule from one tight cluster);
# the digest summarises the whole scope and is injected as standing context,
# never a per-query recall hit. Needs only an LLM. Gated on `digest_enabled`.
_DIGEST_MIN_SOURCES = 3

# The digest prompt now lives as the editable `memory-reflect-digest` surface
# (lib/prompts/surfaces/memory.py::_DEFAULT_BODY_DIGEST); `_llm_digest` wires the
# scope's top memories into its `{{entries}}` slot.


def _age_days(stamp: "str | None") -> float:
    """Whole/fractional days since an ISO timestamp; +inf when absent or
    unparseable (so a missing stamp always trips the age gate)."""
    if not stamp:
        return float("inf")
    try:
        from datetime import datetime
        delta = datetime.now() - datetime.fromisoformat(stamp)
        return delta.total_seconds() / 86400.0
    except ValueError:
        return float("inf")


def _digest_sources(episodic: list[dict], scope: str, cap: int) -> list[dict]:
    """A scope's episodic rows, highest-importance first, capped. Synthesis
    cards (importance = cluster max + 0.05) naturally sort to the front."""
    rows = [m for m in episodic if m["scope"] == scope]
    rows.sort(key=lambda m: m.get("importance") or 0.0, reverse=True)
    return rows[:cap]


def _digest_is_current(existing: "dict | None", sources: list[dict]) -> bool:
    """True when the existing digest needn't be regenerated yet: younger than
    `digest_max_age_days` AND fewer than `digest_min_new_cards` sources are
    newer than it. None (no digest) is never current."""
    if existing is None:
        return False
    cfg = settings.agent_memory
    stamp = existing.get("updated_at") or existing.get("created_at")
    if _age_days(stamp) >= cfg.digest_max_age_days:
        return False
    fresh = sum(1 for m in sources if (m.get("created_at") or "") > (stamp or ""))
    return fresh < cfg.digest_min_new_cards


def _llm_digest(llm, sources: list[dict]) -> "dict | None":
    """Ask the LLM for one scope-level briefing. None when it declines
    (NONE), the output is unparseable, or the draft is too thin to keep."""
    from lib.prompts import render_surface
    from lib.prompts.surfaces.memory import DIGEST_SURFACE_ID
    entries = "\n\n".join(f"[{i + 1}] {_doc_text(m)[:400]}"
                          for i, m in enumerate(sources))
    answer = llm.complete(render_surface(DIGEST_SURFACE_ID, {"entries": entries}),
                          max_tokens=600, surface_id=DIGEST_SURFACE_ID)
    if not answer or answer.strip().upper().startswith("NONE"):
        return None
    draft = _extract_json_object(answer)
    if not draft:
        return None
    title = str(draft.get("title") or "").strip()
    body = str(draft.get("body") or "").strip()
    if len(title) < 6 or len(body) < 80:
        return None
    return {"title": title[:120], "body": body[:2000]}


def _write_digest(store, scope: str, existing: "dict | None", draft: dict,
                  sources: list[dict], *, dry_run: bool,
                  result: ReflectResult) -> None:
    """Write (or supersede) the scope's digest. Importance is fixed high so
    the row is never a decay/forget target; it is excluded from recall and
    the lifecycle regardless, this just keeps any future quality read sane."""
    from lib.memory.models import MemoryInput
    result.digests += 1
    verb = "refresh" if existing else "create"
    result.actions.append(
        f"digest[{scope}] {verb} <- {len(sources)} src «{draft['title'][:40]}»")
    if dry_run:
        return
    payload = MemoryInput(
        body=draft["body"], title=draft["title"], kind="digest",
        tier="episodic", status="active", scope=scope, tags=["digest"],
        importance=0.8)
    if existing is None:
        store.remember(payload)
    else:
        store.supersede(existing["id"], payload)


def _synthesize_digest(store, episodic: list[dict], llm, *,
                       dry_run: bool, result: ReflectResult) -> None:
    """Maintain one digest per scope. A no-op without an LLM or when
    `digest_enabled` is off. `episodic` is already digest-free (reflect filters
    them out), so a scope's own digest never feeds its regeneration."""
    if not settings.agent_memory.digest_enabled or llm is None:
        return
    cap = settings.agent_memory.digest_max_sources
    for scope in sorted({m["scope"] for m in episodic}):
        sources = _digest_sources(episodic, scope, cap)
        if len(sources) < _DIGEST_MIN_SOURCES:
            continue
        existing = store.get_digest(scope)
        if _digest_is_current(existing, sources):
            continue
        draft = _llm_digest(llm, sources)
        if draft is not None:
            _write_digest(store, scope, existing, draft, sources,
                          dry_run=dry_run, result=result)


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


def _dedup_and_judge(store, working: list[dict], pool: list[dict],
                     embedder, llm, *, dry_run: bool,
                     result: ReflectResult) -> set[str]:
    """Run dedup + contradiction passes; return ids consumed by a merge."""
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
        elif sim >= _GRAY_ZONE_FLOOR and llm is not None:
            if _llm_says_contradiction(llm, newcomer, other):
                _resolve_contradiction(store, newcomer, other,
                                       dry_run=dry_run, result=result)
                older = min(newcomer, other, key=lambda m: m["created_at"])
                consumed.add(older["id"])
    return consumed


def reflect(store, embedder=None, llm=None, *,
            dry_run: bool = False) -> ReflectResult:
    """One consolidation pass. Idempotent: a second run over an already-
    consolidated store finds nothing to do."""
    from datetime import datetime
    result = ReflectResult(dry_run=dry_run)
    working = store.list_memories(tier="working", status="active",
                                  include_tests=True, limit=10_000)
    # Digests are episodic by storage but sit outside the learning lifecycle:
    # they must not be deduped, synthesised, decayed or forgotten against real
    # memories, so they're excluded from the working set every stage sees.
    episodic = [m for m in store.list_memories(tier="episodic", status="active",
                                               include_tests=True, limit=10_000)
                if m["kind"] != "digest"]
    result.examined = len(working)
    if working:
        consumed = _dedup_and_judge(store, working, working + episodic,
                                    embedder, llm,
                                    dry_run=dry_run, result=result)
        now = datetime.now().isoformat()
        for mem in working:
            if mem["id"] not in consumed:
                _promote(store, mem, now_field=now,
                         dry_run=dry_run, result=result)
    _forget_stale(store, episodic, dry_run=dry_run, result=result)
    _score_pending(store, dry_run=dry_run)
    _decay_chronically_ignored(store, episodic, dry_run=dry_run, result=result)
    _flag_stale_references(store, working + episodic,
                           dry_run=dry_run, result=result)
    _synthesize(store, episodic, embedder, llm, dry_run=dry_run, result=result)
    # After synthesis so a freshly written card can feed the same scope's
    # digest this pass; only needs an LLM (no embedder).
    _synthesize_digest(store, episodic, llm, dry_run=dry_run, result=result)
    _embed_episodic(store, embedder, dry_run=dry_run, result=result)
    # After embed so freshly written synthesis/promotion rows are linkable.
    _harvest_edges(store, embedder, dry_run=dry_run, result=result)
    log.write("memory_reflected", examined=result.examined,
              merged=result.merged, promoted=result.promoted,
              embedded=result.embedded, forgotten=result.forgotten,
              decayed=result.decayed, synthesized=result.synthesized,
              edges=result.edges, topics=result.topics, digests=result.digests,
              flagged_stale=result.flagged_stale, ref_renames=result.ref_renames,
              dry_run=dry_run)
    return result


__all__ = ["reflect", "ReflectResult", "stale_embedding_todo"]

# Public alias so callers (e.g. store.py) can import the staleness helper
# without duplicating the logic.  The private name stays for internal calls.
stale_embedding_todo = _stale_embedding_todo
