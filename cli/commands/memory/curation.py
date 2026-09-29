"""`regin memory` curation: approve/forget/restore/supersede, eval and
curate-apply."""

from __future__ import annotations

import json
from typing import Optional

import typer

from ._common import _curate_log, memory_app
from .authoring import _reject_titleless_lesson


@memory_app.command("approve")
def cmd_approve(memory_id: str = typer.Argument(...)) -> None:
    import lib.memory as memory
    if not memory.update(memory_id, status="active"):
        print("not found")
        raise typer.Exit(1)
    memory.get_store().record_validation(
        memory_id, validator="user", action="approved")
    print(f"approved {memory_id}")


@memory_app.command("forget")
def cmd_forget(memory_id: str = typer.Argument(...)) -> None:
    import lib.memory as memory
    if not memory.forget(memory_id):
        print("not found")
        raise typer.Exit(1)
    print(f"forgot {memory_id}")


@memory_app.command("restore")
def cmd_restore(memory_id: str = typer.Argument(...)) -> None:
    """Bring a retired memory back to active — the inverse of `retire` and
    the non-destructive counterpart to `supersede`. Reactivates the row and
    clears its supersede link so recall can surface it again. A hard
    `forget` cannot be undone this way; the row no longer exists."""
    import lib.memory as memory
    if not memory.restore(memory_id):
        print("not found")
        raise typer.Exit(1)
    print(f"restored {memory_id}")


@memory_app.command("supersede")
def cmd_supersede(
    old_id: str = typer.Argument(..., help="Memory to retire and chain from"),
    body: str = typer.Option(..., "--body", "-b", help="New memory body"),
    title: Optional[str] = typer.Option(None, "--title"),
    kind: Optional[str] = typer.Option(None, "--kind",
                                       help="default: inherit from old"),
    scope: Optional[str] = typer.Option(None, "--scope",
                                        help="default: inherit from old"),
    importance: Optional[float] = typer.Option(None, "--importance"),
    tags: Optional[str] = typer.Option(
        None, "--tags", help="comma-separated; default: inherit from old"),
) -> None:
    """Replace OLD_ID with a new memory, preserving the supersede chain.

    Unlike `forget` (a hard delete that destroys the row and its
    provenance), the old memory is kept as status=retired with
    superseded_by set — hidden from recall but auditable. Use this when
    correcting or refreshing a memory; use `forget` only for genuine junk.
    """
    import lib.memory as memory
    old = memory.get_store().get_dict(old_id)
    if old is None:
        print("not found")
        raise typer.Exit(1)
    new = memory.MemoryInput(
        body=body,
        kind=kind or old["kind"],
        title=title,
        scope=scope or old["scope"],
        tags=([t.strip() for t in tags.split(",") if t.strip()]
              if tags is not None else list(old.get("tags") or [])),
        importance=(importance if importance is not None
                    else old.get("importance", 0.5)),
        source_trace_id=old.get("source_trace_id"),
        source_span_id=old.get("source_span_id"),
    )
    _reject_titleless_lesson(new.kind, new.title)
    new_id = memory.supersede(old_id, new)
    inherited = memory.get_store().authoritative_topics_of(new_id)
    suffix = (f"; inherited topic(s): {', '.join(inherited)}"
              if inherited else "")
    print(f"superseded {old_id[:8]} -> {new_id[:8]} (old retired, chained){suffix}")


def _print_eval_verdict(v) -> None:
    mark = "PASS" if v.passed else "FAIL"
    rank = f"#{v.hit_rank}" if v.hit_rank else "—"
    matched = v.matched_title or v.top_title or "(no hit)"
    print(f"  [{mark}] rank={rank:3s} {matched}")
    if not v.passed:
        print(f"         query: {v.query[:90]}")


@memory_app.command("eval")
def cmd_eval(
    cases_path: str = typer.Argument(
        ..., help="JSONL case file: {query, expect_any, note?}"),
    top_k: int = typer.Option(5, "--top-k"),
    mode: str = typer.Option("auto", "--mode",
                             help="auto | hybrid | fts (fts = no model load)"),
    json_out: bool = typer.Option(False, "--json"),
) -> None:
    """Score recall quality against a JSONL case set. Each case PASSES at k
    when any top-k hit's title+body contains an `expect_any` substring.
    Prints per-case verdicts plus hit@1/hit@k/MRR; exits 1 when hit@k < 1.0
    so CI can gate on recall regressions."""
    from lib.memory.evaluate import evaluate_recall, load_cases
    cases = load_cases(cases_path)
    report = evaluate_recall(cases, top_k=top_k, mode=mode)
    if json_out:
        print(json.dumps(report.to_dict(), indent=2))
    else:
        for v in report.verdicts:
            _print_eval_verdict(v)
        print(f"\nhit@1={report.hit_at_1:.3f}  hit@{top_k}={report.hit_at_k:.3f}  "
              f"MRR={report.mrr:.3f}  ({report.passed}/{report.total})")
    if report.hit_at_k < 1.0:
        raise typer.Exit(1)


