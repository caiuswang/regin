"""`regin trace ...` — trace-data maintenance commands."""

from __future__ import annotations

import glob
import os

import typer

from lib.providers import get_active_provider

trace_app = typer.Typer(
    name="trace", help="Session trace maintenance",
    no_args_is_help=True,
)


def _richest_model(conn, trace_id: str, fallbacks: tuple) -> str | None:
    """Prefer a session.start model id that includes a `[variant]`
    suffix (which the SessionStart hook payload carries but the
    transcript `message.model` strips). Falls back through the
    supplied alternatives in order.
    """
    import json as _json
    rows = conn.execute("""
        SELECT attributes FROM session_spans
        WHERE trace_id = ? AND name = 'session.start'
        ORDER BY start_time DESC
    """, (trace_id,)).fetchall()
    for r in rows:
        try:
            attrs = _json.loads(r['attributes']) if r['attributes'] else {}
        except (ValueError, TypeError):
            attrs = {}
        m = attrs.get('model')
        if isinstance(m, str) and '[' in m:
            return m
    for m in fallbacks:
        if isinstance(m, str) and m.strip():
            return m
    return None


def _find_transcript(trace_id: str) -> str | None:
    """Locate transcript under the active provider projects directory.

    Claude default:
    ``~/.claude/projects/<cwd-munged>/<session_id>.jsonl``.

    Providers whose on-disk layout differs from Claude's flat
    ``<projects>/*/<id>.jsonl`` (e.g. Kimi's
    ``<sessions>/wd_*/<id>/agents/main/wire.jsonl``) implement
    ``resolve_transcript_path``; route through it so the backfill matches
    the live ingest path instead of the Claude-only glob below.
    """
    provider = get_active_provider()
    from hook_manager.core import HookPayload
    resolved = provider.resolve_transcript_path(
        HookPayload(event="backfill", session_id=trace_id)
    )
    if resolved:
        return resolved
    projects_root = str(provider.transcript_projects_dir())
    candidates = glob.glob(os.path.join(projects_root, "*", f"{trace_id}.jsonl"))
    if not candidates:
        # Codex sessions are commonly sharded under ~/.codex/sessions/YYYY/MM/DD
        # and may not use the exact trace id as the filename.
        candidates = glob.glob(
            os.path.join(projects_root, "**", f"*{trace_id}*.jsonl"),
            recursive=True,
        )
    if not candidates:
        return None
    # Multiple matches are possible if a session_id ever collided across
    # different cwds — pick the largest (most recent turns).
    candidates.sort(key=lambda p: os.path.getsize(p), reverse=True)
    return candidates[0]


def _load_backfill_candidates(get_connection, only_missing: bool) -> list[tuple[str, str]]:
    """Read the (trace_id, model) sessions to consider for backfill."""
    conn = get_connection()
    try:
        where = "WHERE peak_context_tokens IS NULL" if only_missing else ""
        rows = conn.execute(
            f"SELECT trace_id, model FROM sessions {where} ORDER BY last_seen DESC"
        ).fetchall()
        print(f"Found {len(rows)} candidate sessions.")
        return [(r['trace_id'], r['model']) for r in rows]
    finally:
        conn.close()


def _persist_richest_model(get_connection, trace_id: str, usage,
                           current_model: str) -> str | None:
    """Resolve and persist a richer model id for `trace_id`.

    Computes the model once (so `infer_window` in `ingest_turn_usage`
    picks the right window) and writes it back when it differs from the
    stored value. Returns the resolved model.
    """
    conn2 = get_connection()
    try:
        model = _richest_model(conn2, trace_id,
                               fallbacks=(usage.model, current_model))
        if model and model != current_model:
            conn2.execute(
                "UPDATE sessions SET model = ? WHERE trace_id = ?",
                (model, trace_id),
            )
            conn2.commit()
        return model
    finally:
        conn2.close()


