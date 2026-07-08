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

from lib.activity_log import get_activity_logger

memory_app = typer.Typer(name="memory", help="Cross-session agent memory",
                         no_args_is_help=True)

_curate_log = get_activity_logger("memory")


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


def _resolve_subsystem_node(graph: dict, subsystem: Optional[str], task: str,
                            repo_path) -> Optional[str]:
    """The subsystem topic node for a task-scoped recall: a valid `--subsystem`
    override if given, else the node the task text keyword-routes to (or None
    when it routes nowhere)."""
    if subsystem and subsystem in (graph.get("topics") or {}):
        return subsystem
    from lib.topics.route import match_topic
    routed = match_topic(repo_path, task)
    return routed.get("id") if routed else None


def _task_subtree_memories(store, graph: dict, node_id: Optional[str],
                           top_k: int, scope: Optional[str]) -> list[dict]:
    """Structure-first retrieval: the subsystem subtree's active memories
    (importance/recall ranked — NOT query similarity), hydrated and capped to
    `top_k`. [] for an unrouted task. This is the leg that makes the topic tree
    the retriever, so a stage-language task description can't filter the right
    memories out of the candidate pool."""
    if not node_id:
        return []
    from lib.topics.tree import subtree_ids
    ids = store.memories_for_topic_subtree(subtree_ids(graph, node_id),
                                           scope=scope)
    return [d for d in (store.get_dict(mid) for mid in ids[:top_k]) if d]


def _record_task_offered(store, session: str, mems: list[dict],
                         task: str) -> None:
    """Log the surfaced memories as *offered* (an injection event, no
    recall_count bump) — the same exposure-without-usefulness record
    `goal_preflight.record_offered` writes. This is what folds structure-first
    task recall into the engagement-grading loop: without it, these hits are
    never scored, so we could never compare its engaged-rate against the
    flat-recall baseline. Best-effort — recall must never break on it."""
    try:
        ids = [m["id"] for m in mems if m.get("id")]
        if ids:
            store.record_injections(session, ids, query=task)
    except Exception:
        pass


def _hit_summaries(mems: list[dict]) -> list[dict]:
    """Compact, machine-readable view of the surfaced memories — the shared
    source for both the `memory.recall.task` span attributes and the `--json`
    output, so the two never drift."""
    return [{"id": m.get("id"), "kind": m.get("kind"),
             "title": m.get("title"), "scope": m.get("scope")} for m in mems]


def _render_block(mems: list[dict]) -> str:
    """The default `<recalled_experience>` text block a spawner pastes atop a
    sub-task prompt. `(no filed experience)` when the subtree was empty."""
    from lib.memory.skill_experience import format_memory_line
    body = [format_memory_line(m) for m in mems] or ["(no filed experience)"]
    return "\n".join([
        "<recalled_experience>",
        "Experience recalled from regin's past sessions. It may be stale —",
        "verify against the current code before relying on it.",
        *body,
        "</recalled_experience>",
    ])


@memory_app.command("recall-for-task")
def cmd_recall_for_task(
    task: str = typer.Argument(
        ..., help="Sub-task description to recall experience for"),
    session: str = typer.Option(
        ..., "--session", "-s",
        help="Session/trace id to attach the memory.recall.task span to"),
    subsystem: Optional[str] = typer.Option(
        None, "--subsystem",
        help="Topic node id to scope retrieval to; if omitted, routed from "
             "the task text"),
    top_k: int = typer.Option(3, "--top-k"),
    scope: Optional[str] = typer.Option(None, "--scope", help="e.g. repo:regin"),
    json_out: bool = typer.Option(
        False, "--json",
        help="Emit the hits as JSON (subsystem, hit_count, and a hits array of "
             "id/kind/title/scope) for an orchestrator to consume "
             "programmatically, instead of the text block"),
) -> None:
    """Structure-first, task-scoped recall for a spawner to bake into a
    sub-task prompt.

    Resolves a subsystem topic node, pulls that subtree's memories via
    `_task_subtree_memories` (structure-first, not query similarity), prints a
    <recalled_experience> block to stdout (or JSON with `--json`), and emits a
    `memory.recall.task` span so `regin gate task-recall-ran` can prove it
    fired. The binding constraint is topic-link coverage (`regin memory
    link-topics`): a subsystem node with no linked memories returns nothing.
    """
    import lib.memory as memory
    from lib.hook_plugin import post_span
    from lib.settings import settings

    store = memory.get_store()
    graph = _merged_graph()
    node_id = _resolve_subsystem_node(graph, subsystem, task,
                                      settings.project_root)
    mems = _task_subtree_memories(store, graph, node_id, top_k, scope)
    hits = _hit_summaries(mems)

    # Side effects fire in BOTH output modes — never gated on format.
    post_span(trace_id=session, name="memory.recall.task", attributes={
        "task": task[:120], "subsystem": node_id,
        "hit_count": len(mems), "hits": hits,
    })
    _record_task_offered(store, session, mems, task)

    if json_out:
        print(json.dumps(
            {"subsystem": node_id, "hit_count": len(mems), "hits": hits},
            indent=2))
    else:
        # Block (and nothing else) to stdout so it pipes into a prompt.
        print(_render_block(mems))


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


