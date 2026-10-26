"""Cost recomputation helpers behind `regin trace backfill-costs`."""

from __future__ import annotations


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
        cache_creation_1h_tokens=r['cache_creation_1h_tokens'] or 0,
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
               cache_read_tokens, cache_creation_tokens,
               cache_creation_1h_tokens, context_used_tokens
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
