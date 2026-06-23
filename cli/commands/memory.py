"""`regin memory` — CLI surface over the cross-session agent memory.

Thin wrappers around the `lib.memory` facade: recall/list/stats for
inspection, reflect for consolidation, distill for proposing memories
from a finished session, approve/forget for curation. The engine itself
is documented under *Agent Memory* in `ARCHITECTURE.md`.
"""

from __future__ import annotations

import json
from typing import Optional

import typer

memory_app = typer.Typer(name="memory", help="Cross-session agent memory",
                         no_args_is_help=True)


def _print_memory_line(m: dict) -> None:
    title = m.get("title") or (m["body"][:60] + "…" if len(m["body"]) > 60
                               else m["body"])
    # Append compact suffix with importance and use_count (recall_count) if present
    suffix = ""
    importance = m.get("importance")
    recall_count = m.get("recall_count")
    if importance is not None or recall_count is not None:
        parts = []
        if importance is not None:
            parts.append(f"imp={importance:.2f}")
        if recall_count is not None:
            parts.append(f"use={recall_count}")
        if parts:
            suffix = " " + " ".join(parts)
    print(f"  {m['id'][:8]}  {m['tier']:8s} {m['status']:8s} "
          f"{m['kind']:10s} {m['scope']:14s} {title}{suffix}")


@memory_app.command("recall")
def cmd_recall(
    query: str = typer.Argument(..., help="What to recall experience about"),
    top_k: int = typer.Option(5, "--top-k"),
    scope: Optional[str] = typer.Option(None, "--scope", help="e.g. repo:regin"),
    fts_only: bool = typer.Option(False, "--fts-only",
                                  help="Skip the dense leg + rerank"),
    json_out: bool = typer.Option(False, "--json"),
) -> None:
    import lib.memory as memory
    hits = memory.recall(query, top_k=top_k, scope=scope,
                         mode="fts" if fts_only else "auto")
    if json_out:
        print(json.dumps([{**h.memory, "score": h.score,
                           "score_kind": h.score_kind} for h in hits], indent=2))
        return
    if not hits:
        print("no stored experience matched")
        raise typer.Exit(1)
    for h in hits:
        print(f"[{h.score:.3f} {h.score_kind}]", end="")
        _print_memory_line(h.memory)


@memory_app.command("list")
def cmd_list(
    tier: Optional[str] = typer.Option(None, "--tier"),
    status: Optional[str] = typer.Option(None, "--status"),
    kind: Optional[str] = typer.Option(None, "--kind"),
    scope: Optional[str] = typer.Option(None, "--scope",
                                        help="e.g. repo:regin or global"),
    include_tests: bool = typer.Option(False, "--include-tests"),
    limit: int = typer.Option(50, "--limit"),
    sort: str = typer.Option("recent", "--sort",
                             help="Sort by: recent (default), use (recall_count), importance"),
    json_out: bool = typer.Option(False, "--json"),
) -> None:
    import lib.memory as memory
    rows = memory.get_store().list_memories(
        tier=tier, status=status, kind=kind, scope=scope,
        include_tests=include_tests, limit=limit)

    # Client-side sorting
    if sort == "use":
        rows = sorted(rows, key=lambda m: m.get("recall_count", 0), reverse=True)
    elif sort == "importance":
        rows = sorted(rows, key=lambda m: m.get("importance", 0), reverse=True)
    # "recent" is the default (list_memories already orders by updated_at desc)

    if json_out:
        print(json.dumps(rows, indent=2))
        return
    if not rows:
        print("no memories")
        return
    for m in rows:
        _print_memory_line(m)


@memory_app.command("stats")
def cmd_stats() -> None:
    import lib.memory as memory
    print(json.dumps(memory.stats(), indent=2))