def _merged_graph(repo_path=None) -> dict:
    """Repo graph + global meta-roots — the topic id space `--topic` and the
    classifier validate against, so the meta-roots (`skills`, `preferences`, …)
    are fileable. Defaults to the project root; pass `repo_path` for `--repo`."""
    from pathlib import Path
    from lib.settings import settings
    from lib.topics.graph_io import load_authoritative_graph
    from lib.topics.meta_roots import merge_meta_roots
    root = repo_path if repo_path is not None else settings.project_root
    return merge_meta_roots(
        load_authoritative_graph(str(Path(root).resolve())))


def _reject_titleless_lesson(kind: str, title: Optional[str]) -> None:
    """Exit(1) with a friendly message if a lesson is being created without a
    title (the store enforces this too, but a raw traceback is poor UX)."""
    if kind == "lesson" and not (title or "").strip():
        print("error: a lesson requires a title (the one-line rule)")
        raise typer.Exit(1)


def _validate_remember_args(kind: str, title: Optional[str],
                            topic: Optional[str]) -> None:
    """Exit(1) with a friendly message on bad `remember` args — kept out of
    the command body so it stays under the cyclomatic-complexity gate."""
    from lib.memory.models import MEMORY_KINDS
    if kind not in MEMORY_KINDS:
        print(f"error: --kind must be one of {', '.join(MEMORY_KINDS)}")
        raise typer.Exit(1)
    _reject_titleless_lesson(kind, title)
    if topic is not None and topic not in (_merged_graph().get("topics") or {}):
        print(f"error: no topic node {topic!r} — list roots with "
              f"`index_root` (memory MCP) or pick a meta-root "
              f"(skills, preferences)")
        raise typer.Exit(1)


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

    _validate_remember_args(kind, title, topic)
    mid = memory.remember(
        body, kind=kind, title=title, scope=scope, importance=importance,
        tags=[t.strip() for t in tags.split(",") if t.strip()]
        if tags else None)
    print(f"remembered {mid}")
    if topic is not None:
        memory.get_store().link_authoritative_topic(mid, topic,
                                                    source="manual")
        print(f"  filed under topic {topic}")


@memory_app.command("backfill-titles")
def cmd_backfill_titles() -> None:
    """Give every titleless `lesson` a title derived from its body — the
    one-time repair for rows written before lesson titles became mandatory."""
    import lib.memory as memory
    fixed = memory.get_store().backfill_lesson_titles()
    print(f"backfilled {fixed} lesson title(s)")


@memory_app.command("retitle")
def cmd_retitle(
    scope: Optional[str] = typer.Option(
        None, "--scope", help="Limit to one repo scope (e.g. repo:regin)"),
    limit: Optional[int] = typer.Option(
        None, "--limit", help="Max lessons to retitle this run"),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Show old→new titles without writing"),
) -> None:
    """LLM-upgrade auto-derived lesson titles into one-line rules.

    A lesson captured via `send_to_user(type=lesson)` without a `title` gets a
    truncated body-slice placeholder (tagged `auto-title`) that recalls poorly
    and reads badly in every list. This distils a real imperative headline from
    each such body, in batches, and rewrites it in place (FTS refreshes now; the
    dense embedding self-heals on next recall). Needs an external agent
    configured, else it no-ops."""
    import lib.memory as memory
    from lib.memory.adapters import resolve_retitler
    from lib.memory.retitle import retitle_memories
    result = retitle_memories(memory.get_store(), resolve_retitler(),
                              scope=scope, limit=limit, dry_run=dry_run)
    print(f"scanned={result.scanned} candidates={result.candidates} "
          f"retitled={result.retitled} batches={result.batches} "
          f"unparsed={result.unparsed}{' (dry run)' if dry_run else ''}")
    for ch in result.changes:
        print(f"  {ch['id'][:8]}  {ch['old'][:60]!r}\n         → {ch['new']!r}")


