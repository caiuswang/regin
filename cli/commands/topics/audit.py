"""`regin topics` graph repair and restructuring: audit, audit-fix, split-leaf
and group."""

from __future__ import annotations

import json

import typer

from lib.topics import TopicGraphError

from ._common import _repo_path, topics_app


@topics_app.command("audit", help="Show graph-wide validation issues grouped by code")
def cmd_topics_audit(
    repo: str | None = typer.Option(None, "--repo", help="Repository path"),
) -> None:
    from lib.topics.graph_io import load_authoritative_graph
    from lib.topics.validation import (
        INFORMATIONAL_DISPLAY_CODES,
        audit_graph,
        count_by_display_severity,
    )
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
    counts = count_by_display_severity(issues)
    print(json.dumps({
        "error_count": counts["error"],
        "warning_count": counts["warning"],
        "info_count": counts["info"],
        "auto_fixable_codes": sorted(AUTO_FIXABLE_CODES),
        "informational_codes": sorted(INFORMATIONAL_DISPLAY_CODES),
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
