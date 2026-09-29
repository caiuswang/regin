"""`regin memory link-topics` and `consolidate-skills`."""

from __future__ import annotations

from typing import Optional

import typer

from ._common import _merged_graph, memory_app


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
        help="Repo whose approved topic graph to match against; "
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
    """Link active memories to authoritative topic-graph nodes.

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