@memory_app.command("export-tree")
def cmd_export_tree(
    repo: Optional[str] = typer.Option(
        None, "--repo", help="Repo whose topic.json graph to file memories "
             "under; default: project root"),
    out_dir: Optional[str] = typer.Option(
        None, "--out-dir",
        help="Target dir; default: .regin/memory/tree/ under the repo"),
    scope: Optional[str] = typer.Option(
        None, "--scope", help="Only export memories in this scope"),
) -> None:
    """Export active memories as a git-shareable markdown tree, mirrored
    onto the authoritative topic graph: one canonical file per memory under
    its (lexicographically-smallest) linked topic node, a frontmatter-only
    stub at every other linked node, and `_unfiled/` for memories with no
    authoritative link."""
    from pathlib import Path
    import lib.memory as memory
    from lib.settings import settings

    repo_path = Path(repo).resolve() if repo else Path(settings.project_root)
    summary = memory.export_memory_tree(str(repo_path), out_dir=out_dir,
                                        scope=scope)
    print(f"exported canonical={summary['canonical']} "
          f"stub={summary['stub']} unfiled={summary['unfiled']}")


@memory_app.command("import-tree")
def cmd_import_tree(
    repo: Optional[str] = typer.Option(
        None, "--repo", help="Repo whose topic-tree markdown dir to import; "
             "default: project root"),
    in_dir: Optional[str] = typer.Option(
        None, "--in-dir",
        help="Source dir; default: .regin/memory/tree/ under the repo"),
) -> None:
    """Import a git-shared markdown memory tree back into the local memory
    store. Idempotent: canonical files upsert by id and links are
    re-linked, never duplicated, so re-running after a `git pull` is safe."""
    from pathlib import Path
    import lib.memory as memory
    from lib.settings import settings

    repo_path = Path(repo).resolve() if repo else Path(settings.project_root)
    summary = memory.import_memory_tree(str(repo_path), in_dir=in_dir)
    print(f"imported={summary['imported']} linked={summary['linked']} "
          f"skipped_unfiled={summary['skipped_unfiled']}")


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
    topic_id: str = typer.Argument(..., help="Topic route id"),
    query: str = typer.Argument(..., help="The prompt text the case is keyed "
                                "on — routing re-ranks similar future queries"),
    positive: bool = typer.Option(False, "--positive", "-p",
                                  help="A useful case (protect the route)"),
    negative: bool = typer.Option(False, "--negative", "-n",
                                  help="An unhelpful case (suppress the route)"),
) -> None:
    """Hand-curate a topic-route query exemplar (a 'case'). One of
    --positive/--negative is required. The query is embedded and stored as a
    manual exemplar; at route time it protects (or suppresses) the topic banner
    for prompts it resembles."""
    import lib.memory as memory
    polarity = _polarity_flag(positive, negative)
    if polarity is None:
        print("error: pass exactly one of --positive / --negative")
        raise typer.Exit(1)
    written = memory.get_store().add_topic_exemplars(
        "manual", [(topic_id, query)], polarity, source="manual")
    kind = "positive" if polarity > 0 else "negative"
    if written:
        print(f"recorded {kind} exemplar for {topic_id}")
    else:
        print("nothing written (no embedder available, or blank query)")


@memory_app.command("exemplar-rm")
def cmd_exemplar_rm(
    topic_id: str = typer.Argument(..., help="Topic route id"),
    positive: bool = typer.Option(False, "--positive", "-p"),
    negative: bool = typer.Option(False, "--negative", "-n"),
) -> None:
    """Drop curated/captured exemplars for a topic route. With neither
    --positive nor --negative, removes both polarities."""
    import lib.memory as memory
    polarity = _polarity_flag(positive, negative)  # None → both
    removed = memory.get_store().remove_topic_exemplars(topic_id, polarity)
    print(f"removed {removed} exemplar(s) from {topic_id}")


@memory_app.command("exemplar-list")
def cmd_exemplar_list(
    topic_id: str = typer.Argument(..., help="Topic route id"),
) -> None:
    """List the individual exemplars ('cases') for a topic route — their
    id (to revert one), polarity, source, and the query each was built from."""
    import lib.memory as memory
    rows = memory.get_store().list_topic_exemplars(topic_id)
    if not rows:
        print(f"no exemplars for {topic_id}")
        return
    print(f"{len(rows)} exemplar(s) for {topic_id}:")
    for r in rows:
        sign = "+" if r["polarity"] > 0 else "-"
        print(f"  #{r['id']:<5d} {sign} {r['source']:6s} "
              f"{(r['query'] or '(query not recorded)')[:72]}")
    print("  revert one with: regin memory exemplar-forget <id>")