def _merged_graph():
    """Repo graph + global meta-roots — the topic id space `--topic` validates
    against, so the meta-roots (`skills`, `preferences`, …) are fileable."""
    from pathlib import Path
    from lib.settings import settings
    from lib.topics.graph_io import load_authoritative_graph
    from lib.topics.meta_roots import merge_meta_roots
    return merge_meta_roots(
        load_authoritative_graph(str(Path(settings.project_root).resolve())))


@memory_app.command("remember")
def cmd_remember(
    body: str = typer.Argument(..., help="The memory body (fact / lesson / "
                               "preference)"),
    kind: str = typer.Option("lesson", "--kind",
                             help="lesson | gotcha | preference | fact | "
                                  "procedure"),
    title: Optional[str] = typer.Option(None, "--title"),
    scope: str = typer.Option("global", "--scope",
                              help="global or repo:<name>"),
    topic: Optional[str] = typer.Option(
        None, "--topic", help="Authoritative topic node id to file under — "
        "incl. the global meta-roots (skills, preferences, …)"),
    tags: Optional[str] = typer.Option(None, "--tags",
                                       help="comma-separated"),
    importance: float = typer.Option(0.5, "--importance"),
) -> None:
    """Create a memory directly, optionally filing it under a topic node.

    The explicit create path the CLI otherwise lacks (`supersede` needs an
    existing id; `distill` derives from a session). `--topic` links the new
    memory to an authoritative topic node — including the global meta-roots
    (`skills` / `preferences`), giving skill- and preference-related memories
    a navigable home in the tree-nav index.
    """
    import lib.memory as memory
    from lib.memory.models import MEMORY_KINDS

    if kind not in MEMORY_KINDS:
        print(f"error: --kind must be one of {', '.join(MEMORY_KINDS)}")
        raise typer.Exit(1)
    if topic is not None and topic not in (_merged_graph().get("topics") or {}):
        print(f"error: no topic node {topic!r} — list roots with "
              f"`index_root` (memory MCP) or pick a meta-root "
              f"(skills, preferences)")
        raise typer.Exit(1)

    mid = memory.remember(
        body, kind=kind, title=title, scope=scope, importance=importance,
        tags=[t.strip() for t in tags.split(",") if t.strip()]
        if tags else None)
    print(f"remembered {mid}")
    if topic is not None:
        memory.get_store().link_authoritative_topic(mid, topic,
                                                    source="manual")
        print(f"  filed under topic {topic}")


@memory_app.command("topic-feedback")
def cmd_topic_feedback(
    limit: int = typer.Option(30, "--limit",
                              help="Recent topic injections to list"),
    json_out: bool = typer.Option(False, "--json"),
) -> None:
    """The topic-routing feedback loop: per-topic relevance verdicts (from the
    `InjectedRelated` grade aspect) and which routes are now suppressed, plus
    the most recent `<topic_context>` injections."""
    import lib.memory as memory
    store = memory.get_store()
    summary = store.topic_relevance_summary()
    recent = store.list_topic_injections(limit=limit)
    if json_out:
        print(json.dumps({"summary": summary, "recent": recent}, indent=2))
        return
    if not summary:
        print("no topic injections recorded yet "
              "(needs agent_memory.topic_route_inject on + injected prompts)")
        return
    print("per-topic relevance (actionable first):")
    for s in summary:
        print(f"  {s['status']:10s} {s['topic_id'][:36]:36s} "
              f"inj={s['injections']:3d} scored={s['scored']:3d} "
              f"fail={s['fails']:3d} rate={s['fail_rate']:.2f}")
    print("\n  status: proposed = over the bar, awaiting your sign-off; "
          "suppressed/allowed = your decision; routing = default")
    print("  decide with: regin memory topic-decide <topic_id> "
          "<suppress|allow|auto>")
    print(f"\nrecent injections (latest {len(recent)}):")
    for r in recent:
        verdict = r["relevance"] or "—"
        print(f"  {r['injected_at'][:19]}  {verdict:14s} "
              f"{r['topic_id'][:36]:36s} {r['session_id'][:8]}")


