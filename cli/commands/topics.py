"""`regin topics ...` - maintain repo-local topic graphs."""

from __future__ import annotations

from pathlib import Path
import json

import typer

from lib.settings import settings
from lib.topics.wiki import generate_wiki
from lib.topics import (
    TopicGraphError,
    bootstrap,
    delete_topic,
    generate_topic_router_skill,
    install_topic_hooks,
    load_graph,
    load_local_graph,
    promote_all_topics,
    promote_topic,
    route_topic,
    scan,
    validate,
)


topics_app = typer.Typer(
    name="topics",
    help="Maintain repo-local curated topic graphs",
    no_args_is_help=True,
)


def _registered_repo_by_name(repo: str) -> Path | None:
    """Resolve a bare repo name (e.g. `regin`) against `settings.repo_paths`,
    returning the registered tree only if it carries a `.regin/` directory."""
    for candidate in settings.repo_paths or []:
        candidate_path = Path(candidate).expanduser().resolve()
        if candidate_path.name == repo and (candidate_path / ".regin").is_dir():
            return candidate_path
    return None


def _repo_path(repo: str | None) -> Path:
    """Resolve `--repo` to an absolute repo root, with a useful error when
    the argument doesn't look like a real repo. Accepts either a path
    (absolute or relative) or a registered repo name from
    `settings.repo_paths` — the common typo `--repo regin` would
    otherwise resolve relative to cwd and silently miss the right tree.
    """
    if not repo:
        return Path(settings.project_root).resolve()

    resolved = Path(repo).resolve()
    if (resolved / ".regin").is_dir():
        return resolved

    # Name lookup against settings.repo_paths takes precedence over a bare
    # existing directory so the common typo `--repo regin` (which would
    # resolve cwd-relative) still finds the registered tree.
    named = _registered_repo_by_name(repo)
    if named is not None:
        return named

    # Fall back to any existing directory: `bootstrap` creates `.regin/` (so it
    # must accept a tree that doesn't have one yet) and `import` must no-op
    # gracefully on an un-bootstrapped repo from the post-merge git hook. The
    # other subcommands surface a missing graph as their own error downstream.
    if resolved.is_dir():
        return resolved

    known = [str(Path(p).expanduser().resolve()) for p in (settings.repo_paths or [])]
    hint = (
        "\n  registered repos: " + ", ".join(known) if known
        else "\n  (no repo_paths registered — pass an absolute path)"
    )
    raise typer.BadParameter(
        f"--repo {repo!r} doesn't resolve to an existing repo directory"
        f" (tried {resolved}).{hint}"
    )


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


def _proposal_run_row(rp: Path, run: dict) -> dict:
    """One `proposal-list` row: run state + review state + topic count.

    A run with no draft yet (queued / failed before output) has no
    loadable proposal — report it with review '-' and zero topics
    rather than erroring the whole listing."""
    from lib.topics.proposals import (
        load_proposal, load_proposal_status, proposal_review_state,
    )
    review_state, topic_count = "-", 0
    try:
        proposal = load_proposal(rp, run["id"])
        review_state = proposal_review_state(proposal)
        topic_count = len(proposal.get("topics") or [])
    except (OSError, ValueError, TopicGraphError):
        pass
    try:
        status = load_proposal_status(rp, run["id"])
    except TopicGraphError:
        status = {}
    return {
        "id": run["id"],
        "state": run.get("state") or "unknown",
        "review_state": review_state,
        "topic_count": topic_count,
        "created_at": status.get("started_at") or run.get("last_activity_at"),
        "completed_at": status.get("completed_at"),
    }


def _print_proposal_run_table(rows: list[dict]) -> None:
    if not rows:
        print("No proposal runs.")
        return
    print(f"{'id':<18} {'state':<12} {'review':<18} {'topics':>6}  created / completed")
    for r in rows:
        stamps = f"{r['created_at'] or '-'} / {r['completed_at'] or '-'}"
        print(f"{r['id']:<18} {r['state']:<12} {r['review_state']:<18} "
              f"{r['topic_count']:>6}  {stamps}")


@topics_app.command(
    "proposal-list",
    help="List proposal runs (id, run state, review state, topic count)")
def cmd_topics_proposal_list(
    repo: str | None = typer.Option(None, "--repo", help="Repository path"),
    state: str | None = typer.Option(
        None, "--state", help="Filter by run state (e.g. completed, failed)"),
    as_json: bool = typer.Option(False, "--json", help="Emit machine-readable JSON"),
) -> None:
    from lib.topics.proposals import list_proposal_runs

    rp = _repo_path(repo)
    runs = list_proposal_runs(rp)
    if state:
        runs = [r for r in runs if r.get("state") == state]
    rows = [_proposal_run_row(rp, r) for r in runs]
    if as_json:
        print(json.dumps(rows, indent=2))
        return
    _print_proposal_run_table(rows)


