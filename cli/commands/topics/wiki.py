"""`regin topics` wiki generation and stats, tier/wiki backfills, and route."""

from __future__ import annotations

import json
from pathlib import Path

import typer

from lib.topics import TopicGraphError, route_topic
from lib.topics.wiki import generate_wiki

from ._common import _repo_path, topics_app


@topics_app.command("wiki", help="Generate derived wiki files from the approved graph")
def cmd_topics_wiki(
    repo: str | None = typer.Option(None, "--repo", help="Repository path"),
) -> None:
    try:
        paths = generate_wiki(_repo_path(repo))
    except (TopicGraphError, ValueError) as exc:
        print(f"Topic wiki generation failed: {exc}")
        raise typer.Exit(1)
    print(f"Topic wiki files written: {len(paths)}")
    for path in paths:
        print(path)


@topics_app.command(
    "wiki-debt",
    help="Report topics needing a wiki (missing or drifted), optionally "
         "scoped to a git diff — the goal-verified close-out check")
def cmd_topics_wiki_debt(
    repo: str | None = typer.Option(None, "--repo", help="Repository path"),
    changed_since: str | None = typer.Option(
        None, "--changed-since",
        help="Only audit topics whose refs changed between this git ref and "
             "HEAD (e.g. HEAD~1). Omit to audit the whole repo."),
    emit: bool = typer.Option(
        False, "--emit",
        help="Emit a fast, agent-free stub refresh proposal (pending_review) "
             "for each drifted topic. missing topics stay report-only."),
    session_id: str | None = typer.Option(
        None, "--session-id",
        help="Attribute emitted drift cards to this agent session so the "
             "inbox card links back to the run that detected the drift. "
             "Defaults to the id `regin session-id` resolves."),
    as_json: bool = typer.Option(False, "--json", help="Emit machine-readable JSON"),
) -> None:
    from lib.session_probe import resolve as resolve_session
    from lib.topics.wiki_debt import emit_wiki_debt_proposals, wiki_debt

    rp = _repo_path(repo)
    sid = session_id or resolve_session()
    rows = (emit_wiki_debt_proposals(rp, changed_since=changed_since,
                                     session_trace_id=sid) if emit
            else wiki_debt(rp, changed_since=changed_since))
    if as_json:
        print(json.dumps(rows, indent=2))
        return
    if not rows:
        scope = f" changed since {changed_since}" if changed_since else ""
        print(f"No wiki debt{scope} — every audited topic has a current wiki.")
        return
    for row in rows:
        detail = (f" ({', '.join(row['drifted_paths'])})"
                  if row["status"] == "drifted" else "")
        queued = (f" → proposal {row['proposal_id']}"
                  if row.get("proposal_id") else "")
        print(f"{row['status']:8} {row['topic_id']}{detail}{queued}")


def _wiki_stat_row(row, topics: dict, wdir: Path) -> dict:
    node = topics.get(row.topic_id) or {}
    return {
        "topic_id": row.topic_id,
        "label": node.get("label") or row.topic_id,
        "signal": row.signal,
        "recall_count": row.recall_count,
        "last_recalled": row.last_recalled,
        "wiki_present": (wdir / f"{row.topic_id}.md").exists(),
    }


def _format_wiki_stat_line(e: dict) -> str:
    stale = "" if e["wiki_present"] else "  ⚠ wiki missing"
    last = (e["last_recalled"] or "")[:19]
    return (f"{e['recall_count']:>5}  {e['signal']:8}  {e['topic_id']}"
            f"  ({e['label']}){stale}  {last}")


@topics_app.command(
    "wiki-stats",
    help="Report per-topic wiki recall counts (how often index_fetch surfaced "
         "each wiki). A high count whose wiki file is gone, or exposure with "
         "no reads, is a prune/refresh signal.")
def cmd_topics_wiki_stats(
    repo: str | None = typer.Option(None, "--repo", help="Repository path"),
    signal: str | None = typer.Option(
        None, "--signal",
        help="Filter to one signal: 'exposure' (index_fetch surfaced the "
             "path) or 'read' (agent Read the file). Omit for all."),
    sync: bool = typer.Option(
        False, "--sync",
        help="Recompute the 'read' signal from the session trace (tool.Read "
             "spans on wiki files) before reporting. Idempotent."),
    as_json: bool = typer.Option(False, "--json", help="Emit machine-readable JSON"),
) -> None:
    import lib.memory as memory
    from lib.topics.graph_io import load_authoritative_graph
    from lib.topics.wiki import wiki_dir

    if not memory.enabled():
        print("agent memory is disabled (settings.agent_memory.enabled)")
        raise typer.Exit(1)

    if sync:
        from lib.memory.wiki_reads import sync_wiki_reads
        sync_wiki_reads()

    rp = _repo_path(repo)
    topics = load_authoritative_graph(str(rp)).get("topics") or {}
    wdir = wiki_dir(rp)
    enriched = [_wiki_stat_row(r, topics, wdir)
                for r in memory.get_store().wiki_recall_stats(signal=signal)]

    if as_json:
        print(json.dumps(enriched, indent=2))
        return
    if not enriched:
        scope = f" for signal={signal}" if signal else ""
        print(f"No wiki recalls recorded yet{scope}.")
        return
    for e in enriched:
        print(_format_wiki_stat_line(e))