@memory_app.command("topic-decide")
def cmd_topic_decide(
    topic_id: str = typer.Argument(..., help="Topic id to decide on"),
    decision: str = typer.Argument(
        ..., help="suppress (approve withholding) | allow (pin on / reject "
                  "a proposal) | auto (clear, back to threshold-proposed)"),
) -> None:
    """The human gate over topic suppression. A topic is withheld only after
    you `suppress` it; `allow` pins it on; `auto` clears your decision."""
    import lib.memory as memory
    canon = {"suppress": "suppressed", "suppressed": "suppressed",
             "allow": "allowed", "allowed": "allowed", "auto": "auto"}.get(
                 decision.lower())
    if canon is None:
        print("error: decision must be suppress | allow | auto")
        raise typer.Exit(1)
    memory.get_store().set_topic_decision(topic_id, canon)
    from lib.grader.topic_notify import resolve_proposal
    resolve_proposal(topic_id)  # clear any open inbox proposal for this topic
    print(f"{topic_id}: {'cleared (auto)' if canon == 'auto' else canon}")


def _polarity_flag(positive: bool, negative: bool) -> "int | None":
    if positive == negative:  # both or neither
        return None
    return 1 if positive else -1


@memory_app.command("exemplar-add")
def cmd_exemplar_add(
    target_id: str = typer.Argument(..., help="Memory id (default) or, with "
                                    "--topic, a topic id"),
    query: str = typer.Argument(..., help="The prompt text the case is keyed "
                                "on — recall re-ranks similar future queries"),
    positive: bool = typer.Option(False, "--positive", "-p",
                                  help="A useful case (boost)"),
    negative: bool = typer.Option(False, "--negative", "-n",
                                  help="An unhelpful case (demote)"),
    topic: bool = typer.Option(False, "--topic",
                               help="Treat target_id as a topic route id"),
) -> None:
    """Hand-curate a query exemplar (a 'case'). One of --positive/--negative
    is required. The query is embedded and stored as a manual exemplar; at
    recall time it boosts (or demotes) the target for prompts it resembles."""
    import lib.memory as memory
    polarity = _polarity_flag(positive, negative)
    if polarity is None:
        print("error: pass exactly one of --positive / --negative")
        raise typer.Exit(1)
    store = memory.get_store()
    if topic:
        written = store.add_topic_exemplars(
            "manual", [(target_id, query)], polarity, source="manual")
    else:
        written = store.add_query_exemplars(
            "manual", [(target_id, query)], polarity, source="manual")
    kind = "positive" if polarity > 0 else "negative"
    if written:
        print(f"recorded {kind} exemplar for {target_id}")
    else:
        print("nothing written (no embedder available, or blank query)")


@memory_app.command("exemplar-rm")
def cmd_exemplar_rm(
    target_id: str = typer.Argument(..., help="Memory id or, with --topic, a "
                                    "topic id"),
    positive: bool = typer.Option(False, "--positive", "-p"),
    negative: bool = typer.Option(False, "--negative", "-n"),
    topic: bool = typer.Option(False, "--topic"),
) -> None:
    """Drop curated/captured exemplars for a memory or topic. With neither
    --positive nor --negative, removes both polarities."""
    import lib.memory as memory
    polarity = _polarity_flag(positive, negative)  # None → both
    store = memory.get_store()
    if topic:
        removed = store.remove_topic_exemplars(target_id, polarity)
    else:
        removed = store.remove_exemplars(target_id, polarity)
    print(f"removed {removed} exemplar(s) from {target_id}")