# ---------------------------------------------------------------------------
# curate-apply: apply a verified curation plan from the memory-curate skill.
# The skill (agentic subagents) produces the judgment; this command is the
# deterministic, audited, reversible apply step. Plan shape:
#   {"actions": [
#     {"op": "retag",   "id": "<id8>", "tier": "core|narrow", "reason": ...},
#     {"op": "retire",  "id": "<id8>", "winner": "<id8>?", "reason": ...},
#     {"op": "merge",   "into": "<id8>", "ids": ["<id8>", ...], "reason": ...},
#     {"op": "rewrite", "sources": ["<id8>", ...], "title": ..., "body": ...,
#                       "scope": ...?, "reason": ...},
#     {"op": "forget",  "id": "<id8>", "reason": ...}   # HARD delete
#   ]}
# All ids may be 8-char prefixes (recall/inject blocks render prefixes).
# ---------------------------------------------------------------------------

_VALID_OPS = {"retag", "retire", "forget", "merge", "rewrite"}


def _index_by_prefix(store) -> dict:
    """Map 8-char id prefix -> [full ids] across every memory (all statuses)."""
    rows = store.list_memories(include_tests=True, limit=100000)
    idx: dict[str, list[str]] = {}
    for r in rows:
        idx.setdefault(r["id"][:8], []).append(r["id"])
    return idx


def _resolve(prefix: Optional[str], idx: dict) -> str:
    """Resolve an 8-char prefix (or full id) to exactly one full id."""
    if not prefix:
        raise KeyError("missing id")
    full = idx.get(prefix[:8])
    if not full:
        raise KeyError(f"no memory matches id '{prefix}'")
    if len(full) > 1:
        raise KeyError(f"ambiguous id prefix '{prefix}' ({len(full)} matches)")
    return full[0]


def _action_ids(a: dict) -> list[str]:
    """Every id an action references (for pre-flight resolution)."""
    op = a.get("op")
    if op in ("retag", "retire", "forget"):
        ids = [a.get("id")]
        if op == "retire" and a.get("winner"):
            ids.append(a["winner"])
        return ids
    if op == "merge":
        return [a.get("into"), *(a.get("ids") or [])]
    if op == "rewrite":
        return list(a.get("sources") or [])
    return []


def _validate_plan(actions: list, idx: dict) -> list[str]:
    """Resolve every id and op BEFORE mutating anything — fail fast."""
    errors = []
    for i, a in enumerate(actions):
        if a.get("op") not in _VALID_OPS:
            errors.append(f"action {i}: bad op {a.get('op')!r}")
            continue
        for x in _action_ids(a):
            try:
                _resolve(x, idx)
            except KeyError as exc:
                errors.append(f"action {i} ({a.get('op')}): {exc}")
    return errors


# CORE/NARROW is the curation *quality grade* — regin has no column for it
# (the `tier` column is the lifecycle: working/episodic/digest), so a grade
# maps to an importance band. An explicit `importance` always wins; only a
# real lifecycle value is written to `tier`.
_GRADE_IMPORTANCE = {"core": 0.8, "narrow": 0.45, "lowq": 0.2}
_LIFECYCLE_TIERS = {"working", "episodic", "digest"}


def _do_retag(store, a: dict, idx: dict, dry: bool) -> str:
    mid = _resolve(a["id"], idx)
    fields = {}
    grade = (a.get("tier") or "").lower()
    if grade in _LIFECYCLE_TIERS:
        fields["tier"] = grade
    elif grade in _GRADE_IMPORTANCE:
        fields["importance"] = _GRADE_IMPORTANCE[grade]
    if a.get("importance") is not None:
        fields["importance"] = a["importance"]
    if not dry and fields:
        store.update(mid, **fields)
        store.record_validation(mid, validator="curate", action="retag",
                                note=a.get("reason"))
    return f"{mid[:8]} -> {fields or '(no-op)'}"


