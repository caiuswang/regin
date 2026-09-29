"""Dream: the single agentic LLM stage of `reflect()` — pack, plan, apply."""

from __future__ import annotations

import json
import re
import statistics
from dataclasses import dataclass, field as dc_field

from lib.activity_log import get_activity_logger
from lib.settings import settings
from lib.memory.reflect_ops import (
    ReflectResult,
    _doc_text,
    _drop,
    _extract_json_object,
    _hold,
    _merge_pair,
    _promote,
    _record_synthesis_topic,
    _retire_older,
    _time_ordered,
)
from lib.memory.reflect_decay import (
    _referenced_paths, _validation_action_counts,
)

log = get_activity_logger("memory")


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
