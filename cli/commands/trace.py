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
    """
    provider = get_active_provider()
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
    from lib.trace.transcript_usage import read_usage
    from lib.trace.trace_service import ingest_turn_usage

    conn = get_connection()
    try:
        where = ""
        if only_missing:
            where = "WHERE peak_context_tokens IS NULL"
        rows = conn.execute(
            f"SELECT trace_id, model FROM sessions {where} ORDER BY last_seen DESC"
        ).fetchall()
        print(f"Found {len(rows)} candidate sessions.")

        candidates: list[tuple[str, str]] = [(r['trace_id'], r['model']) for r in rows]
    finally:
        conn.close()

    updated = 0
    missing = 0
    empty = 0
    for trace_id, current_model in candidates:
        if limit and updated >= limit:
            break
        transcript = _find_transcript(trace_id)
        if transcript is None:
            missing += 1
            continue
        usage = read_usage(transcript)
        if usage is None or not usage.turns:
            empty += 1
            continue
        # Compute a richer model once, apply it to every row — so
        # infer_window in ingest_turn_usage picks the right window.
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
        finally:
            conn2.close()

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
        if payload:
            ingest_turn_usage(payload)
            updated += 1
            if updated % 50 == 0:
                print(f"  processed {updated}/{len(candidates)}…")
    print(f"Done. updated={updated} missing_transcript={missing} empty_usage={empty}")


@trace_app.command("backfill-active-work",
                   help="Compute and persist sessions.active_work_ms for existing rows")
def cmd_backfill_active_work(
    only_missing: bool = typer.Option(
        True, "--only-missing/--all",
        help="Skip sessions that already have active_work_ms set",
    ),
) -> None:
    """One-shot fix-up after migration 0004 added the column.

    Re-runs the projection pipeline (graft + widen) on every session's
    spans and writes the union-of-roots duration back to the row, so
    the list view's Active column shows real data instead of '-' for
    pre-migration sessions. Idempotent — safe to re-run.
    """
    from lib.orm.engine import get_connection
    from lib.trace.projection import (
        _compute_active_work_ms, _fetch_spans,
        _graft_orphans, _widen_envelopes,
    )

    conn = get_connection()
    try:
        where = "WHERE active_work_ms IS NULL" if only_missing else ""
        rows = conn.execute(
            f"SELECT trace_id FROM sessions {where} ORDER BY last_seen DESC"
        ).fetchall()
        trace_ids = [r['trace_id'] for r in rows]
        print(f"Found {len(trace_ids)} candidate sessions.")

        updated = 0
        empty = 0
        for tid in trace_ids:
            raw = _fetch_spans(conn, tid)
            if not raw:
                empty += 1
                continue
            widened = _widen_envelopes(_graft_orphans(raw))
            conn.execute(
                "UPDATE sessions SET active_work_ms = ? WHERE trace_id = ?",
                (_compute_active_work_ms(widened), tid),
            )
            updated += 1
            if updated % 100 == 0:
                print(f"  processed {updated}/{len(trace_ids)}…")
        conn.commit()
        print(f"Done. updated={updated} empty_spans={empty}")
    finally:
        conn.close()


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


def register_trace(app: typer.Typer) -> None:
    """Hook point called from cli/app.py to attach the `trace` subapp."""
    app.add_typer(trace_app)