def _load_proposal_or_none(rp: Path, proposal_id: str) -> dict | None:
    from lib.topics.proposals import load_proposal
    try:
        return load_proposal(rp, proposal_id)
    except (OSError, ValueError, TopicGraphError):
        return None


def _proposal_topic_rows(topics: list) -> list[dict]:
    return [{
        "id": t.get("id"),
        "label": t.get("label") or t.get("id"),
        "review_status": t.get("review_status") or "pending",
    } for t in topics if isinstance(t, dict)]


def _thread_snippet(thread: dict) -> str:
    comments = thread.get("comments") or []
    body = str((comments[0] or {}).get("body") or "") if comments else ""
    lines = body.strip().splitlines()
    return lines[0] if lines else ""


def _feedback_thread_rows(threads: list[dict], open_only: bool = False) -> list[dict]:
    return [{
        "id": t.get("id"),
        "kind": t.get("kind"),
        "proposal_topic_id": t.get("proposal_topic_id"),
        "resolution_state": t.get("resolution_state"),
        "snippet": _thread_snippet(t),
    } for t in threads if not (open_only and t.get("resolution_state") != "open")]


def _print_proposal_show(
    proposal_id: str, status: dict, review_state: str | None,
    topic_rows: list[dict], thread_rows: list[dict],
) -> None:
    print(f"Proposal {proposal_id}")
    print(f"  run state: {status.get('state') or 'unknown'}")
    print(f"  review state: {review_state or '-'}")
    if status.get("agent"):
        print(f"  agent: {status['agent']}")
    if status.get("error"):
        print(f"  error: {status['error']}")
    print(f"  topics ({len(topic_rows)}):")
    for row in topic_rows:
        print(f"    {row['id']}  [{row['review_status']}]  {row['label']}")
    print(f"  open feedback threads ({len(thread_rows)}):")
    for t in thread_rows:
        print(f"    #{t['id']}  {t['kind']}  {t['resolution_state']}  {t['snippet']}")


@topics_app.command(
    "proposal-show",
    help="Show one proposal run: status, review state, topics, open feedback")
def cmd_topics_proposal_show(
    proposal_id: str = typer.Argument(..., help="Proposal run id"),
    repo: str | None = typer.Option(None, "--repo", help="Repository path"),
    as_json: bool = typer.Option(False, "--json", help="Emit machine-readable JSON"),
) -> None:
    from lib.topics.proposals import (
        list_proposal_feedback_threads, load_proposal_status,
        proposal_review_state,
    )

    rp = _repo_path(repo)
    try:
        status = load_proposal_status(rp, proposal_id)
    except TopicGraphError as exc:
        print(f"Proposal show failed: {exc}")
        raise typer.Exit(1)
    proposal = _load_proposal_or_none(rp, proposal_id)
    review_state = proposal_review_state(proposal) if proposal else None
    topic_rows = _proposal_topic_rows((proposal or {}).get("topics") or [])
    thread_rows = _feedback_thread_rows(
        list_proposal_feedback_threads(rp, proposal_id), open_only=True,
    )
    if as_json:
        print(json.dumps({
            "id": proposal_id,
            "status": status,
            "review_state": review_state,
            "topics": topic_rows,
            "open_feedback_threads": thread_rows,
        }, indent=2))
        return
    _print_proposal_show(proposal_id, status, review_state, topic_rows, thread_rows)


def _print_topic_delta_line(delta: dict) -> None:
    changes = []
    for key, label in (("ref_adds", "+ref"), ("ref_removes", "-ref"),
                       ("alias_adds", "+alias"), ("alias_removes", "-alias"),
                       ("edge_adds", "+edge"), ("edge_removes", "-edge"),
                       ("scalar_changes", "field")):
        n = len(delta.get(key) or [])
        if n:
            changes.append(f"{n} {label}")
    detail = ", ".join(changes) if changes else "no field-level changes"
    print(f"  {delta.get('kind', '?'):<8} {delta.get('topic_id')}  ({detail})")


def _print_diff_summary(diff: dict) -> None:
    target = f" → {diff['target_topic_id']}" if diff.get("target_topic_id") else ""
    print(f"strategy: {diff.get('strategy')}{target}")
    for delta in diff.get("topic_deltas") or []:
        _print_topic_delta_line(delta)
    errors = diff.get("introduced_errors") or []
    warnings = diff.get("graph_warnings") or []
    print(f"introduced errors: {len(errors)}, graph warnings: {len(warnings)}")
    for issue in errors:
        print(f"  error: {issue.get('message')}")
    print(f"applyable: {'yes' if diff.get('is_applyable') else 'NO'}")


@topics_app.command(
    "proposal-diff",
    help="Preview what applying a proposed topic would change (no writes)")