@memory_app.command("exemplar-list")
def cmd_exemplar_list(
    target_id: str = typer.Argument(..., help="Memory id or, with --topic, a "
                                    "topic id"),
    topic: bool = typer.Option(False, "--topic"),
) -> None:
    """List the individual exemplars ('cases') for a memory or topic — their
    id (to revert one), polarity, source, and the query each was built from."""
    import lib.memory as memory
    store = memory.get_store()
    rows = (store.list_topic_exemplars(target_id) if topic
            else store.list_memory_exemplars(target_id))
    if not rows:
        print(f"no exemplars for {target_id}")
        return
    print(f"{len(rows)} exemplar(s) for {target_id}:")
    for r in rows:
        sign = "+" if r["polarity"] > 0 else "-"
        print(f"  #{r['id']:<5d} {sign} {r['source']:6s} "
              f"{(r['query'] or '(query not recorded)')[:72]}")
    print(f"  revert one with: regin memory exemplar-forget <id> "
          f"{'--topic' if topic else ''}".rstrip())


@memory_app.command("exemplar-forget")
def cmd_exemplar_forget(
    exemplar_id: int = typer.Argument(..., help="Exemplar id (from "
                                      "exemplar-list) to delete"),
    topic: bool = typer.Option(False, "--topic",
                               help="The id is a topic exemplar"),
) -> None:
    """Delete one exemplar by id — undo a single mislabel, the fine-grained
    complement to exemplar-rm (which drops a whole polarity)."""
    import lib.memory as memory
    kind = "topic" if topic else "memory"
    ok = memory.get_store().delete_exemplar(exemplar_id, kind)
    print(f"removed exemplar #{exemplar_id}" if ok
          else f"no {kind} exemplar #{exemplar_id}")


@memory_app.command("reflect")
def cmd_reflect(
    dry_run: bool = typer.Option(False, "--dry-run",
                                 help="Report actions without writing"),
) -> None:
    import lib.memory as memory
    result = memory.reflect(dry_run=dry_run)
    print(f"examined={result.examined} merged={result.merged} "
          f"contradictions={result.contradictions} "
          f"promoted={result.promoted} embedded={result.embedded} "
          f"forgotten={result.forgotten} decayed={result.decayed} "
          f"synthesized={result.synthesized} "
          f"flagged_stale={result.flagged_stale}"
          f"{' (dry run)' if dry_run else ''}")
    for action in result.actions:
        print(f"  {action}")


@memory_app.command("distill")
def cmd_distill(
    trace_id: str = typer.Argument(..., help="Session (trace) id to distill"),
    scope: Optional[str] = typer.Option(
        None, "--scope",
        help="Override scope; default resolves the session's own repo"),
    force: bool = typer.Option(
        False, "--force",
        help="Re-run even if memories for this session already exist"),
) -> None:
    """Distil memories from a finished session. Each draft is self-scored
    and dropped, auto-approved (status=active), or queued (status=proposed,
    approve with `regin memory approve`) per the agent_memory thresholds.
    A second run on the same session is skipped by default (idempotency
    guard); pass --force to bypass it for deliberate re-distillation."""
    import lib.memory as memory
    from lib.memory.adapters import resolve_distiller
    from lib.memory.distill import distill_session
    result = distill_session(memory.get_store(), trace_id, scope=scope,
                             llm=resolve_distiller(), force=force)
    if result.skipped_already_distilled:
        print(f"{trace_id}: skipped (already distilled; use --force to re-run)")
        return
    print(f"{trace_id} [{result.source}]: {result.proposed} proposed, "
          f"{result.approved} auto-approved, {result.dropped} dropped, "
          f"{result.reinforced} reinforced")
    for mid in result.memory_ids:
        print(f"  {mid}")


def _link_topics_hard(store, rows, repo_path, dry_run) -> tuple[int, int, int]:
    """Deterministic ref-path heuristic path: one topic per memory whose ref
    file paths appear in its title+body. Returns (linked, refreshed,
    unmatched)."""
    from lib.topics.route import best_topic_for_text

    linked = refreshed = unmatched = 0
    for m in rows:
        text = f"{m.get('title') or ''} {m.get('body') or ''}".strip()
        node_id = best_topic_for_text(repo_path, text)
        if node_id is None:
            unmatched += 1
            continue
        if dry_run:
            print(f"  {m['id'][:8]} -> {node_id}")
            linked += 1
        elif store.link_authoritative_topic(m["id"], node_id, source="route"):
            linked += 1
        else:
            refreshed += 1
    return linked, refreshed, unmatched