def _build_turn_payload(trace_id: str, usage, model: str | None) -> list[dict]:
    """Build the per-turn usage rows; skips turns lacking uuid/timestamp."""
    payload = []
    for idx, t in enumerate(usage.turns):
        if not t.uuid or not t.timestamp:
            continue
        payload.append({
            'trace_id': trace_id,
            'turn_uuid': t.uuid,
            'turn_index': idx,
            'timestamp': t.timestamp,
            'model': t.model or model,
            'input_tokens': t.input_tokens,
            'output_tokens': t.output_tokens,
            'cache_read_tokens': t.cache_read_tokens,
            'cache_creation_tokens': t.cache_creation_tokens,
            'context_used_tokens': t.context_used,
            'request_id': t.request_id,
        })
    return payload


def _backfill_one_session(deps, trace_id: str, current_model: str,
                          counts: dict, total: int) -> None:
    """Backfill a single session, mutating `counts` in place.

    `deps` bundles the lazily-imported collaborators
    (`get_connection`, `read_usage`, `ingest_turn_usage`). An
    empty payload is silently dropped (neither `updated` nor `empty`
    is bumped) to preserve the original behavior.
    """
    transcript = _find_transcript(trace_id)
    if transcript is None:
        counts['missing'] += 1
        return
    usage = deps['read_usage'](transcript)
    if usage is None or not usage.turns:
        counts['empty'] += 1
        return
    model = _persist_richest_model(deps['get_connection'], trace_id, usage,
                                   current_model)
    payload = _build_turn_payload(trace_id, usage, model)
    if payload:
        deps['ingest_turn_usage'](payload)
        counts['updated'] += 1
        if counts['updated'] % 50 == 0:
            print(f"  processed {counts['updated']}/{total}…")


@trace_app.command("dump",
                   help="Print a session's gradeable evidence (prompts, "
                        "final deliverable, ordered tool spans) as JSON")
def cmd_dump(
    trace_id: str = typer.Argument(..., help="Session (trace) id"),
    index: bool = typer.Option(
        False, "--index",
        help="Compact catalog only (no large span content) — pair with "
             "`trace span` to read the spans you need"),
) -> None:
    import json as _json

    from lib.grader.dump import dump_session
    print(_json.dumps(dump_session(trace_id, index_only=index),
                      indent=2, ensure_ascii=False))


@trace_app.command("span",
                   help="Print one span's full untruncated recorded content")
def cmd_span(
    trace_id: str = typer.Argument(..., help="Session (trace) id"),
    span_id: str = typer.Argument(..., help="Span id within the session"),
) -> None:
    import json as _json

    from lib.grader.dump import dump_span
    out = dump_span(trace_id, span_id)
    if out is None:
        print(f"error: span {span_id} not found in session {trace_id}")
        raise typer.Exit(1)
    print(_json.dumps(out, indent=2, ensure_ascii=False))


@trace_app.command("backfill-tokens",
                   help="Populate per-turn usage rows from on-disk transcripts for existing sessions")
def cmd_backfill_tokens(
    only_missing: bool = typer.Option(
        True, "--only-missing/--all",
        help="Skip sessions that already have peak_context_tokens set",
    ),
    limit: int = typer.Option(
        0, "--limit",
        help="Stop after processing this many sessions (0 = no limit)",
    ),
) -> None:
    provider = get_active_provider()
    if not provider.capabilities.transcript_usage:
        print(f"backfill-tokens is not supported for provider: {provider.display_name}")
        raise typer.Exit(2)

    from lib.orm.engine import get_connection
    from lib.trace.trace_service import ingest_turn_usage

    candidates = _load_backfill_candidates(get_connection, only_missing)
    deps = {
        'get_connection': get_connection,
        # Route through the active provider so non-Claude on-disk formats
        # (Kimi's wire.jsonl) parse correctly instead of the Claude-only
        # read_usage; the live ingest path uses the same method.
        'read_usage': provider.parse_transcript,
        'ingest_turn_usage': ingest_turn_usage,
    }
    counts = {'updated': 0, 'missing': 0, 'empty': 0}
    for trace_id, current_model in candidates:
        if limit and counts['updated'] >= limit:
            break
        _backfill_one_session(deps, trace_id, current_model, counts, len(candidates))
    print(
        f"Done. updated={counts['updated']} "
        f"missing_transcript={counts['missing']} empty_usage={counts['empty']}"
    )


