"""Mechanical `reflect()` primitives: the result record, similarity, per-row
merge/promote/retire actions, dedup, embedding and edge harvest."""

from __future__ import annotations

import difflib
import hashlib
import json
import math
import re
from dataclasses import dataclass, field as dc_field

from lib.settings import settings


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