def _apply_assignments(store, assignments, dry_run) -> tuple[int, int, int]:
    """Write the agentic `{memory_id: [topic_id, ...]}` map (multi-topic,
    additive). A memory mapped to `[]` counts as unmatched. Returns (linked,
    refreshed, unmatched)."""
    from lib.memory.topic_classify import CLASSIFY_SOURCE

    linked = refreshed = unmatched = 0
    for mid, topics in assignments.items():
        if not topics:
            unmatched += 1
            continue
        for node_id in topics:
            if dry_run:
                print(f"  {mid[:8]} -> {node_id}")
                linked += 1
            elif store.link_authoritative_topic(mid, node_id,
                                                source=CLASSIFY_SOURCE):
                linked += 1
            else:
                refreshed += 1
    return linked, refreshed, unmatched


def _classify_agentic(store, rows, repo_path):
    """Run the agentic classifier over `rows`; exit non-zero (fail-loud) when
    no external agent is reachable instead of degrading to the heuristic."""
    from lib.topics.route import load_authoritative_graph
    from lib.topics.meta_roots import merge_meta_roots
    from lib.memory.adapters import resolve_topic_classifier
    from lib.memory.topic_classify import (classify_memories,
                                           ClassifierUnavailable)

    # Include the global meta-roots so the classifier can route skill-/
    # preference-shaped memories to a precise leaf (e.g. pref-tooling,
    # skill-playwright) and backfill existing ones — the precision complement
    # to distill's deterministic kind→bucket link.
    graph = merge_meta_roots(load_authoritative_graph(repo_path))
    try:
        return classify_memories(rows, graph, resolve_topic_classifier())
    except ClassifierUnavailable as exc:
        print(f"error: {exc}\nConfigure settings.topic_proposal_external_agents"
              ", or pass --hard-match for the deterministic heuristic.")
        raise typer.Exit(1)


@memory_app.command("link-topics")
def cmd_link_topics(
    repo: Optional[str] = typer.Option(
        None, "--repo",
        help="Repo whose topic.json graph to match against; "
             "default: project root"),
    scope: Optional[str] = typer.Option(
        None, "--scope", help="Only link memories in this scope"),
    limit: int = typer.Option(2000, "--limit"),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Report matches without writing links"),
    hard_match: bool = typer.Option(
        False, "--hard-match",
        help="Use the deterministic ref-path heuristic instead of the LLM "
             "(single topic, no agent required)"),
) -> None:
    """Link active memories to authoritative topic.json nodes.

    Agentic by default: an LLM reads each memory's *subject* and returns the
    genuinely related topic node(s) — zero, one, or several (multi-topic),
    linked with source='agent'. Fail-loud — if no external agent is configured
    it exits non-zero rather than silently degrading. Pass `--hard-match` for
    the deterministic ref-path heuristic (single topic, source='route').
    Additive and idempotent: a re-run refreshes existing links and never drops
    manual/reflect ones."""
    from pathlib import Path
    import lib.memory as memory
    from lib.settings import settings

    repo_path = Path(repo).resolve() if repo else Path(
        settings.project_root).resolve()
    store = memory.get_store()
    rows = store.list_memories(status="active", scope=scope, limit=limit)
    if hard_match:
        linked, refreshed, unmatched = _link_topics_hard(
            store, rows, repo_path, dry_run)
    else:
        assignments = _classify_agentic(store, rows, repo_path)
        linked, refreshed, unmatched = _apply_assignments(
            store, assignments, dry_run)
    verb = "would link" if dry_run else "linked"
    print(f"{verb}={linked} refreshed={refreshed} unmatched={unmatched} "
          f"(of {len(rows)} active)")


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
    new_id = memory.supersede(old_id, new)
    print(f"superseded {old_id[:8]} -> {new_id[:8]} (old retired, chained)")


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