def cmd_topics_proposal_diff(
    proposal_id: str = typer.Argument(..., help="Proposal run id"),
    topic_id: str = typer.Argument(..., help="Proposed topic id inside the run"),
    strategy: str = typer.Option(
        "create", "--strategy", help="create | replace | merge"),
    target: str | None = typer.Option(
        None, "--target", help="Approved topic id (merge/replace target)"),
    repo: str | None = typer.Option(None, "--repo", help="Repository path"),
    as_json: bool = typer.Option(False, "--json", help="Emit machine-readable JSON"),
) -> None:
    from lib.topics.proposals import diff_proposal_topic

    try:
        result = diff_proposal_topic(
            str(_repo_path(repo)), proposal_id, topic_id,
            strategy=strategy, target_topic_id=target,
        )
    except (LookupError, ValueError, TopicGraphError) as exc:
        print(f"Diff failed: {exc}")
        raise typer.Exit(1)
    if as_json:
        print(json.dumps(result, indent=2))
        return
    _print_diff_summary(result["diff"])


@topics_app.command(
    "proposal-apply",
    help="Apply a proposed topic to the approved graph (writes a snapshot)")
def cmd_topics_proposal_apply(
    proposal_id: str = typer.Argument(..., help="Proposal run id"),
    topic_id: str = typer.Argument(..., help="Proposed topic id inside the run"),
    strategy: str = typer.Option(
        "create", "--strategy", help="create | replace | merge"),
    target: str | None = typer.Option(
        None, "--target", help="Approved topic id (merge/replace target)"),
    repo: str | None = typer.Option(None, "--repo", help="Repository path"),
    as_json: bool = typer.Option(False, "--json", help="Emit machine-readable JSON"),
) -> None:
    from lib.topics.proposals import apply_proposal_topic

    try:
        result = apply_proposal_topic(
            str(_repo_path(repo)), proposal_id, topic_id,
            strategy=strategy, target_topic_id=target,
        )
    except (LookupError, ValueError, TopicGraphError) as exc:
        print(f"Apply failed: {exc}")
        raise typer.Exit(1)
    if as_json:
        print(json.dumps(result, indent=2))
        if not result.get("ok"):
            raise typer.Exit(1)
        return
    if not result.get("ok"):
        print("Apply blocked: unresolvable errors")
        for issue in (result.get("diff") or {}).get("introduced_errors") or []:
            print(f"  error: {issue.get('message')}")
        raise typer.Exit(1)
    if result.get("already_applied"):
        print(f"{topic_id}: already applied (snapshot {result['snapshot_id']})")
        return
    print(f"Applied {topic_id} from {proposal_id} (snapshot {result['snapshot_id']})")


# The user-settable subset — draft/partially_applied/applied are derived
# states the apply path owns; mirrors the web review-state endpoint.
_CLI_REVIEW_STATES = ("pending_review", "changes_requested", "ready_to_apply")


@topics_app.command(
    "proposal-review-state",
    help="Set a proposal run's review state "
         "(pending_review | changes_requested | ready_to_apply)")
def cmd_topics_proposal_review_state(
    proposal_id: str = typer.Argument(..., help="Proposal run id"),
    state: str = typer.Argument(..., help="New review state"),
    repo: str | None = typer.Option(None, "--repo", help="Repository path"),
) -> None:
    from lib.topics.proposals import set_proposal_review_state

    if state not in _CLI_REVIEW_STATES:
        print(f"Review state failed: state must be one of {list(_CLI_REVIEW_STATES)}")
        raise typer.Exit(1)
    try:
        set_proposal_review_state(_repo_path(repo), proposal_id, state)
    except TopicGraphError as exc:
        print(f"Review state failed: {exc}")
        raise typer.Exit(1)
    print(f"{proposal_id}: review state set to {state}")


def _print_feedback_threads(rp: Path, proposal_id: str, as_json: bool) -> None:
    from lib.topics.proposals import list_proposal_feedback_threads

    threads = list_proposal_feedback_threads(rp, proposal_id)
    if as_json:
        print(json.dumps(threads, indent=2))
        return
    if not threads:
        print(f"No feedback threads on {proposal_id}.")
        return
    for row in _feedback_thread_rows(threads):
        anchored = f" [{row['proposal_topic_id']}]" if row["proposal_topic_id"] else ""
        print(f"#{row['id']}  {row['kind']}{anchored}  {row['resolution_state']}  {row['snippet']}")


def _create_feedback_thread(
    rp: Path, proposal_id: str, *, body: str, topic: str | None,
    kind: str, as_json: bool,
) -> None:
    from lib.topics.proposals import create_proposal_feedback_thread

    try:
        thread = create_proposal_feedback_thread(
            rp, proposal_id,
            proposal_topic_id=topic,
            kind=kind,
            # The web files a whole-topic comment as proposal_summary; match
            # it so the auto-addressed sweep treats CLI feedback the same.
            anchor_kind="proposal_summary" if topic else "general",
            body=body,
        )
    except TopicGraphError as exc:
        print(f"Feedback failed: {exc}")
        raise typer.Exit(1)
    if as_json:
        print(json.dumps(thread, indent=2))
        return
    where = f" on topic {topic}" if topic else ""
    print(f"Feedback thread #{thread['id']} created{where} ({proposal_id})")


