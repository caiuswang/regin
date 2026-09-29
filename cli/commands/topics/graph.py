"""`regin topics` graph lifecycle: bootstrap, scan, list, promote/delete, drift
and evolve, review notes, proposal finish/reap and propose."""

from __future__ import annotations

import json

import typer

from lib.topics import (
    TopicGraphError,
    bootstrap,
    delete_topic,
    load_graph,
    load_local_graph,
    promote_all_topics,
    promote_topic,
    scan,
)

from ._common import _repo_path, topics_app


@topics_app.command("bootstrap", help="Create the approved topic graph (.regin/topics/topics/) for a repo")
def cmd_topics_bootstrap(
    repo: str | None = typer.Option(None, "--repo", help="Repository path"),
    seeds: bool = typer.Option(False, "--seeds", help="Create a few starter topics"),
    force: bool = typer.Option(False, "--force", help="Overwrite existing topic files"),
) -> None:
    try:
        paths = bootstrap(_repo_path(repo), seeds=seeds, force=force)
    except TopicGraphError as exc:
        print(f"Topic bootstrap failed: {exc}")
        raise typer.Exit(1)
    print(f"Topic graph created: {paths['topic']}")


@topics_app.command("scan", help="Refresh refs on approved topics from current files")
def cmd_topics_scan(
    repo: str | None = typer.Option(None, "--repo", help="Repository path"),
    staged: bool = typer.Option(False, "--staged", help="Scan staged file names only"),
) -> None:
    try:
        result = scan(_repo_path(repo), staged=staged)
    except TopicGraphError as exc:
        print(f"Topic scan failed: {exc}")
        raise typer.Exit(1)
    print(f"Updated topics: {len(result['updated_topics'])}")
    print(f"Covered refs: {result['covered_ref_count']}")


@topics_app.command(
    "list",
    help="Show topic ids by layer: shared (base graph) vs local overlay",
)
def cmd_topics_list(
    repo: str | None = typer.Option(None, "--repo", help="Repository path or registered name"),
    local_only: bool = typer.Option(False, "--local", help="Show only the local overlay (added + tombstoned)"),
) -> None:
    """Print topic ids grouped by where they live, so the user knows what's
    eligible for `topics promote` without having to open the JSON files."""
    try:
        path = _repo_path(repo)
        base_topics = sorted((load_graph(path).get("topics") or {}).keys())
        overlay = load_local_graph(path)
    except TopicGraphError as exc:
        print(f"Topic list failed: {exc}")
        raise typer.Exit(1)
    overlay_topics = sorted((overlay.get("topics") or {}).keys())
    deleted_topics = sorted(overlay.get("deleted_topics") or [])

    if not local_only:
        print(f"Shared ({len(base_topics)}) — in the base graph, travel via git:")
        for tid in base_topics:
            print(f"  {tid}")
    print(f"Local-added ({len(overlay_topics)}) — in topic.local.json, eligible for `topics promote`:")
    for tid in overlay_topics:
        print(f"  {tid}")
    print(f"Local-deleted ({len(deleted_topics)}) — tombstones, will remove from the base graph on promote:")
    for tid in deleted_topics:
        print(f"  {tid}")


def _print_promote_all(result: dict) -> None:
    """Render the `topics promote --all` summary."""
    added, removed = result["added"], result["removed"]
    if not added and not removed:
        print("Nothing to promote: local overlay has no pending changes.")
        return
    if added:
        print(f"Added to the base graph ({len(added)}): {', '.join(added)}")
    if removed:
        print(f"Removed from the base graph ({len(removed)}): {', '.join(removed)}")
    print("Cleared the overlay. Commit .regin/topics/ (+ wikis) to share it.")


@topics_app.command(
    "promote",
    help="Promote local-overlay topic changes into the git-tracked base graph",
)
def cmd_topics_promote(
    topic_id: str | None = typer.Argument(
        None, help="Topic id present in topic.local.json (omit with --all)"
    ),
    repo: str | None = typer.Option(None, "--repo", help="Repository path"),
    promote_all: bool = typer.Option(
        False, "--all",
        help="Promote every overlay-added topic and tombstoned deletion at once",
    ),
) -> None:
    """Move an approved/edited topic out of the machine-local overlay and
    into the shared base graph so it travels via git. The topic is
    already live locally; this only makes it shareable. Pass `--all` to
    promote every pending overlay change in one pass instead of an id.
    """
    if promote_all == (topic_id is not None):
        raise typer.BadParameter("pass a topic id or --all (exactly one)")

    try:
        if promote_all:
            _print_promote_all(promote_all_topics(_repo_path(repo)))
            return
        single = promote_topic(_repo_path(repo), topic_id)
    except TopicGraphError as exc:
        print(f"Promote failed: {exc}")
        raise typer.Exit(1)

    where = "added to" if single["action"] == "added" else "removed from"
    print(f"Promoted '{single['topic_id']}': {where} the base graph; cleared from overlay.")
    print("Commit .regin/topics/topics/ (+ its wiki) to share it.")


