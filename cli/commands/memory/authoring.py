"""`regin memory` write side: list/stats, remember, titles, tree export/import,
topic feedback, exemplars, reflect and distill."""

from __future__ import annotations

import json
from typing import Optional

import typer

from ._common import _merged_graph, _print_memory_line, memory_app


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
        None, "--repo", help="Repo whose approved topic graph to file memories "
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
    if summary.get("refused"):
        print("refused: the store looks empty but the tree is populated — "
              "nothing was deleted. Check which memory DB this process sees "
              "(regin doctor) before re-running.")
        raise typer.Exit(code=1)
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