@topics_app.command(
    "proposal-feedback",
    help="Add a feedback thread to a proposal run (--body), or list its "
         "threads (--list)")
def cmd_topics_proposal_feedback(
    proposal_id: str = typer.Argument(..., help="Proposal run id"),
    body: str | None = typer.Option(None, "--body", help="Comment body for a new thread"),
    topic: str | None = typer.Option(
        None, "--topic", help="Anchor the thread to this proposed topic id"),
    kind: str = typer.Option("comment", "--kind", help="Thread kind (default: comment)"),
    list_threads: bool = typer.Option(
        False, "--list", help="List feedback threads instead of creating one"),
    repo: str | None = typer.Option(None, "--repo", help="Repository path"),
    as_json: bool = typer.Option(False, "--json", help="Emit machine-readable JSON"),
) -> None:
    if list_threads == bool(body):
        raise typer.BadParameter(
            "pass --body to create a thread or --list to list (exactly one)")
    rp = _repo_path(repo)
    if list_threads:
        _print_feedback_threads(rp, proposal_id, as_json)
        return
    _create_feedback_thread(
        rp, proposal_id, body=body, topic=topic, kind=kind, as_json=as_json,
    )


@topics_app.command(
    "proposal-export",
    help="Export an in-flight proposal run to a portable bundle "
         "(commit the JSON to share it — no server needed)")
def cmd_topics_proposal_export(
    proposal_id: str = typer.Argument(..., help="Proposal run id"),
    out: str | None = typer.Option(
        None, "--out",
        help="Output path (default: .regin/topics/bundles/<id>.json)"),
    repo: str | None = typer.Option(None, "--repo", help="Repository path"),
) -> None:
    import subprocess

    from lib.topics.proposals import export_proposal_bundle

    rp = _repo_path(repo)
    try:
        path = export_proposal_bundle(rp, proposal_id, out_path=out)
    except TopicGraphError as exc:
        print(f"Proposal export failed: {exc}")
        raise typer.Exit(1)
    print(f"Exported proposal {proposal_id} to {path}")
    ignored = subprocess.run(
        ["git", "-C", str(rp), "check-ignore", "-q", str(path)],
        capture_output=True,
    ).returncode == 0
    if ignored:
        print("NOTE: this repo's .gitignore still ignores the bundle "
              f"(no re-include block to patch) — stage it with: git add -f {path}")
    print("Commit the bundle to share it; teammates run "
          "`regin topics proposal-import <path>`.")


@topics_app.command(
    "proposal-import",
    help="Import a teammate's proposal bundle into the local DB "
         "(review continues locally in the WebUI/CLI)")
def cmd_topics_proposal_import(
    bundle_path: str = typer.Argument(..., help="Path to a proposal bundle JSON"),
    force: bool = typer.Option(
        False, "--force",
        help="Replace an existing local run (+ revisions + feedback) wholesale"),
    repo: str | None = typer.Option(None, "--repo", help="Repository path"),
) -> None:
    from lib.topics.proposals import import_proposal_bundle

    try:
        result = import_proposal_bundle(_repo_path(repo), bundle_path, force=force)
    except TopicGraphError as exc:
        print(f"Proposal import failed: {exc}")
        raise typer.Exit(1)
    if result["action"] == "refused":
        print(f"Proposal import refused: {result['message']}")
        print("Re-run with --force to replace the local run wholesale.")
        raise typer.Exit(1)
    verb = "Replaced local" if result["action"] == "replaced" else "Imported"
    print(f"{verb} proposal {result['proposal_id']}: "
          f"{result['revisions']} revision(s), "
          f"{result['threads']} feedback thread(s)")


@topics_app.command("check", help="Validate topic graph schema and approved refs")
def cmd_topics_check(
    repo: str | None = typer.Option(None, "--repo", help="Repository path"),
) -> None:
    result = validate(_repo_path(repo))
    for warning in result.warnings:
        print(f"warning: {warning}")
    if not result.ok:
        for error in result.errors:
            print(f"error: {error}")
        raise typer.Exit(1)
    print("Topic graph is valid.")


def _resolve_repo_id_for_import(repo_path: Path) -> int:
    """Return Repo.id for `repo_path`, or exit(1) with a helpful message."""
    from sqlmodel import select

    from lib.orm import SessionLocal
    from lib.orm.models import Repo

    p = str(repo_path.resolve())
    with SessionLocal() as s:
        repo_row = s.exec(select(Repo).where(Repo.path == p)).first()
    if repo_row is None or repo_row.id is None:
        print(f"Repo not registered: run `regin add-repo {repo_path}` first")
        raise typer.Exit(1)
    return repo_row.id