@topics_app.command(
    "delete",
    help="Permanently delete an approved topic (graph + wiki); prunes inbound edges",
)
def cmd_topics_delete(
    topic_id: str = typer.Argument(..., help="Topic id to delete"),
    repo: str | None = typer.Option(None, "--repo", help="Repository path"),
) -> None:
    """Hard-delete an approved topic from the graph and remove its wiki.

    Use `downgrade` instead to move a topic back to a reviewable draft.
    """
    try:
        result = delete_topic(_repo_path(repo), topic_id)
    except TopicGraphError as exc:
        print(f"Delete failed: {exc}")
        raise typer.Exit(1)
    extras = []
    if result["pruned_edges"]:
        extras.append(f"pruned {result['pruned_edges']} inbound edge(s)")
    if result["wiki_removed"]:
        extras.append("removed wiki")
    suffix = f" ({'; '.join(extras)})" if extras else ""
    print(f"Deleted '{result['topic_id']}'{suffix}.")
    print("Commit .regin/topics/ to share the removal.")


@topics_app.command(
    "drift",
    help="Follow git renames into topic refs + memory paths (gated by "
         "topic_evolution.mechanical_autoapply)")
def cmd_topics_drift(
    repo: str | None = typer.Option(None, "--repo", help="Repository path"),
    base: str = typer.Option("HEAD~1", "--base", help="Compare-from commit"),
    head: str = typer.Option("HEAD", "--head", help="Compare-to commit"),
) -> None:
    from lib.topics.drift import run_mechanical_drift

    result = run_mechanical_drift(_repo_path(repo), base=base, head=head)
    if not result.get("enabled"):
        print("Drift skipped (topic_evolution.mechanical_autoapply is off)")
        return
    print(f"Renames followed: {result['renames']} — "
          f"topics rewritten: {result['topics_rewritten']}, "
          f"memories rewritten: {result['memories_rewritten']}, "
          f"memories staled: {result['memories_staled']}")


@topics_app.command(
    "evolve",
    help="Detect content drift and emit refresh proposals (gated by "
         "topic_evolution.evolution_enabled)")
def cmd_topics_evolve(
    repo: str | None = typer.Option(None, "--repo", help="Repository path"),
    all_repos: bool = typer.Option(
        False, "--all", help="Run across every registered repo (for cron)"),
) -> None:
    from lib.settings import settings as _settings
    from lib.topics.content_drift import run_content_evolution

    targets = ([str(p) for p in _settings.repo_paths] if all_repos
               else [str(_repo_path(repo))])
    for target in targets:
        result = run_content_evolution(target)
        if not result.get("enabled"):
            print(f"{target}: evolve skipped (evolution_enabled is off)")
            continue
        print(f"{target}: drifted {result['drifted']}, "
              f"proposals {result['proposals']}, "
              f"memories staled {result['memories_staled']}, "
              f"expired {result['expired']}")


@topics_app.command(
    "drift-dismiss",
    help="Dismiss a topic's content drift with the reason on the record "
         "(the drift judge calls this for a TRIVIAL verdict)")
def cmd_topics_drift_dismiss(
    topic_id: str = typer.Argument(..., help="Topic whose drift is trivial"),
    reason: str = typer.Option(..., "--reason",
                               help="One-sentence why the wiki is unaffected"),
    repo: str | None = typer.Option(None, "--repo", help="Repository path"),
) -> None:
    from lib.topics.content_drift import judge_dismiss_drift

    result = judge_dismiss_drift(_repo_path(repo), topic_id, reason)
    print(f"{topic_id}: dismissed — threads {len(result['threads_dismissed'])}"
          f" (commented {result['threads_commented']}), "
          f"digests recaptured {result['digests_captured']}, "
          f"standalone stub ignored: {result['proposal_ignored']}")


@topics_app.command(
    "drift-note",
    help="Attach a review note to a topic's open content-drift threads "
         "(the drift judge calls this for a MATERIAL verdict)")
def cmd_topics_drift_note(
    topic_id: str = typer.Argument(..., help="Topic whose drift is material"),
    note: str = typer.Option(..., "--note",
                             help="What the wiki redraft must cover"),
    repo: str | None = typer.Option(None, "--repo", help="Repository path"),
) -> None:
    from lib.topics.content_drift import judge_note_drift

    commented = judge_note_drift(_repo_path(repo), topic_id, note)
    if commented:
        print(f"{topic_id}: note attached to {commented} open drift thread(s)")
    else:
        print(f"{topic_id}: no open drift threads — note not recorded")


@topics_app.command(
    "cascade-stale",
    help="Cascade a topic's staleness to its linked memories "
         "(veracity true->unknown)")
def cmd_topics_cascade_stale(
    topic_id: str = typer.Argument(..., help="Authoritative topic node id"),
    reason: str = typer.Option("stale", "--reason", help="Validation note"),
) -> None:
    from lib.memory import get_store
    from lib.memory.topic_cascade import cascade_topic_stale

    n = cascade_topic_stale(get_store(), topic_id, reason=reason)
    print(f"Cascaded staleness to {n} memory(ies)")