def _resolve_one_session(conn, trace_id: str, norm) -> tuple:
    """Resolve one session's high-signal spans against `norm`.

    Returns `(start_cwd, {repo_id: is_primary})`. Reuses the exact
    membership rule from the ingest path (`_repo_signal_path`) so the
    backfill can never drift from live ingest.
    """
    import json as _json

    from lib.trace.trace_service.ingest import (
        _REPO_CWD_NAMES, _REPO_EDIT_NAMES, _repo_signal_path,
    )
    from lib.rule_engines.repo_scope import repo_for_path_norm

    names = tuple(_REPO_CWD_NAMES | _REPO_EDIT_NAMES)
    placeholders = ",".join("?" * len(names))
    rows = conn.execute(
        f"SELECT name, attributes, start_time FROM session_spans "
        f"WHERE trace_id = ? AND name IN ({placeholders}) ORDER BY start_time",
        (trace_id, *names),
    ).fetchall()

    found: dict = {}
    start_cwd = None
    for row in rows:
        try:
            attrs = _json.loads(row['attributes']) if row['attributes'] else {}
        except (ValueError, TypeError):
            attrs = {}
        name = row['name']
        if name == 'session.start' and start_cwd is None:
            cwd = attrs.get('cwd')
            if isinstance(cwd, str) and cwd.strip():
                start_cwd = cwd.strip()
        path, primary = _repo_signal_path(name, attrs)
        if not path:
            continue
        repo = repo_for_path_norm(path, norm)
        if repo is not None:
            found[repo.id] = max(found.get(repo.id, 0), primary)
    return start_cwd, found


@trace_app.command("resolve-repos",
                   help="Backfill sessions.cwd + session_repos for existing sessions")
def cmd_resolve_repos(
    only_missing: bool = typer.Option(
        True, "--only-missing/--all",
        help="Skip sessions that already have cwd set",
    ),
    limit: int = typer.Option(
        0, "--limit", help="Stop after this many sessions (0 = no limit)",
    ),
) -> None:
    """Tag existing sessions with the registered repos they touched.

    Idempotent — safe to re-run. Uses the same high-signal rule as live
    ingest: starting cwd (primary), cwd.changed, and file mutations;
    reads and bash are excluded.
    """
    from lib.orm.engine import get_connection
    from lib.trace.trace_service.ingest import (
        _active_repos_normalized, _SESSION_REPOS_UPSERT_SQL,
    )

    norm = _active_repos_normalized()
    if not norm:
        print("No active registered repos — nothing to resolve.")
        raise typer.Exit(0)

    conn = get_connection()
    try:
        where = "WHERE cwd IS NULL" if only_missing else ""
        rows = conn.execute(
            f"SELECT trace_id FROM sessions {where} ORDER BY last_seen DESC"
        ).fetchall()
        print(f"Found {len(rows)} candidate sessions.")

        scanned = 0
        tags = 0
        for r in rows:
            if limit and scanned >= limit:
                break
            tid = r['trace_id']
            start_cwd, found = _resolve_one_session(conn, tid, norm)
            if start_cwd:
                conn.execute(
                    "UPDATE sessions SET cwd = COALESCE(cwd, ?) WHERE trace_id = ?",
                    (start_cwd, tid),
                )
            for repo_id, is_primary in found.items():
                conn.execute(_SESSION_REPOS_UPSERT_SQL, (tid, repo_id, is_primary))
                tags += 1
            scanned += 1
            if scanned % 200 == 0:
                conn.commit()
                print(f"  processed {scanned}/{len(rows)}…")
        conn.commit()
        print(f"Done. sessions_scanned={scanned} repo_tags_written={tags}")
    finally:
        conn.close()


@trace_app.command("ingest-workflows",
                   help="Capture Claude Code dynamic-workflow runs into the trace DB")