def _read_disk_state(repo_path: Path) -> tuple[dict, dict[str, str]]:
    """Return (merged graph, wikis) from disk, or exit(1) on read failure.

    The graph is the effective merge of the base graph and the local
    ``topic.local.json`` overlay, matching what `load_authoritative_graph`
    captures into a snapshot — so the import sync compares like for like.
    """
    from lib.topics.core import TopicGraphError, load_graph_merged, topic_split_dir
    from lib.topics.graph_io import _read_wiki_pages_from_disk

    try:
        disk_graph = load_graph_merged(repo_path)
    except (OSError, json.JSONDecodeError, TopicGraphError) as exc:
        print(f"Failed to read {topic_split_dir(repo_path)}: {exc}")
        raise typer.Exit(1)
    return disk_graph, _read_wiki_pages_from_disk(repo_path, disk_graph)


def _snap_matches_disk(snap, disk_graph: dict, disk_wikis: dict[str, str]) -> bool:
    """True iff `snap`'s graph + wiki bodies equal the on-disk content."""
    from lib.topics.graph_io import _graph_hash

    snap_graph = json.loads(snap.graph_json)
    snap_wikis = json.loads(snap.wiki_pages_json or "{}")
    return _graph_hash(disk_graph) == _graph_hash(snap_graph) and disk_wikis == snap_wikis


def _latest_snapshot_row(repo_id: int):
    """Return the `is_latest=1` GraphSnapshot row for `repo_id`, or None."""
    from sqlmodel import select

    from lib.orm import SessionLocal
    from lib.orm.models import GraphSnapshot

    with SessionLocal() as s:
        return s.exec(
            select(GraphSnapshot)
            .where(GraphSnapshot.repo_id == repo_id)
            .where(GraphSnapshot.is_latest == 1)
        ).first()


@topics_app.command(
    "reconcile",
    help="Reconcile stale proposal 'accepted' markers against the approved graph")
def cmd_topics_reconcile(
    repo: str | None = typer.Option(None, "--repo", help="Repository path"),
    fix: bool = typer.Option(
        False, "--fix",
        help="Clear stale 'accepted' markers via the recovery primitive"),
) -> None:
    from lib.topics.reconcile import (
        find_stale_acceptances,
        fix_stale_acceptances,
        record_reconcile_audit,
    )

    rp = _repo_path(repo)
    stale = find_stale_acceptances(rp)
    if not stale:
        print("No stale acceptances — every accepted proposal topic is "
              "present in the approved graph.")
        return

    print(f"Stale acceptances ({len(stale)} topic(s) accepted but absent "
          f"from the approved graph):")
    for item in stale:
        runs = ", ".join(item["runs"])
        print(f"  • {item['topic_id']}  (last accepted@{item['accepted_at']}; "
              f"runs: {runs})")
    record_reconcile_audit(rp, stale, fixed=fix)

    if not fix:
        print("\nReport-only. Re-run with --fix to clear these stale markers.")
        return

    reset = fix_stale_acceptances(rp, stale)
    print(f"\nFixed: cleared {reset} stale 'accepted' marker(s) across "
          f"proposal rows.")


@topics_app.command("import", help="Sync the on-disk graph + wikis into a new GraphSnapshot")
def cmd_topics_import(
    repo: str | None = typer.Option(None, "--repo", help="Repository path"),
    reason: str = typer.Option(
        "manual",
        "--reason",
        help="Provenance tag for the snapshot (e.g. git_pull, manual)",
    ),
    quiet: bool = typer.Option(
        False, "--quiet",
        help="Suppress 'already in sync' output (intended for git hooks)",
    ),
) -> None:
    """Sync the on-disk approved graph into a new GraphSnapshot.

    Idempotent: no-op when the on-disk graph + per-topic wikis
    already match the latest snapshot. Use after `git pull` (manually
    or via the post-merge hook installed by `regin topics install-hook`)
    so a teammate's approved topics become routable locally — multi-user
    sharing without a shared database.
    """
    from lib.topics.core import graph_exists, topic_dir, topic_path, topic_split_dir
    from lib.topics.graph_io import load_authoritative_graph, sync_snapshot_from_disk

    repo_path = _repo_path(repo)
    if not graph_exists(repo_path):
        # Not gated by --quiet: the post-merge hook runs quiet, and a
        # legacy-only repo would otherwise fail totally silently.
        if topic_path(repo_path).exists():
            print(f"Legacy single-file layout retired: {topic_path(repo_path)} — "
                  "restore the split layout (.regin/topics/topics/) from git; "
                  "nothing imported")
        elif not quiet:
            print(f"No topic graph under {topic_dir(repo_path)} — nothing to import")
        return

    repo_id = _resolve_repo_id_for_import(repo_path)
    disk_graph, disk_wikis = _read_disk_state(repo_path)
    topic_count = len(disk_graph.get("topics") or {})
    snap = _latest_snapshot_row(repo_id)

    if snap is None:
        # No prior snapshot — drive through load_authoritative_graph so
        # the Phase 0 auto-seed path (which ingests wikis) takes over.
        load_authoritative_graph(str(repo_path))
        print(
            f"Seeded snapshot from {topic_split_dir(repo_path).name}/ (reason=auto_seed, "
            f"{topic_count} topics, {len(disk_wikis)} wikis)"
        )
        return

    if _snap_matches_disk(snap, disk_graph, disk_wikis):
        if not quiet:
            print("Already in sync")
        return

    snap_id = sync_snapshot_from_disk(repo_path, reason=reason)
    if snap_id is None:
        print(f"Import failed: sync_snapshot_from_disk returned None for {repo_path}")
        raise typer.Exit(1)
    print(
        f"Imported snapshot id={snap_id} (reason={reason}, "
        f"{topic_count} topics, {len(disk_wikis)} wikis)"
    )