@topics_app.command(
    "digest-refs",
    help="Capture per-topic-ref content fingerprints for drift detection")
def cmd_topics_digest_refs(
    repo: str | None = typer.Option(None, "--repo", help="Repository path"),
) -> None:
    from lib.topics.ref_digest import capture_all_digests

    try:
        result = capture_all_digests(_repo_path(repo))
    except TopicGraphError as exc:
        print(f"Digest capture failed: {exc}")
        raise typer.Exit(1)
    total = sum(result.values())
    print(f"Captured {total} ref digest(s) across {len(result)} topic(s)")


@topics_app.command(
    "review-note",
    help="Generate an LLM review note for a proposal run (manual, ungated)")
def cmd_topics_review_note(
    proposal_id: str = typer.Argument(..., help="Proposal run id"),
    repo: str | None = typer.Option(None, "--repo", help="Repository path"),
) -> None:
    from lib.topics.proposal_review import generate_review_note

    try:
        thread = generate_review_note(_repo_path(repo), proposal_id)
    except TopicGraphError as exc:
        print(f"Review note failed: {exc}")
        raise typer.Exit(1)
    if thread is None:
        print("No review note written (no external agent configured, or the "
              "proposal has no draft).")
        raise typer.Exit(1)
    print(f"Review note added to proposal {proposal_id} (thread {thread.get('id')})")


@topics_app.command(
    "review-finish",
    help="Submit a review agent's verdict into a review_note thread "
         "(the review agent calls this as its final step)")
def cmd_topics_review_finish(
    proposal_id: str = typer.Argument(..., help="Proposal run id"),
    repo: str | None = typer.Option(None, "--repo", help="Repository path"),
) -> None:
    from lib.topics.proposal_review import finish_review_note

    try:
        thread = finish_review_note(_repo_path(repo), proposal_id, source="agent")
    except TopicGraphError as exc:
        print(f"Review finish failed: {exc}")
        raise typer.Exit(1)
    if thread is None:
        print(f"{proposal_id}: review already submitted (no-op)")
        return
    recommendation = (thread.get("metadata") or {}).get("recommendation")
    print(f"Review note added to proposal {proposal_id} "
          f"(thread {thread.get('id')}, recommendation {recommendation})")


@topics_app.command(
    "proposal-finish",
    help="Ingest a finished proposal run on the agent's completion signal "
         "(the drafting agent calls this as its final step)")
def cmd_topics_proposal_finish(
    proposal_id: str = typer.Argument(..., help="Proposal run id"),
    repo: str | None = typer.Option(None, "--repo", help="Repository path"),
) -> None:
    from lib.topics.proposals import finish_proposal_run

    try:
        result = finish_proposal_run(_repo_path(repo), proposal_id, source="agent")
    except TopicGraphError as exc:
        print(f"Proposal finish failed: {exc}")
        raise typer.Exit(1)
    verb = "ingested" if result["ingested"] else "no-op"
    print(f"{proposal_id}: {result['state']} ({verb})")


@topics_app.command(
    "proposal-reap",
    help="Mark stranded proposal runs failed (watcher died / serve restarted "
         "mid-run with no finish signal)")
def cmd_topics_proposal_reap(
    repo: str | None = typer.Option(None, "--repo", help="Repository path"),
) -> None:
    from lib.topics.proposals import reap_stranded_proposal_runs

    reaped = reap_stranded_proposal_runs(_repo_path(repo))
    print(f"Reaped {reaped} stranded proposal run(s)")


@topics_app.command(
    "propose",
    help="Draft a topic proposal with the configured external agent "
         "(runs synchronously; prints the run id + final state)")
def cmd_topics_propose(
    topic_request: str = typer.Argument(..., help="What the drafted topics should cover"),
    repo: str | None = typer.Option(None, "--repo", help="Repository path"),
    agent: str | None = typer.Option(
        None, "--agent", help="External agent id (defaults to the configured default)"),
    as_json: bool = typer.Option(False, "--json", help="Emit machine-readable JSON"),
) -> None:
    """The web drafts via `start_external_proposal_run`, whose daemon thread
    dies with the process — a CLI that used it would strand the run. Run the
    blocking `create_proposal_run` path instead."""
    from lib.topics.proposal_external import external_agent_configured
    from lib.topics.proposals import create_proposal_run, load_proposal_status

    if not external_agent_configured():
        print("Propose failed: no external drafting agent configured "
              "(settings.topic_proposal_external_agents)")
        raise typer.Exit(1)
    rp = _repo_path(repo)
    try:
        artifacts = create_proposal_run(rp, agent=agent, topic_request=topic_request)
    except TopicGraphError as exc:
        print(f"Propose failed: {exc}")
        raise typer.Exit(1)
    proposal_id = artifacts["dir"].name
    state = load_proposal_status(rp, proposal_id).get("state") or "unknown"
    if as_json:
        print(json.dumps({"id": proposal_id, "state": state}, indent=2))
        return
    print(f"Proposal {proposal_id}: {state}")
