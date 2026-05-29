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


def _apply_model_cost(conn, model, in_rate: float, out_rate: float,
                      trace: str | None) -> None:
    """Stamp cost_usd on NULL-cost tool spans for one session model."""
    upd = (
        "UPDATE session_spans "
        "SET cost_usd = (? * COALESCE(input_tokens, 0) "
        "                + ? * COALESCE(output_tokens, 0)) / 1000000.0 "
        "WHERE name LIKE 'tool.%' AND cost_usd IS NULL "
        "  AND (input_tokens IS NOT NULL OR output_tokens IS NOT NULL) "
        "  AND trace_id IN (SELECT trace_id FROM sessions WHERE model IS ?)"
    )
    params = [in_rate, out_rate, model]
    if trace:
        upd += " AND trace_id = ?"
        params.append(trace)
    conn.execute(upd, params)


def _collect_cost_updates(conn, rows, dry_run: bool, trace: str | None) -> tuple:
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
        in_rate = rates.get('input') or 0
        out_rate = rates.get('output') or 0
        print(f"  {str(r['model']):32} in=${in_rate}/Mtok out=${out_rate}/Mtok "
              f"→ {r['spans']} spans across {r['sessions']} session(s)")
        total_spans += r['spans']
        total_sessions += r['sessions']
        if not dry_run:
            _apply_model_cost(conn, r['model'], in_rate, out_rate, trace)
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


@trace_app.command("backfill-costs",
                   help="Recompute NULL cost_usd from now-available models.dev rates")
def cmd_backfill_costs(
    trace: str = typer.Option(
        None, "--trace", help="Limit to one trace_id (default: every session)",
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Report what would change without writing",
    ),
) -> None:
    """Fill in per-tool `cost_usd` that was NULL at ingest time.

    `cost_usd` is stamped once at ingest from the session model's
    models.dev rate (ingest.py). If that model wasn't in the catalogue
    yet — or the fetch failed (pricing degrades silently to None so it
    never blocks ingest) — the span keeps its token counts but a NULL
    cost, and nothing recomputes it. This recomputes cost for every
    tool span whose cost is still NULL and whose model resolves today,
    using the same input+output formula as live ingest (image and cache
    tokens excluded, matching ingest).

    Only `tool.%` spans are touched: live ingest never costs
    assistant_response / assistant.thinking spans, so backfilling them
    would make these sessions report a higher attributed cost than
    freshly-ingested ones of the same model.

    Idempotent and re-runnable: only NULL-cost spans are updated, so
    correctly-priced rows stay untouched. Models still missing from
    models.dev are reported as pending — re-run once the catalogue
    catches up.
    """
    from lib.orm.engine import get_connection

    base_where = (
        "sp.name LIKE 'tool.%' "
        "AND sp.cost_usd IS NULL "
        "AND (sp.input_tokens IS NOT NULL OR sp.output_tokens IS NOT NULL)"
    )
    scope = []
    if trace:
        base_where += " AND sp.trace_id = ?"
        scope.append(trace)

    conn = get_connection()
    try:
        rows = conn.execute(f"""
            SELECT s.model AS model,
                   COUNT(*) AS spans,
                   COUNT(DISTINCT sp.trace_id) AS sessions
            FROM session_spans sp
            JOIN sessions s ON s.trace_id = sp.trace_id
            WHERE {base_where}
            GROUP BY s.model
            ORDER BY spans DESC
        """, scope).fetchall()
        if not rows:
            print("No NULL-cost tool spans found — nothing to backfill.")
            raise typer.Exit(0)
        totals = _collect_cost_updates(conn, rows, dry_run, trace)
        if not dry_run:
            conn.commit()
    finally:
        conn.close()

    _print_cost_summary(*totals, dry_run)


def register_trace(app: typer.Typer) -> None:
    """Hook point called from cli/app.py to attach the `trace` subapp."""
    app.add_typer(trace_app)