@topics_app.command(
    "backfill-tiers",
    help="Tag refs a topic's wiki never mentions as tier=reference so they stop "
         "emitting content-drift debt. Dry-run by default; --apply writes the "
         "git-tracked base graph (review the diff before committing). Never "
         "overrides an existing tier, so it is safe to re-run.")
def cmd_topics_backfill_tiers(
    repo: str | None = typer.Option(None, "--repo", help="Repository path"),
    topic: str | None = typer.Option(
        None, "--topic", help="Only backfill this topic id (default: all)."),
    apply: bool = typer.Option(
        False, "--apply",
        help="Write tier=reference into the base graph (default: dry-run/report)."),
    as_json: bool = typer.Option(False, "--json", help="Emit machine-readable JSON"),
) -> None:
    from lib.topics.tier_backfill import backfill_reference_tiers

    rp = _repo_path(repo)
    result = backfill_reference_tiers(rp, apply=apply, topic_id=topic)
    if as_json:
        print(json.dumps(result, indent=2))
        return
    demotions = result["demotions"]
    if demotions:
        verb = "tagged" if result["applied"] else "would tag"
        for row in demotions:
            print(f"{verb} reference  {row['topic_id']}  {row['path']}")
        topics_n = len({row["topic_id"] for row in demotions})
        where = ("written to the base graph — review `git diff` before committing"
                 if result["applied"] else "dry-run — pass --apply to write")
        print(f"\n{len(demotions)} ref(s) across {topics_n} topic(s) ({where})")
    else:
        print("No reference-tier candidates — every wiki-unmentioned ref is "
              "already tagged (or none found).")
    if result["skipped_no_wiki"]:
        print(f"skipped {len(result['skipped_no_wiki'])} topic(s) with no wiki "
              f"(no narrative to judge refs against)")


@topics_app.command(
    "backfill-topic-wiki",
    help="One-time: split legacy runs' combined wiki into per-topic wiki "
         "bodies (and add the wiki_md columns on databases that predate them). "
         "New runs need no backfill — they draft per-topic wiki directly.")
def cmd_topics_backfill_topic_wiki() -> None:
    from lib.topics.wiki_backfill import backfill_topic_wiki

    result = backfill_topic_wiki()
    print(f"Topic-wiki backfill: filled {result['filled']} topic pages "
          f"across {result['revisions']} revision(s).")


def _render_topic_wiki(result: dict) -> str:
    """The routed topic's wiki pages as plain markdown — the content the
    `<topic_context>` pointer promises, surfaced directly. The JSON envelope
    sorts `wiki_pages` below the (often long) `refs` list, so an agent that
    pipes the JSON through `head` never reaches the content; `--wiki` prints
    it content-first instead."""
    pages = result.get("wiki_pages") or []
    if not pages:
        return f"(no wiki pages for topic {result.get('query', '')!r})"
    out = []
    for page in pages:
        path = page.get("path")
        if path:
            out.append(f"<!-- {path} -->")
        out.append(page.get("content", ""))
        if page.get("truncated"):
            out.append("_(wiki page truncated — read the file for the full text)_")
    return "\n\n".join(out).strip()


@topics_app.command("route", help="Resolve a query into approved topic context")
def cmd_topics_route(
    query: str = typer.Argument(..., help="Topic query"),
    repo: str | None = typer.Option(None, "--repo", help="Repository path"),
    wiki: bool = typer.Option(
        False, "--wiki",
        help="Print the topic's wiki markdown (content-first) instead of the "
             "JSON envelope, which buries the wiki below the refs list."),
) -> None:
    try:
        result = route_topic(_repo_path(repo), query)
    except TopicGraphError as exc:
        print(f"Topic route failed: {exc}")
        raise typer.Exit(1)
    if wiki:
        print(_render_topic_wiki(result))
        return
    print(json.dumps(result, indent=2, sort_keys=True))
