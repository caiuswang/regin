"""`regin trace ...` — trace-data maintenance commands."""

from __future__ import annotations

import glob
import os
from datetime import datetime, timedelta

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


def _skill_attribution_payloads(trace_id: str, usage) -> list[dict]:
    """One `tool_attribution` payload per turn that launched a skill.

    Scoped to `Skill` calls so a re-scan can only ever correct a skill's
    own row — every other tool's live attribution is left untouched.
    """
    payloads = []
    for turn in usage.turns:
        calls = [
            {'tool_use_id': c.get('id'), 'name': c.get('name'),
             'input_tokens': c.get('input_token_estimate'),
             'output_tokens': c.get('output_token_estimate'),
             'image_tokens': c.get('image_token_estimate')}
            for c in turn.tool_calls
            if c.get('name') == 'Skill' and isinstance(c.get('id'), str)
            and c.get('skill_payload_token_estimate')
        ]
        if calls:
            payloads.append({'trace_id': trace_id, 'turn_uuid': turn.uuid,
                             'tool_calls': calls})
    return payloads


def _backfill_skill_one(provider, ingest, trace_id: str, counts: dict) -> None:
    """Re-attribute one session's skill launches, mutating `counts`."""
    transcript = _find_transcript(trace_id)
    if transcript is None:
        counts['skipped'] += 1
        return
    usage = provider.parse_transcript(transcript)
    if usage is None or not usage.turns:
        counts['skipped'] += 1
        return
    payloads = _skill_attribution_payloads(trace_id, usage)
    if not payloads:
        counts['skipped'] += 1
        return
    for payload in payloads:
        ingest(payload)
    counts['updated'] += 1


@trace_app.command("backfill-skill-tokens",
                   help="Re-attribute Skill launches' injected bodies to their "
                        "tool.Skill spans for existing sessions (the body "
                        "arrives separately from the one-line tool_result, so "
                        "sessions traced before this landed under-report it)")
def cmd_backfill_skill_tokens(
    session: str = typer.Option(
        "", "--session", help="Only this trace id (default: every session)",
    ),
    limit: int = typer.Option(
        0, "--limit", help="Stop after this many sessions (0 = no limit)",
    ),
) -> None:
    provider = get_active_provider()
    if not provider.capabilities.transcript_usage:
        print(f"not supported for provider: {provider.display_name}")
        raise typer.Exit(2)

    from lib.orm.engine import get_connection
    from lib.trace.trace_service import ingest_tool_attribution

    conn = get_connection()
    if session:
        trace_ids = [session]
    else:
        trace_ids = [r['trace_id'] for r in conn.execute(
            "SELECT DISTINCT trace_id FROM session_spans WHERE name = 'tool.Skill'"
        ).fetchall()]

    counts = {'updated': 0, 'skipped': 0}
    for trace_id in trace_ids:
        if limit and counts['updated'] >= limit:
            break
        _backfill_skill_one(provider, ingest_tool_attribution, trace_id, counts)
    print(f"Done. sessions_updated={counts['updated']} "
          f"skipped={counts['skipped']}")


@trace_app.command("backfill-model",
                   help="Set sessions.model from stored transcript spans for "
                        "sessions that never resolved one (e.g. llm-stage runs "
                        "whose model lived only on assistant_response spans)")