@topics_app.command(
    "install-hook",
    help="Install git hooks (pre-commit, post-merge, post-checkout) for topic sync",
)
def cmd_topics_install_hook(
    repo: str | None = typer.Option(None, "--repo", help="Repository path"),
) -> None:
    paths = install_topic_hooks(_repo_path(repo))
    for name, path in paths.items():
        print(f"Installed {name} hook: {path}")


@topics_app.command("router-skill", help="Print the generic topic-router skill")
def cmd_topics_router_skill(
    repo: str | None = typer.Option(None, "--repo", help="Repository path"),
) -> None:
    print(generate_topic_router_skill(_repo_path(repo)))


@topics_app.command(
    "rebuild-query-df",
    help="Rebuild the router's query-log term-frequency cache (query_df.json)",
)
def cmd_topics_rebuild_query_df(
    repo: str | None = typer.Option(None, "--repo", help="Repository path"),
) -> None:
    from lib.topics import rebuild_query_df

    count = rebuild_query_df(_repo_path(repo))
    print(f"query_df.json rebuilt from {count} routed prompt(s)")


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
        help="Attribute emitted drift cards to this Claude Code session so the "
             "inbox card links back to the run that detected the drift. "
             "Defaults to $CLAUDE_CODE_SESSION_ID."),
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


@topics_app.command("audit", help="Show graph-wide validation issues grouped by code")
def cmd_topics_audit(
    repo: str | None = typer.Option(None, "--repo", help="Repository path"),
) -> None:
    from lib.topics.graph_io import load_authoritative_graph
    from lib.topics.validation import audit_graph
    from lib.topics.bulk_fix import AUTO_FIXABLE_CODES

    repo_path = _repo_path(repo)
    try:
        graph = load_authoritative_graph(repo_path)
    except TopicGraphError as exc:
        print(f"Audit failed: {exc}")
        raise typer.Exit(1)
    issues = audit_graph(graph, repo_path=repo_path)
    by_code: dict[str, list] = {}
    for issue in issues:
        by_code.setdefault(issue.code, []).append({
            "severity": issue.severity,
            "message": issue.message,
            "topic_ids": list(issue.topic_ids),
            "paths": list(issue.paths),
            "aliases": list(issue.aliases),
        })
    print(json.dumps({
        "error_count": sum(1 for i in issues if i.severity == "error"),
        "warning_count": sum(1 for i in issues if i.severity == "warning"),
        "auto_fixable_codes": sorted(AUTO_FIXABLE_CODES),
        "by_code": by_code,
    }, indent=2, sort_keys=True))