def cmd_ingest_workflows(
    watch: bool = typer.Option(
        False, "--watch",
        help="Poll continuously for new/updated runs instead of one pass",
    ),
    deep: bool = typer.Option(
        True, "--deep/--no-deep",
        help="Expand each agent into per-turn / per-tool spans (completion only)",
    ),
    interval: float = typer.Option(
        5.0, "--interval", help="Watch poll interval in seconds",
    ),
) -> None:
    """Scan the provider's transcript dir for workflow runs and project each
    onto the session/span trace store (run -> phase -> agent -> turn).

    Idempotent: deterministic span ids + delete-then-rebuild, so re-running
    refreshes a run rather than duplicating it. `regin serve` runs the same
    capture in the background; this command is for one-off backfill or a
    standalone watcher.
    """
    from lib.trace.workflow_ingest import ingest_all, watch as watch_runs

    if watch:
        print(f"Watching for workflow runs every {interval}s (Ctrl-C to stop)…")
        try:
            watch_runs(interval, deep=deep)
        except KeyboardInterrupt:
            print("\nStopped.")
        return

    summary = ingest_all(deep=deep)
    print(f"Done. runs={summary['runs']} spans={summary['spans']} "
          f"failed={summary['failed']}")


def _tier_rates(rates: dict) -> tuple:
    """Unpack (base_in, base_out, over_in, over_out, threshold) per 1M.

    Reuses the same context-tier selection as `lib.tokens.pricing.cost`
    (passing a huge context picks the highest tier) so the backfill's SQL
    CASE matches live ingest. `threshold` is a sentinel beyond any real
    context size when the model is flat, so the over branch never fires.
    """
    from lib.tokens.pricing import _best_context_tier

    base_in = rates.get('input') or 0
    base_out = rates.get('output') or 0
    flat = (base_in, base_out, base_in, base_out, 1 << 62)
    tiers = rates.get('tiers')
    if not isinstance(tiers, list):
        return flat
    tier = _best_context_tier(tiers, 1 << 62)
    if not tier:
        return flat
    threshold = (tier.get('tier') or {}).get('size') or (1 << 62)
    return (base_in, base_out, tier.get('input') or base_in,
            tier.get('output') or base_out, threshold)


def _apply_model_cost(conn, model, tier: tuple, trace: str | None,
                      recompute: bool) -> None:
    """Stamp context-tiered cost_usd on one model's tool spans.

    Per span, the >threshold rate applies when that span's turn ran with
    context over the tier threshold (looked up in turn_usage by
    turn_uuid); spans whose turn has no recorded context fall to the base
    rate. recompute=False touches only NULL-cost spans.
    """
    base_in, base_out, over_in, over_out, threshold = tier
    cost_filter = "" if recompute else "AND cost_usd IS NULL"
    upd = (
        "UPDATE session_spans SET cost_usd = (CASE WHEN ("
        "    SELECT tu.context_used_tokens FROM turn_usage tu "
        "     WHERE tu.trace_id = session_spans.trace_id "
        "       AND tu.turn_uuid = session_spans.turn_uuid) > ? "
        "  THEN (? * COALESCE(input_tokens, 0) + ? * COALESCE(output_tokens, 0)) "
        "  ELSE (? * COALESCE(input_tokens, 0) + ? * COALESCE(output_tokens, 0)) "
        "END) / 1000000.0 "
        f"WHERE name LIKE 'tool.%' {cost_filter} "
        "  AND (input_tokens IS NOT NULL OR output_tokens IS NOT NULL) "
        "  AND trace_id IN (SELECT trace_id FROM sessions WHERE model IS ?)"
    )
    params = [threshold, over_in, over_out, base_in, base_out, model]
    if trace:
        upd += " AND trace_id = ?"
        params.append(trace)
    conn.execute(upd, params)