def cmd_backfill_model(
    only_missing: bool = typer.Option(
        True, "--only-missing/--all",
        help="Skip sessions that already have model set",
    ),
    limit: int = typer.Option(
        0, "--limit",
        help="Stop after updating this many sessions (0 = no limit)",
    ),
) -> None:
    from lib.trace.trace_service import backfill_session_models

    counts = backfill_session_models(only_missing=only_missing, limit=limit)
    print(
        f"Done. updated={counts['updated']} "
        f"unchanged={counts['unchanged']} no_model={counts['no_model']}"
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


# Each provider's ended-session repair path: the module holding its
# `discover_subagent_sessions()` work list, its reconciler, and the result
# counters worth printing per session. Those counters are totals RE-stamped,
# not deltas — the reconcilers report no mutation count, so nothing here can
# honestly claim a session "changed" (see `_SUBAGENT_IDEMPOTENT_NOTE`).
_SUBAGENT_BACKFILL = {
    "kimi": ("lib.trace.kimi_subagents", "reconcile_kimi_subagents",
             ("tool_spans", "turns", "launches_closed")),
    "claude": ("lib.trace.claude_subagents", "reconcile_claude_subagents",
               ("stamped", "nested_parented")),
}

# sqlite's SQLITE_MAX_VARIABLE_NUMBER is 999 on builds before 3.32; the trace
# store only grows, so chunk rather than trust the local build's ceiling.
_SQL_VARS_PER_CHUNK = 900

# Same "this session has gone quiet" window the serve-time merge uses
# (`lib.trace.pending_spans.INACTIVE_THRESHOLD_SEC`), so the CLI and the
# renderer agree on which sessions count as still running.
_DEFAULT_IDLE_MINUTES = 10

_STALE_SERVER_NOTE = (
    "restart `regin serve` on current code before a real run — a hook that "
    "reaches a server still running the OLD reconciler re-applies its "
    "nesting, silently undoing this backfill."
)

_SUBAGENT_IDEMPOTENT_NOTE = (
    "Counters are totals (re)stamped, not deltas: the reconcilers are "
    "idempotent and report the same numbers on a repeat run."
)


def _subagent_providers(provider: str) -> list[str]:
    """Provider names selected by `--provider` (`all` = every reconciler)."""
    if provider == "all":
        return list(_SUBAGENT_BACKFILL)
    if provider not in _SUBAGENT_BACKFILL:
        raise typer.BadParameter(
            f"unknown provider '{provider}'; expected one of "
            f"{', '.join([*_SUBAGENT_BACKFILL, 'all'])}")
    return [provider]


def _idle_cutoff(idle_minutes: int) -> datetime:
    """A session quiet since before this instant is not live."""
    # Cap so an absurd --idle-minutes reports as "nothing is eligible" rather
    # than an OverflowError traceback.
    return datetime.now() - timedelta(minutes=min(idle_minutes, 5_000_000))


def _has_tz_marker(stamp) -> bool:
    """Does an ISO stamp state its own zone? (`...Z` / `±HH:MM`, never the
    hyphens in the date part.)"""
    if not isinstance(stamp, str):
        return False
    time_part = stamp[10:]
    return stamp.endswith("Z") or "+" in time_part or "-" in time_part


def _is_live_session(status, last_seen, cutoff: datetime) -> bool:
    """Inverse of `lib.trace.merge._session_is_inactive`, and it must stay
    that way — the CLI and the renderer disagreeing about who is running is
    exactly how a live session gets synthetic `subagent.stop` markers.

    `last_seen` arrives in several shapes (naive local from the hook path,
    `...Z` and space-separated UTC from sqlite's `datetime('now')`), so it is
    parsed, never compared as a string. An unreadable stamp counts as live:
    the safe direction.

    For a stamp that names no zone the effective window is
    `idle_minutes + the host's UTC offset`, since either reading has to be
    stale before the session is touched. East of UTC that widens the window
    by up to 14 hours — it only delays a repair, where the opposite error
    stamps a running subagent as finished.
    """
    from lib.trace.pending_spans import parse_naive_ts
    if status == "ended":
        return False
    seen = parse_naive_ts(last_seen)
    if seen is None or seen > cutoff:
        return True
    if _has_tz_marker(last_seen):
        return False
    offset = datetime.now().astimezone().utcoffset()
    return offset is not None and seen + offset > cutoff


def _session_backfill_state(trace_ids: list[str],
                            cutoff: datetime) -> tuple[set[str], set[str]]:
    """`(ids that have a sessions row, of those the ones still live)`."""
    if not trace_ids:
        return set(), set()
    from lib.orm.engine import get_connection
    known: set[str] = set()
    live: set[str] = set()
    conn = get_connection()
    try:
        for start in range(0, len(trace_ids), _SQL_VARS_PER_CHUNK):
            chunk = trace_ids[start:start + _SQL_VARS_PER_CHUNK]
            rows = conn.execute(
                "SELECT trace_id, status, last_seen FROM sessions "
                f"WHERE trace_id IN ({','.join('?' * len(chunk))})",
                chunk).fetchall()
            for row in rows:
                known.add(row["trace_id"])
                if _is_live_session(row["status"], row["last_seen"], cutoff):
                    live.add(row["trace_id"])
    finally:
        conn.close()
    return known, live


def _subagent_work_list(module, session: str | None,
                        cutoff: datetime) -> tuple[list[str], int, int]:
    """`(trace_ids to reconcile, skipped for no row, skipped for still live)`.

    Reconciling a LIVE session here would be actively harmful: the reconcilers
    run in their non-`live` mode, which mints synthetic `subagent.stop`
    markers, so a subagent still running would render as finished.
    """
    # dict.fromkeys so the skip counts below stay set-arithmetic-safe even if
    # a discoverer ever stops de-duplicating.
    found = list(dict.fromkeys(module.discover_subagent_sessions()))
    if session:
        found = [t for t in found if t == session]
    known, live = _session_backfill_state(found, cutoff)
    todo = [t for t in found if t in known and t not in live]
    return todo, len(found) - len(known), len(live)


def _reconcile_subagent_sessions(reconcile, report_keys: tuple,
                                 trace_ids: list[str], limit: int) -> dict:
    """Run one provider's reconciler over `trace_ids`, summing its result
    counters and printing the sessions that reported any work.

    A session whose reconcile raises is counted and reported, not fatal: a
    single unreadable transcript must not abandon the rest of the sweep.
    """
    totals: dict[str, float] = {}
    scanned = failed = 0
    for trace_id in trace_ids[:limit or None]:
        try:
            result = reconcile(trace_id)
            # Sum into a scratch dict first: a half-summed malformed result
            # must not leave phantom counters in a summary that reports the
            # session as failed.
            merged = {**totals,
                      **{k: totals.get(k, 0) + v for k, v in result.items()}}
        except Exception as exc:
            failed += 1
            print(f"    {trace_id}: FAILED — {type(exc).__name__}: {exc}")
            continue
        totals = merged
        scanned += 1
        if any(result.get(k) for k in report_keys):
            print(f"    {trace_id}: {result}")
    return {"scanned": scanned, "failed": failed, "totals": totals}


def _skip_suffix(no_row: int, live: int) -> str:
    """The `, N skipped (…)` tail of a provider's work-list line."""
    parts = []
    if no_row:
        parts.append(f"{no_row} with no `sessions` row")
    if live:
        parts.append(f"{live} still live")
    return f", skipped {' and '.join(parts)}" if parts else ""


def _run_subagent_backfill(name: str, session: str | None, limit: int,
                           dry_run: bool,
                           cutoff: datetime) -> tuple[int, int]:
    """Reconcile one provider's ended sessions (or list them, when dry-run).

    Returns `(sessions whose reconcile raised, sessions reconciled)`.
    """
    import importlib
    module_path, fn_name, report_keys = _SUBAGENT_BACKFILL[name]
    module = importlib.import_module(module_path)
    trace_ids, no_row, live = _subagent_work_list(module, session, cutoff)
    print(f"  {name}: {len(trace_ids)} eligible session(s) with subagent "
          f"transcripts{_skip_suffix(no_row, live)}")
    if not trace_ids:
        return 0, 0
    if dry_run:
        for trace_id in trace_ids[:limit or None]:
            print(f"    would reconcile {trace_id}")
        return 0, 0
    stats = _reconcile_subagent_sessions(
        getattr(module, fn_name), report_keys, trace_ids, limit)
    detail = " ".join(f"{k}={v}" for k, v in sorted(stats["totals"].items()))
    failed = f", {stats['failed']} failed" if stats["failed"] else ""
    print(f"  {name}: reconciled {stats['scanned']} session(s){failed}"
          f"{' — ' + detail if detail else ''}")
    return stats["failed"], stats["scanned"]


def _sweep_providers(names: list[str], session: str | None, limit: int,
                     dry_run: bool,
                     cutoff: datetime) -> tuple[int, int]:
    """Run every selected provider, isolating a provider-level failure so one
    provider's unreadable transcript dir cannot abandon the other's sweep."""
    failed = reconciled = 0
    for name in names:
        try:
            provider_failed, provider_done = _run_subagent_backfill(
                name, session, limit, dry_run, cutoff)
        except Exception as exc:
            failed += 1
            print(f"  {name}: FAILED — {type(exc).__name__}: {exc}")
            continue
        failed += provider_failed
        reconciled += provider_done
    return failed, reconciled


@trace_app.command(
    "backfill-subagents",
    help="Re-run subagent nesting/attribution over already-ended sessions — "
         "the repair path for sessions ingested under an older reconciler. "
         "Writes only with --yes, and only after you restart `regin serve` on "
         "current code (a hook reaching the old server undoes the backfill)")
def cmd_backfill_subagents(
    provider: str = typer.Option(
        "all", "--provider", "-p",
        help="Whose reconciler to run: kimi, claude, or all"),
    session: str = typer.Option(
        None, "--session", "-s",
        help="Limit to one trace_id (default: every discovered session)"),
    limit: int = typer.Option(
        0, "--limit",
        help="Stop after this many sessions per provider (0 = no limit)"),
    idle_minutes: int = typer.Option(
        _DEFAULT_IDLE_MINUTES, "--idle-minutes",
        help="Treat a session quiet for at least this long as ended. 0 drops "
             "the quiet window entirely — only safe against a stopped server; "
             "a row with an unreadable last_seen is skipped either way"),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Report the work list without writing"),
    yes: bool = typer.Option(
        False, "--yes", "-y",
        help="Confirm a real (non-dry-run) run; required to actually write"),
) -> None:
    """Walk each provider's `discover_subagent_sessions()` and re-run its
    reconciler over sessions that have already ended.

    Every live trigger point (`SubagentStart` / `SubagentStop`, the `Stop` +
    `SessionEnd` sweep, the trace view's rescan poll) needs a *live* session,
    so a session that already ended keeps whatever attribution the reconciler
    produced at ingest time — forever, including attribution from a reconciler
    that has since been fixed. This is the supported way to repair those.
    Idempotent.

    Skipped: trace ids with no `sessions` row, and sessions that are still
    live. The liveness guard is not cosmetic — the reconcilers run in their
    non-`live` mode here, which mints synthetic `subagent.stop` markers, so
    reconciling a running session would render its in-flight subagents as
    finished until the next rescan pass repaired them.

    Writing requires `--yes`; without it the run is forced to `--dry-run`,
    mirroring `reap-pending` / `prune`, because it rewrites span parentage on
    the live DB.

    Restart `regin serve` on current code before a real run: a hook that
    reaches a server still running the old reconciler re-applies that server's
    nesting, silently undoing the backfill.

    The printed counters are what each reconciler (re)stamped, not a diff —
    they do not shrink on a second run. A session whose reconcile raises is
    reported and skipped rather than aborting the sweep; the command exits 1
    if any did.
    """
    names = _subagent_providers(provider)
    if session is not None and not session.strip():
        raise typer.BadParameter("--session was empty; omit it to sweep every "
                                 "discovered session")
    limit = max(limit, 0)
    if not dry_run and not yes:
        print("Refusing to rewrite spans without confirmation. Re-run with "
              "--dry-run to preview, or add --yes to commit the backfill. "
              f"Either way, {_STALE_SERVER_NOTE}")
        dry_run = True
    if not dry_run:
        print(f"Note: {_STALE_SERVER_NOTE}")
    verb = "Would reconcile" if dry_run else "Reconciling"
    print(f"{verb} ended sessions for: {', '.join(names)}")
    failed, reconciled = _sweep_providers(
        names, session, limit, dry_run, _idle_cutoff(max(idle_minutes, 0)))
    if reconciled:
        print(_SUBAGENT_IDEMPOTENT_NOTE)
    if failed:
        print(f"{failed} session(s)/provider(s) failed; re-run to retry.")
        raise typer.Exit(1)


def register_trace(app: typer.Typer) -> None:
    """Hook point called from cli/app.py to attach the `trace` subapp."""
    app.add_typer(trace_app)