def _do_retire(store, a: dict, idx: dict, dry: bool) -> str:
    mid = _resolve(a["id"], idx)
    winner = _resolve(a["winner"], idx) if a.get("winner") else None
    if not dry:
        store.update(mid, status="retired", superseded_by=winner)
        store.record_validation(mid, validator="curate", action="curate_retire",
                                note=a.get("reason"))
    return f"{mid[:8]}" + (f" -> {winner[:8]}" if winner else " (no successor)")


def _do_forget(store, a: dict, idx: dict, dry: bool) -> str:
    mid = _resolve(a["id"], idx)
    if not dry:
        store.forget(mid)
    return f"{mid[:8]} (HARD delete, not reversible)"


def _do_merge(store, a: dict, idx: dict, dry: bool) -> str:
    keeper = _resolve(a["into"], idx)
    losers = [_resolve(x, idx) for x in (a.get("ids") or [])]
    if not dry:
        for lz in losers:
            store.update(lz, status="retired", superseded_by=keeper)
            store.record_validation(lz, validator="curate", action="merged",
                                    note=f"near-duplicate of {keeper}")
    return f"{[l[:8] for l in losers]} -> {keeper[:8]}"


def _synth_input(a: dict, base: dict):
    """Build the MemoryInput for a synthesis rewrite, inheriting unset
    fields (kind/scope/tags/provenance) from the first source."""
    import lib.memory as memory
    return memory.MemoryInput(
        body=a["body"], title=a.get("title"),
        kind=base["kind"], scope=a.get("scope") or base["scope"],
        tags=a.get("tags") or list(base.get("tags") or []),
        importance=a.get("importance", 0.6),
        source_trace_id=base.get("source_trace_id"))


def _do_rewrite(store, a: dict, idx: dict, dry: bool) -> str:
    import lib.memory as memory
    sources = [_resolve(x, idx) for x in (a.get("sources") or [])]
    shown = [s[:8] for s in sources]
    if dry:
        return f"{shown} -> NEW {a.get('title') or '(untitled)'!r}"
    new_id = memory.supersede(sources[0], _synth_input(a, store.get_dict(sources[0])))
    for s in sources[1:]:
        store.update(s, status="retired", superseded_by=new_id)
        store.record_validation(s, validator="curate", action="synthesized",
                                note=f"folded into {new_id}")
    return f"{shown} -> {new_id[:8]}"


_CURATE_OPS = {"retag": _do_retag, "retire": _do_retire, "forget": _do_forget,
               "merge": _do_merge, "rewrite": _do_rewrite}


@memory_app.command("curate-apply")
def cmd_curate_apply(
    plan_path: str = typer.Argument(..., help="plan.json from memory-curate"),
    apply: bool = typer.Option(False, "--apply",
                               help="execute (default: dry-run)"),
) -> None:
    """Apply a verified curation plan from the `memory-curate` skill.

    The skill's agentic subagents produce the judgment (grade/dedup/
    synthesize/verify); this command is the deterministic apply step.
    Ops: retag (tier/importance), retire (soft, reversible, chained),
    merge (retire dups -> keeper), rewrite (synthesis: one new memory
    supersedes its sources), forget (HARD delete — genuine junk only).

    Ids may be 8-char prefixes. Dry-run by default; --apply executes.
    Every id is resolved up front, so a bad plan aborts before any write.
    All ops except forget are reversible via `regin memory restore`.
    """
    import lib.memory as memory
    with open(plan_path) as fh:
        plan = json.load(fh)
    actions = plan.get("actions") or []
    store = memory.get_store()
    idx = _index_by_prefix(store)

    errors = _validate_plan(actions, idx)
    if errors:
        print(f"plan invalid ({len(errors)} error(s)); nothing applied:")
        for e in errors:
            print(f"  ERROR {e}")
        raise typer.Exit(1)

    dry = not apply
    print(f"{'DRY-RUN — ' if dry else ''}{len(actions)} action(s) "
          f"from {plan_path}\n")
    for a in actions:
        msg = _CURATE_OPS[a["op"]](store, a, idx, dry)
        print(f"  [{a['op']:7s}] {msg}")
    if dry:
        print("\nDry-run only. Re-run with --apply to execute.")
        return
    _curate_log.write("curate_plan_applied", actions=len(actions),
                      plan=plan_path)
    print(f"\nApplied {len(actions)} action(s). "
          f"Soft ops reversible via `regin memory restore`.")