def _collect_cost_updates(conn, rows, dry_run: bool, trace: str | None,
                          recompute: bool) -> tuple:
    """Apply (or, when dry_run, just tally) cost for each candidate model.

    Returns (total_spans, total_sessions, pending) where pending lists
    (model, spans, sessions) for models models.dev can't price yet.
    """
    from lib.tokens.pricing import model_rates

    total_spans = 0
    total_sessions = 0
    pending = []
    for r in rows:
        rates = model_rates(r['model'])
        if rates is None:
            pending.append((r['model'], r['spans'], r['sessions']))
            continue
        tier = _tier_rates(rates)
        base_in, base_out, over_in, over_out, threshold = tier
        note = (f" · >{int(threshold) // 1000}k=${over_in}/${over_out}"
                if (over_in, over_out) != (base_in, base_out) else "")
        print(f"  {str(r['model']):28} in=${base_in}/Mtok out=${base_out}/Mtok{note} "
              f"→ {r['spans']} spans across {r['sessions']} session(s)")
        total_spans += r['spans']
        total_sessions += r['sessions']
        if not dry_run:
            _apply_model_cost(conn, r['model'], tier, trace, recompute)
    return total_spans, total_sessions, pending


def _print_cost_summary(total_spans: int, total_sessions: int,
                        pending: list, dry_run: bool) -> None:
    """Print the pending-models list and the one-line backfill summary."""
    if pending:
        print("\nPending (model not in models.dev catalogue yet — re-run later):")
        for model, spans, sessions in pending:
            print(f"  {str(model):32} {spans} spans across {sessions} session(s)")
    verb = "Would update" if dry_run else "Updated"
    print(f"\n{verb} {total_spans} tool spans across {total_sessions} session(s); "
          f"{len(pending)} model(s) still pending.")


def _span_cost_rows(conn, trace: str | None, recompute: bool) -> list:
    """Candidate tool-span counts grouped by session model."""
    cost_clause = "" if recompute else "AND sp.cost_usd IS NULL"
    where = (f"sp.name LIKE 'tool.%' {cost_clause} "
             "AND (sp.input_tokens IS NOT NULL OR sp.output_tokens IS NOT NULL)")
    scope = []
    if trace:
        where += " AND sp.trace_id = ?"
        scope.append(trace)
    return conn.execute(f"""
        SELECT s.model AS model, COUNT(*) AS spans,
               COUNT(DISTINCT sp.trace_id) AS sessions
        FROM session_spans sp JOIN sessions s ON s.trace_id = sp.trace_id
        WHERE {where}
        GROUP BY s.model ORDER BY spans DESC
    """, scope).fetchall()


def _turn_row_cost(r) -> float | None:
    """Full per-turn API cost (input+output+cache, context-tiered)."""
    from lib.tokens.pricing import cost, TokenBreakdown

    return cost(r['model'], TokenBreakdown(
        input_tokens=r['input_tokens'] or 0,
        output_tokens=r['output_tokens'] or 0,
        cache_read_tokens=r['cache_read_tokens'] or 0,
        cache_creation_tokens=r['cache_creation_tokens'] or 0,
    ), context_tokens=r['context_used_tokens'] or 0)


def _recompute_turn_costs(conn, trace: str | None, dry_run: bool,
                          recompute: bool) -> tuple:
    """Recompute turn_usage.cost_usd and re-aggregate sessions.cost_usd.

    Unlike the per-tool span cost, the per-turn bill is the full API cost
    — input + output + cache_read + cache_write, context-tiered — matching
    ingest_turn_usage. Returns (turns_priced, sessions_touched).
    """
    cost_clause = "" if recompute else "AND cost_usd IS NULL"
    where = f"1=1 {cost_clause}"
    scope = []
    if trace:
        where += " AND trace_id = ?"
        scope.append(trace)
    rows = conn.execute(f"""
        SELECT trace_id, turn_uuid, model, input_tokens, output_tokens,
               cache_read_tokens, cache_creation_tokens, context_used_tokens
        FROM turn_usage WHERE {where}
    """, scope).fetchall()

    updates = []
    touched = set()
    for r in rows:
        usd = _turn_row_cost(r)
        if usd is None:
            continue
        updates.append((usd, r['trace_id'], r['turn_uuid']))
        touched.add(r['trace_id'])
    if not dry_run and updates:
        conn.executemany(
            "UPDATE turn_usage SET cost_usd = ? "
            "WHERE trace_id = ? AND turn_uuid = ?", updates)
        conn.executemany(
            "UPDATE sessions SET cost_usd = "
            "(SELECT SUM(cost_usd) FROM turn_usage WHERE trace_id = ?) "
            "WHERE trace_id = ?", [(t, t) for t in touched])
    return len(updates), len(touched)