@topics_app.command("audit-fix", help="Bulk-fix auto-resolvable audit issues (dead refs, orphan edges)")
def cmd_topics_audit_fix(
    codes: list[str] = typer.Option(
        None, "--code",
        help="Issue codes to fix (repeatable). Defaults to all auto-fixable codes.",
    ),
    repo: str | None = typer.Option(None, "--repo", help="Repository path"),
) -> None:
    from lib.topics.apply import apply_diff
    from lib.topics.bulk_fix import AUTO_FIXABLE_CODES, compose_fix
    from lib.topics.diff import GraphDiff, compute_topic_delta
    from lib.topics.graph_io import load_authoritative_graph
    from lib.topics.snapshots import resolve_or_create_repo
    from lib.topics.validation import audit_graph

    repo_path = _repo_path(repo)
    requested = set(codes) if codes else set(AUTO_FIXABLE_CODES)
    skipped = sorted(requested - AUTO_FIXABLE_CODES)
    actionable = requested & AUTO_FIXABLE_CODES
    if not actionable:
        print(f"No auto-fixable codes selected. Auto-fixable: {sorted(AUTO_FIXABLE_CODES)}")
        if skipped:
            print(f"Skipped (manual): {skipped}")
        return
    graph = load_authoritative_graph(repo_path)
    issues = audit_graph(graph, repo_path=repo_path)
    fixes = compose_fix(graph, issues, codes_to_fix=actionable)
    if not fixes:
        print(json.dumps({"snapshot_ids": [], "fixed_counts": {c: 0 for c in actionable}, "skipped_codes": skipped}, indent=2))
        return
    repo_row = resolve_or_create_repo(str(repo_path))
    snapshot_ids: list[int] = []
    fixed_counts: dict[str, int] = {c: 0 for c in actionable}
    running = json.loads(json.dumps(graph))
    for topic_id, cleaned, before in fixes:
        prospective = json.loads(json.dumps(running))
        prospective.setdefault("topics", {})[topic_id] = cleaned
        delta = compute_topic_delta(topic_id_after=topic_id, kind="replace", before=before, after=cleaned)
        diff = GraphDiff(
            topic_deltas=(delta,), graph_warnings=(), introduced_errors=(),
            valid_strategies_by_topic={topic_id: ("replace",)},
            strategy="replace", target_topic_id=None,
            proposed_topic_id=topic_id, prospective_graph=prospective,
        )
        result = apply_diff(repo_row.id, diff, reason="bulk_fix")
        snapshot_ids.append(result.snapshot_id)
        fixed_counts["graph.dead_ref"] = fixed_counts.get("graph.dead_ref", 0) + len(delta.ref_removes)
        fixed_counts["graph.orphan_edge_target"] = fixed_counts.get("graph.orphan_edge_target", 0) + len(delta.edge_removes)
        running = prospective
    print(json.dumps({
        "snapshot_ids": snapshot_ids,
        "fixed_counts": fixed_counts,
        "skipped_codes": skipped,
    }, indent=2))


def _split_target(graph: dict, leaf_id: str) -> str:
    """Resolve + validate the bucket the new siblings hang under, or exit."""
    from lib.topics.split_leaf import bucket_for_leaf

    if leaf_id not in graph.get("topics", {}):
        print(f"unknown leaf {leaf_id!r}")
        raise typer.Exit(1)
    bucket_id = bucket_for_leaf(graph, leaf_id)
    if bucket_id is None:
        print(f"leaf {leaf_id!r} is not directly under a bucket — "
              "cannot place sibling sub-topics")
        raise typer.Exit(1)
    return bucket_id


def _print_split_plan(plan, gate) -> None:
    """Human preview: the move-map grouped by destination, then gate verdict."""
    by_dest: dict[str, list[str]] = {}
    for mid, dest in plan.assignment.items():
        by_dest.setdefault(dest, []).append(mid)
    print(f"split {plan.leaf_id} → bucket {plan.bucket_id}")
    for tid in plan.new_topics:
        node = plan.new_topics[tid]
        print(f"  + {tid}  ({len(by_dest.get(tid, []))} mem)  {node['label']}")
    kept = len(by_dest.get(plan.leaf_id, []))
    if kept:
        print(f"  · {plan.leaf_id} keeps {kept} (overview/unplaced)")
    print(f"gate: {'PASS' if gate.ok else 'FAIL'} "
          f"({len(gate.errors)} errors, {len(gate.warnings)} warnings)")
    for e in gate.errors:
        print(f"  ERROR: {e}")
    for w in gate.warnings:
        print(f"  warn:  {w}")


@topics_app.command(
    "split-leaf",
    help="Cluster an over-large topic leaf into sibling sub-topics and relink "
         "its memories (dry-run + gate by default; --apply to write).")
def cmd_topics_split_leaf(
    leaf_id: str = typer.Argument(..., help="The leaf topic id to split"),
    apply: bool = typer.Option(
        False, "--apply", help="Write the split. Default: dry-run preview + gate."),
    repo: str | None = typer.Option(None, "--repo", help="Repository path"),
    allow_protected_move: bool = typer.Option(
        False, "--allow-protected-move",
        help="Permit moving manual/reflect-sourced links off the leaf"),
    min_clusters: int = typer.Option(2, "--min-clusters"),
    max_clusters: int = typer.Option(5, "--max-clusters"),
) -> None:
    import lib.memory as memory
    from lib.memory.adapters import resolve_topic_classifier
    from lib.topics.graph_io import load_authoritative_graph
    from lib.topics.split_gate import gather_leaf_links
    from lib.topics.split_leaf import (
        ClusterProposerUnavailable, apply_split, build_split_plan,
        gate_only, propose_clusters,
    )

    repo_path = _repo_path(repo)
    graph = load_authoritative_graph(repo_path)
    bucket_id = _split_target(graph, leaf_id)

    store = memory.get_store()
    leaf_links = gather_leaf_links(store, leaf_id)
    if not leaf_links:
        print(f"leaf {leaf_id!r} has no linked memories — nothing to split")
        raise typer.Exit(1)
    # Fetch only the leaf's memories by id — `gather_leaf_links` already
    # filtered to active links, so scanning the whole active table (and
    # capping it) would be wasted work and could silently drop memories.
    mems = [m for m in (store.get_dict(mid) for mid in leaf_links) if m]

    try:
        clusters = propose_clusters(
            graph["topics"][leaf_id], mems, resolve_topic_classifier(),
            lo=min_clusters, hi=max_clusters)
    except ClusterProposerUnavailable as exc:
        print(f"cluster proposer unavailable: {exc}")
        raise typer.Exit(2)
    if not clusters:
        print("proposer returned no clusters — leaving leaf intact")
        raise typer.Exit(1)

    plan = build_split_plan(leaf_id, bucket_id, clusters, leaf_links, graph)
    gate = gate_only(store, repo_path, plan, graph,
                     allow_protected_move=allow_protected_move)
    _print_split_plan(plan, gate)

    if not apply:
        print("\n(dry-run — pass --apply to write)")
        return
    if not gate.ok:
        print("\nGATE FAILED — not applying.")
        raise typer.Exit(1)
    result = apply_split(store, repo_path, plan, graph,
                         allow_protected_move=allow_protected_move)
    print(f"\napplied: {len(result['new_topics'])} sub-topics created, "
          f"{result['moved']} memories moved, "
          f"{result['kept_on_leaf']} kept on the leaf")