@memory_app.command("exemplar-forget")
def cmd_exemplar_forget(
    exemplar_id: int = typer.Argument(..., help="Exemplar id (from "
                                      "exemplar-list) to delete"),
) -> None:
    """Delete one topic exemplar by id — undo a single mislabel, the
    fine-grained complement to exemplar-rm (which drops a whole polarity)."""
    import lib.memory as memory
    ok = memory.get_store().delete_exemplar(exemplar_id, "topic")
    print(f"removed exemplar #{exemplar_id}" if ok
          else f"no topic exemplar #{exemplar_id}")


@memory_app.command("reflect")
def cmd_reflect(
    dry_run: bool = typer.Option(False, "--dry-run",
                                 help="Report actions without writing"),
) -> None:
    import lib.memory as memory
    result = memory.reflect(dry_run=dry_run)
    print(f"examined={result.examined} merged={result.merged} "
          f"pairs_checked={result.pairs_checked} "
          f"contradictions={result.contradictions} "
          f"obsoleted={result.obsoleted} "
          f"dream_skipped={result.dream_skipped} "
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


def _mem_title(m: dict) -> str:
    """A human-readable handle for a memory: its title, else a one-line body
    snippet, else a placeholder — so the dry-run preview never shows a bare id
    a user can't interpret."""
    title = (m.get("title") or "").strip()
    if not title:
        title = " ".join((m.get("body") or "").split())
    return title or "(untitled)"


def _fmt_link(mid: str, node_id: str, titles: dict, labels: dict,
              status: str = "") -> str:
    """One readable line: `<status>  <id8>  <memory title>  → <topic-id> (label)`.
    Replaces the bare `<id> -> <id>` so a user can see *what* memory is being
    filed *where* without looking either id up. `status` (`linked`/`refresh`/
    `would`) names the action so a real run shows the same per-link detail as a
    dry-run, with new vs refreshed links distinguishable at a glance."""
    title = titles.get(mid, "")
    if len(title) > 50:
        title = title[:49] + "…"
    label = labels.get(node_id, node_id)
    # The label carries the node's full intent and can run long; clip it so a
    # line stays scannable (the id already names the target precisely).
    short = label.split(" (", 1)[0]
    if len(short) > 38:
        short = short[:37] + "…"
    suffix = f"  ({short})" if short and short != node_id else ""
    tag = f"{status:<7} " if status else ""
    return f"  {tag}{mid[:8]}  {title:<50}  → {node_id}{suffix}"


def _link_topics_hard(store, rows, repo_path, dry_run, titles,
                      labels) -> tuple[int, int, int]:
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
            status = "would"
            linked += 1
        elif store.link_authoritative_topic(m["id"], node_id, source="route"):
            status = "linked"
            linked += 1
        else:
            status = "refresh"
            refreshed += 1
        print(_fmt_link(m["id"], node_id, titles, labels, status))
    return linked, refreshed, unmatched


def _apply_assignments(store, assignments, dry_run, titles,
                       labels) -> tuple[int, int, int]:
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
                status = "would"
                linked += 1
            elif store.link_authoritative_topic(mid, node_id,
                                                source=CLASSIFY_SOURCE):
                status = "linked"
                linked += 1
            else:
                status = "refresh"
                refreshed += 1
            print(_fmt_link(mid, node_id, titles, labels, status))
    return linked, refreshed, unmatched


def _select_link_rows(store, *, scope, kind, tier, limit,
                      orphans_only) -> list[dict]:
    """Pick the active memories `link-topics` will (re)classify, applying the
    optional filters. `kind`/`tier` pass straight through to `list_memories`;
    `orphans_only` keeps only memories with NO authoritative-topic link yet
    (the 'unfiled' bucket from `store.orphaned_memory_ids`), so a re-run is
    incremental — it never re-sends already-filed memories to the classifier."""
    rows = store.list_memories(status="active", scope=scope, kind=kind,
                               tier=tier, limit=limit)
    if orphans_only:
        orphan_ids = set(store.orphaned_memory_ids(scope=scope))
        rows = [m for m in rows if m["id"] in orphan_ids]
    return rows


def _classify_agentic(rows, graph, stats=None):
    """Run the agentic classifier over `rows` against the pre-merged `graph`;
    exit non-zero (fail-loud) when no external agent is reachable instead of
    degrading to the heuristic. `graph` already carries the global meta-roots
    so skill-/preference-shaped memories route to a precise leaf. A `stats`
    dict, when passed, is filled with `placed`/`batches`/`unparsed` so the
    caller can report silently-dropped (unparsed-batch) memories."""
    from lib.memory.adapters import resolve_topic_classifier
    from lib.memory.topic_classify import (classify_memories,
                                           ClassifierUnavailable)

    try:
        return classify_memories(rows, graph, resolve_topic_classifier(),
                                 stats=stats)
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
    kind: Optional[str] = typer.Option(
        None, "--kind",
        help="Only link memories of this kind (e.g. lesson, note, decision)"),
    tier: Optional[str] = typer.Option(
        None, "--tier",
        help="Only link memories in this tier (e.g. working, episodic)"),
    orphans_only: bool = typer.Option(
        False, "--orphans-only", "-o",
        help="Only link memories not yet linked to ANY topic (the unfiled "
             "bucket) — makes a re-run incremental and skips already-filed "
             "memories, so the costly agentic pass only sees new ones"),
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
    rows = _select_link_rows(store, scope=scope, kind=kind, tier=tier,
                             limit=limit, orphans_only=orphans_only)
    graph = _merged_graph(repo_path)
    # Resolve ids → human handles once so the dry-run preview reads as
    # "<memory title> → <topic label>" instead of two opaque ids.
    titles = {m["id"]: _mem_title(m) for m in rows}
    labels = {tid: (node.get("label") or tid)
              for tid, node in graph.get("topics", {}).items()}
    diag = ""
    if hard_match:
        linked, refreshed, unmatched = _link_topics_hard(
            store, rows, repo_path, dry_run, titles, labels)
    else:
        stats: dict = {}
        assignments = _classify_agentic(rows, graph, stats)
        linked, refreshed, unmatched = _apply_assignments(
            store, assignments, dry_run, titles, labels)
        # Memories in a batch the LLM left unparseable / uncompleted never
        # enter `assignments`, so they're neither linked nor counted unmatched
        # — surface them so a silent batch failure isn't invisible.
        dropped = len(rows) - len(assignments)
        diag = f" dropped={dropped} unparsed={stats.get('unparsed', 0)}"
    verb = "would link" if dry_run else "linked"
    selected = "orphaned" if orphans_only else "active"
    print(f"{verb}={linked} refreshed={refreshed} unmatched={unmatched}{diag} "
          f"(of {len(rows)} {selected})")


def _redeploy_skills(slugs) -> None:
    """Best-effort: push each edited pattern source to the active agent so the
    folded-in lesson reaches the live skill. A deploy failure is reported, not
    fatal — the durable change is already in the source SKILL.md."""
    from pathlib import Path
    from lib.settings import settings
    from lib.skills.skill_deployer import deploy_pattern_as_skill
    for slug in sorted(slugs):
        src = Path(settings.patterns_dir) / slug
        try:
            deploy_pattern_as_skill(str(src), slug, slug)
            print(f"  redeployed {slug}")
        except Exception as exc:  # noqa: BLE001 — report, never abort the run
            print(f"  redeploy of {slug} failed: {exc}")


@memory_app.command("consolidate-skills")
def cmd_consolidate_skills(
    apply: bool = typer.Option(
        False, "--apply", help="Write non-manual sources + retire those "
        "memories. Default: preview only (no writes)"),
    skill: Optional[str] = typer.Option(
        None, "--skill", help="Limit to one skill slug"),
    min_recall: Optional[int] = typer.Option(
        None, "--min-recall", help="Override the promotion bar (recall_count)"),
) -> None:
    """Graduate proven skill-memories into their skill's SKILL.md.

    A memory filed under a `skill-<slug>` meta-leaf whose recall_count clears
    the bar is folded into a `## Lessons (from agent memory)` section of the
    pattern source and retired. Preview by default; `--apply` writes. A
    `manual: true` (user-owned) pattern is never auto-written — it is listed
    as a proposal for you to apply by hand.
    """
    import lib.memory as memory
    from lib.memory.skill_consolidate import consolidate_skills
    result = consolidate_skills(memory.get_store(), apply=apply, skill=skill,
                                min_recall=min_recall)
    if not result.lessons:
        print("no skill-memories over the promotion bar")
        return
    verb = "folded" if apply else "would fold"
    for ll in result.lessons:
        if ll.skipped:
            print(f"  SKIP  {ll.memory_id[:8]} → {ll.skill}: {ll.skipped}")
            print(f"        propose: {ll.bullet[:100]}")
        else:
            print(f"  {verb.upper():10s} {ll.memory_id[:8]} → {ll.skill}: "
                  f"{ll.title}")
    if apply and result.changed_skills:
        print(f"applied {result.applied}; redeploying "
              f"{len(result.changed_skills)} skill(s):")
        _redeploy_skills(result.changed_skills)
    elif not apply:
        print(f"\n{len([ll for ll in result.lessons if not ll.skipped])} "
              f"ready to fold — re-run with --apply to write")


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