@trace_app.command("backfill-costs",
                   help="Recompute NULL cost_usd from now-available models.dev rates")
def cmd_backfill_costs(
    trace: str = typer.Option(
        None, "--trace", help="Limit to one trace_id (default: every session)",
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Report what would change without writing",
    ),
    recompute: bool = typer.Option(
        False, "--recompute",
        help="Also re-price rows that already have a cost (e.g. after a "
             "pricing or context-tier fix), not just NULL-cost ones",
    ),
) -> None:
    """Recompute `cost_usd` from current models.dev rates, two ways.

    `cost_usd` is stamped once at ingest from the session model's
    models.dev rate (ingest.py). If that model wasn't in the catalogue
    yet — or the fetch failed (pricing degrades silently to None so it
    never blocks ingest) — the row keeps its token counts but a NULL
    cost, and nothing recomputes it.

    Two stores are fixed:
      • session_spans.cost_usd — the per-tool cost the "Tokens by tool"
        rollup sums. Only `tool.%` spans, input+output only (image/cache
        excluded), matching live ingest — costing assistant_response /
        assistant.thinking spans would over-report vs fresh ingest.
      • turn_usage.cost_usd — the full per-turn API bill (input + output
        + cache), re-aggregated into sessions.cost_usd (the session-total
        cost shown in the sessions list).

    Both use the shared `lib.tokens.pricing.cost` path, so they pick up
    context-tiered pricing (1M-context Claude models bill the higher rate
    above 200K), keyed per turn on context_used_tokens.

    Idempotent: by default only NULL-cost rows are updated, so correctly
    priced rows stay untouched and the same command fills new gaps as the
    catalogue catches up. Pass --recompute to re-price already-costed rows
    after a pricing/tier change.
    """
    from lib.orm.engine import get_connection

    conn = get_connection()
    try:
        span_rows = _span_cost_rows(conn, trace, recompute)
        print("Tool spans (Tokens-by-tool rollup):")
        if span_rows:
            span_totals = _collect_cost_updates(
                conn, span_rows, dry_run, trace, recompute)
        else:
            print("  none")
            span_totals = (0, 0, [])
        turns, turn_sessions = _recompute_turn_costs(
            conn, trace, dry_run, recompute)
        verb = "would re-price" if dry_run else "re-priced"
        print(f"\nTurn usage (session totals): {verb} {turns} turns "
              f"across {turn_sessions} session(s)")
        if not dry_run:
            conn.commit()
    finally:
        conn.close()

    _print_cost_summary(*span_totals, dry_run)


@trace_app.command(
    "reap-pending",
    help="Physically delete superseded PENDING placeholder spans that "
         "merge_spans already hides (the prune path for the append-only store)")