def _flat_topics(graph: dict) -> list[dict]:
    """The topics eligible for grouping: non-bucket nodes whose
    `effective_parent` is the reserved `unclassified` bucket (no real bucket
    ancestor). Buckets themselves are never grouped."""
    from lib.topics.tree import UNCLASSIFIED, effective_parent, is_bucket

    topics = graph.get("topics") or {}
    buckets = {tid for tid, n in topics.items()
               if isinstance(n, dict) and is_bucket(n)}
    flat: list[dict] = []
    for tid, node in topics.items():
        if tid in buckets:
            continue
        if effective_parent(topics, buckets, tid) == UNCLASSIFIED:
            flat.append({"id": tid, "label": node.get("label") or tid,
                         "intent": node.get("intent") or ""})
    return flat


def _print_group_plan(plan, gate) -> None:
    """Human preview: each new bucket + its member count/label, then verdict."""
    counts: dict[str, int] = {}
    for bid in plan.assignment.values():
        counts[bid] = counts.get(bid, 0) + 1
    print(f"group {len(plan.assignment)} flat topics → {len(plan.new_buckets)} buckets")
    for bid, body in plan.new_buckets.items():
        print(f"  + {bid}  ({counts.get(bid, 0)} topics)  {body['label']}")
    print(f"gate: {'PASS' if gate.ok else 'FAIL'} "
          f"({len(gate.errors)} errors, {len(gate.warnings)} warnings)")
    for e in gate.errors:
        print(f"  ERROR: {e}")
    for w in gate.warnings:
        print(f"  warn:  {w}")


@topics_app.command(
    "group",
    help="Cluster the flat/unclassified topics into top-level buckets and "
         "reparent them (dry-run + gate by default; --apply to write).")
def cmd_topics_group(
    apply: bool = typer.Option(
        False, "--apply", help="Write the grouping. Default: dry-run + gate."),
    repo: str | None = typer.Option(None, "--repo", help="Repository path"),
    min_buckets: int = typer.Option(3, "--min-buckets"),
    max_buckets: int = typer.Option(8, "--max-buckets"),
) -> None:
    from lib.memory.adapters import resolve_topic_classifier
    from lib.topics.graph_io import load_authoritative_graph
    from lib.topics.group_topics import (
        ClusterProposerUnavailable, apply_group, build_group_plan,
        gate_only, propose_buckets,
    )

    repo_path = _repo_path(repo)
    graph = load_authoritative_graph(repo_path)

    flat_topics = _flat_topics(graph)
    if not flat_topics:
        print("no flat topics to group — the taxonomy is already bucketed")
        raise typer.Exit(0)

    try:
        clusters = propose_buckets(
            flat_topics, resolve_topic_classifier(),
            lo=min_buckets, hi=max_buckets)
    except ClusterProposerUnavailable as exc:
        print(f"cluster proposer unavailable: {exc}")
        raise typer.Exit(2)
    if not clusters:
        print("proposer returned no buckets — leaving the taxonomy flat")
        raise typer.Exit(1)

    flat_ids = {t["id"] for t in flat_topics}
    plan = build_group_plan(clusters, flat_ids, graph)
    gate = gate_only(repo_path, plan, graph)
    _print_group_plan(plan, gate)

    if not apply:
        print("\n(dry-run — pass --apply to write)")
        return
    if not gate.ok:
        print("\nGATE FAILED — not applying.")
        raise typer.Exit(1)
    result = apply_group(repo_path, plan, graph)
    print(f"\napplied: {len(result['new_buckets'])} buckets created, "
          f"{result['grouped']} topics grouped")