def cmd_reap_pending(
    session: str = typer.Option(
        None, "--session", "-s", help="Limit to one trace_id (default: every "
        "session with pending placeholders)"),
    idle_minutes: int = typer.Option(
        0, "--idle-minutes", help="Only sweep sessions idle at least this long "
        "(0 = no filter; merge already protects in-flight rows)"),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Report what would be deleted without writing"),
    yes: bool = typer.Option(
        False, "--yes", "-y",
        help="Confirm a real (non-dry-run) deletion; required to actually write"),
    limit: int = typer.Option(
        0, "--limit", help="Stop after this many traces (0 = no limit)"),
) -> None:
    """Delete the transient `promptlive-`/`pending-`/`permreq-` rows whose
    resolved counterpart is already present, so `session_spans` stops growing
    unbounded. Deletes ONLY rows the serve-time merge already hides, so the
    rendered trace is unchanged; in-flight placeholders and slash-command
    expansion sources are preserved. Idempotent — safe to re-run.

    A real deletion requires `--yes`; without it the run is forced to
    `--dry-run` (report-only). This guard exists because the delete is
    irreversible and hits the live DB by default — preview first, then
    confirm."""
    from lib.trace.reap import reap_pending_spans

    if not dry_run and not yes:
        print("Refusing to delete without confirmation. Re-run with --dry-run "
              "to preview, or add --yes to commit the deletion.")
        dry_run = True

    result = reap_pending_spans(
        session=session, idle_minutes=idle_minutes or None,
        dry_run=dry_run, limit=limit)
    verb = "Would reap" if dry_run else "Reaped"
    print(f"{verb} {result['rows_reaped']} placeholder span(s) across "
          f"{result['traces_touched']} of {result['traces_scanned']} "
          f"scanned trace(s).")


def _print_prune_result(result: dict, dry_run: bool) -> None:
    """Render the per-table tally and the one-line summary."""
    if not result["enabled"]:
        print("Nothing to do. Enable at least one mode: --purge-test, "
              "--orphans, or --days N (e.g. --days 60).")
        return
    verb = "Would delete" if dry_run else "Deleted"
    for table, n in sorted(result["by_table"].items(),
                           key=lambda kv: kv[1], reverse=True):
        print(f"  {verb.lower():13} {n:>9,}  {table}")
    print(f"{verb} {result['rows']:,} row(s) across "
          f"{len(result['by_table'])} table(s) [{', '.join(result['enabled'])}].")
    if not dry_run:
        print("Space reclaimed to OS." if result["vacuumed"]
              else "VACUUM skipped (DB busy or --no-vacuum); "
                   "run again with --vacuum when idle to shrink the file.")


@trace_app.command(
    "prune",
    help="Delete whole sessions' trace data — test fixtures, orphans, and an "
         "age cutoff — then VACUUM (the retention path for the trace store)")
def cmd_prune(
    purge_test: bool = typer.Option(
        False, "--purge-test",
        help="Remove is_test=1 fixture sessions entirely (test-run leakage)"),
    orphans: bool = typer.Option(
        False, "--orphans",
        help="Remove child rows whose trace_id has no sessions row"),
    days: int = typer.Option(
        0, "--days",
        help="Retention cutoff: drop heavy detail of real sessions older than "
             "N days, keeping the aggregate row (0 = off; 60 recommended)"),
    drop_sessions: bool = typer.Option(
        False, "--drop-sessions",
        help="With --days, also delete the aggregate `sessions` row, not just "
             "its detail"),
    vacuum: bool = typer.Option(
        True, "--vacuum/--no-vacuum",
        help="After a real delete, VACUUM to return freed pages to the OS"),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Report what would be deleted without writing"),
    yes: bool = typer.Option(
        False, "--yes", "-y",
        help="Confirm a real (non-dry-run) deletion; required to actually write"),
) -> None:
    """Prune whole sessions from the append-only trace store.

    Mirrors `reap-pending`'s safety: a real deletion requires `--yes`; without
    it the run is forced to `--dry-run` (report-only), because the delete is
    irreversible and hits the live DB by default. Enable one or more modes;
    with none enabled it reports guidance and writes nothing."""
    from lib.trace.prune import prune_trace_data

    if not dry_run and not yes:
        print("Refusing to delete without confirmation. Re-run with --dry-run "
              "to preview, or add --yes to commit the deletion.")
        dry_run = True

    result = prune_trace_data(
        purge_test=purge_test, orphans=orphans, days=days,
        drop_sessions=drop_sessions, dry_run=dry_run, vacuum=vacuum)
    _print_prune_result(result, dry_run)


def register_trace(app: typer.Typer) -> None:
    """Hook point called from cli/app.py to attach the `trace` subapp."""
    app.add_typer(trace_app)
