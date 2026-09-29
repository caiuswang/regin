"""Per-tool token rollup for one session: tool rows, per-target
breakdown and the session bill totals.
"""

from __future__ import annotations

import json

def _command_str(tool_input: object) -> str | None:
    """Pull the command/pattern string out of a stored `tool_input`
    attribute. It may be a dict, a JSON string, or a Python-repr string
    (the Bash hook stored some as `str(dict)`, which isn't valid JSON)."""
    import ast
    val = tool_input
    if isinstance(val, str):
        parsed = None
        for loader in (json.loads, ast.literal_eval):
            try:
                parsed = loader(val)
            except (ValueError, SyntaxError, TypeError):
                continue
            break
        val = parsed
    if isinstance(val, dict):
        return val.get('command') or val.get('pattern') or val.get('glob')
    return None


_FILE_TOOLS = ('Read', 'Edit', 'Write', 'NotebookEdit')


_CMD_TOOLS = ('Bash', 'Grep', 'Glob')


_AGENT_TOOLS = ('Agent', 'Task')


def _nonempty_str(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _file_target(attrs: dict) -> tuple[str, str] | None:
    path = _nonempty_str(attrs.get('file_path') or attrs.get('notebook_path'))
    return (path, path.rsplit('/', 1)[-1]) if path else None


def _cmd_target(attrs: dict) -> tuple[str, str] | None:
    """`(full, label)` for a command-style tool (Bash/Grep/Glob), or None.

    The command/pattern lives in different attributes across capture paths:
    the live hook path (`post_tool_trace`) stores `command`/`command_preview`
    for Bash and `pattern` for Grep/Glob, while the workflow-ingest path stores
    the raw `tool_input` dict. Reading only `tool_input` (the old behaviour)
    left every hook-captured Bash/Grep/Glob span with no drill-down target, so
    the per-tool rollup's jump-to-span button was permanently disabled for them.
    """
    cmd = (_nonempty_str(attrs.get('command'))
           or _command_str(attrs.get('tool_input'))
           or _nonempty_str(attrs.get('command_preview'))
           or _nonempty_str(attrs.get('pattern'))
           or _nonempty_str(attrs.get('glob'))
           or _nonempty_str(attrs.get('query')))
    flat = ' '.join((cmd or '').split())
    if not flat:
        return None
    return flat, (flat[:160] + '…' if len(flat) > 160 else flat)


def _agent_target(attrs: dict) -> tuple[str, str] | None:
    desc = _nonempty_str(attrs.get('description') or attrs.get('subagent_type'))
    return (desc, desc) if desc else None


def _skill_target(attrs: dict) -> tuple[str, str] | None:
    name = _nonempty_str(attrs.get('skill_name'))
    return (name, name) if name else None


def _span_target(attrs: dict) -> tuple[str, str] | None:
    """`(full_target, display_label)` a tool call drills into, or None when
    the tool has no meaningful per-call target. file_path for file tools,
    the command for Bash/Grep/Glob, the description for subagents, the
    skill name for Skill launches."""
    tool = attrs.get('tool_name')
    if tool == 'Skill':
        return _skill_target(attrs)
    if tool in _FILE_TOOLS:
        return _file_target(attrs)
    if tool in _CMD_TOOLS:
        return _cmd_target(attrs)
    if tool in _AGENT_TOOLS:
        return _agent_target(attrs)
    return None


def _clean_target_cell(c: dict) -> dict:
    """Public shape of a drill-down target — drops the internal peak tracker."""
    return {'target': c['target'], 'label': c['label'], 'tokens': c['tokens'],
            'calls': c['calls'], 'span_id': c['span_id']}


def _tool_target_breakdown(conn, trace_id: str, per_tool: int = 8) -> dict:
    """Per-tool top targets by token cost: `{tool_key: [{target, label,
    tokens, calls, span_id}, ...]}`, top `per_tool` each. `tool_key` matches
    `fetch_tool_token_rollup`'s row name (`attributes.tool_name`), so the UI
    can hang each tool's drill-down off its rollup row. Token cost is
    input+output — the SAME total the rollup row shows — so the per-target
    sums reconcile with the tool subtotal. (For Read/Bash that's ~all result
    tokens; for Edit/Write the bulk is the OUTPUT — the edit/write content
    the model emitted — which input-only would have hidden.) `span_id` is the
    single most expensive call for that target, for jump-to-span."""
    rows = conn.execute(
        "SELECT json_extract(attributes, '$.tool_name') AS tool, "
        "span_id, attributes, "
        "COALESCE(input_tokens, 0) + COALESCE(output_tokens, 0) AS tok "
        "FROM session_spans "
        "WHERE trace_id = ? AND name LIKE 'tool.%' "
        "AND status_code != 'PENDING' "
        "AND (input_tokens > 0 OR output_tokens > 0)",
        (trace_id,),
    ).fetchall()
    agg: dict = {}
    for r in rows:
        try:
            attrs = json.loads(r['attributes'] or '{}')
        except (ValueError, TypeError):
            continue
        target = _span_target(attrs)
        if not r['tool'] or target is None:
            continue
        full, label = target
        tok = int(r['tok'] or 0)
        cell = agg.setdefault(r['tool'], {}).setdefault(
            full, {'target': full, 'label': label, 'tokens': 0, 'calls': 0,
                   'span_id': None, 'peak': -1})
        cell['tokens'] += tok
        cell['calls'] += 1
        if tok > cell['peak']:
            cell['peak'], cell['span_id'] = tok, r['span_id']
    return {
        tool: [_clean_target_cell(c) for c in
               sorted(cells.values(), key=lambda c: -c['tokens'])[:per_tool]]
        for tool, cells in agg.items()
    }


def _rollup_row(r, targets_by_tool: dict) -> dict:
    """Build one per-tool rollup row, hanging its drill-down targets off the
    same `tool` key the breakdown was aggregated under."""
    name = r[0] or ''
    return {
        'name': name,
        'calls': int(r[1] or 0),
        'input_tokens': int(r[2] or 0),
        'output_tokens': int(r[3] or 0),
        'image_tokens': int(r[4] or 0),
        'cost_usd': float(r[5] or 0.0),
        'targets': targets_by_tool.get(name, []),
    }


def _session_bill_cost(conn, trace_id: str) -> dict:
    """Per-bucket USD cost for the full recorded session bill, keyed
    `{input, output, cache_read, cache_write}`.

    Summed per-turn from `turn_usage`, each turn priced at its own context
    tier — mirroring ingest's `_insert_one_turn_row` — so the bucket costs
    reconcile to `sessions.cost_usd`. It can't be derived from the `sessions`
    aggregate row: the >200K tier rate is per-request, and cache reads bill
    at ~1/10 the input rate, so the cost split (≈ even thirds across
    cache-read / cache-write / output here) looks nothing like the token
    split (≈ 90% cache-read). 'Tokens by tool' attributes only output by
    activity; this is the dollar context that view omits. Zeros when no turn
    carries a model the catalogue knows.
    """
    from lib.tokens.pricing import TokenBreakdown, cost_components

    out = {'input': 0.0, 'output': 0.0, 'cache_read': 0.0, 'cache_write': 0.0}
    rows = conn.execute(
        "SELECT model, input_tokens, output_tokens, cache_read_tokens, "
        "cache_creation_tokens, context_used_tokens FROM turn_usage "
        "WHERE trace_id = ?",
        (trace_id,),
    ).fetchall()
    for r in rows:
        comps = cost_components(
            r['model'],
            TokenBreakdown(
                input_tokens=int(r['input_tokens'] or 0),
                output_tokens=int(r['output_tokens'] or 0),
                cache_read_tokens=int(r['cache_read_tokens'] or 0),
                cache_creation_tokens=int(r['cache_creation_tokens'] or 0),
            ),
            context_tokens=int(r['context_used_tokens'] or 0),
        )
        if comps:
            for k in out:
                out[k] += comps[k]
    return out


def _rollup_totals(rollup: list[dict], sess_row, bill_cost: dict,
                   subagent_cost: float = 0.0,
                   subagent_tokens: int = 0) -> dict:
    """Assemble the session-level totals payload for `fetch_tool_token_rollup`.

    `attributed_*` sum the per-tool rows; `session_*` come from the recorded
    `sessions` aggregate (main model only); `untagged_*` is the output the
    rollup couldn't pin to a tool; the `*_cost_usd` quartet is the
    per-turn-priced dollar split (`_session_bill_cost`); `subagent_*` is the
    server-side sub-model spend (the advisor) that `sessions.cost_usd`
    excludes. `total_spend_*` = main bill + sub-agent is true spend — the
    honest footer total and the "$X of $Y" denominator. Kept out of the
    caller so it stays under the cyclomatic-complexity grade.
    """
    def _cell(key: str) -> int:
        return int((sess_row[key] if sess_row else 0) or 0)

    attributed_in = sum(r['input_tokens'] for r in rollup)
    attributed_out = sum(r['output_tokens'] for r in rollup)
    session_in = _cell('input_tokens')
    session_out = _cell('output_tokens')
    session_cache_read = _cell('cache_read_tokens')
    session_cache_write = _cell('cache_creation_tokens')
    session_total = (session_in + session_out
                     + session_cache_read + session_cache_write)
    session_cost = float((sess_row['cost_usd'] if sess_row else 0) or 0.0)
    return {
        'attributed_input_tokens': attributed_in,
        'attributed_output_tokens': attributed_out,
        'attributed_cost_usd': sum(r['cost_usd'] for r in rollup),
        'session_input_tokens': session_in,
        'session_output_tokens': session_out,
        'session_cache_read_tokens': session_cache_read,
        'session_cache_creation_tokens': session_cache_write,
        'session_total_tokens': session_total,
        'session_cost_usd': session_cost,
        'untagged_input_tokens': max(0, session_in - attributed_in),
        'untagged_output_tokens': max(0, session_out - attributed_out),
        # Per-bucket dollar split of the main-model bill (cache reads bill
        # ~10x cheaper than fresh input, so the cost story ≠ the token story).
        'input_cost_usd': bill_cost['input'],
        'output_cost_usd': bill_cost['output'],
        'cache_read_cost_usd': bill_cost['cache_read'],
        'cache_write_cost_usd': bill_cost['cache_write'],
        # Server-side sub-model spend (advisor), excluded from session_cost_usd.
        'subagent_cost_usd': subagent_cost,
        'subagent_tokens': subagent_tokens,
        'total_spend_usd': session_cost + subagent_cost,
        'total_spend_tokens': session_total + subagent_tokens,
    }


def fetch_tool_token_rollup(trace_id: str) -> tuple[list[dict], dict]:
    """Aggregate per-tool token cost for one session.

    Reads `session_spans` directly so the rollup works regardless of
    whether the trace UI loaded the tree shallow or full. Aggregates
    by `attributes.tool_name` (falling back to the span name) so MCP
    tools group under their full `mcp__server__tool` name.

    Returns (per_tool_rows, totals). `totals` carries the session-level
    numbers the frontend needs to put the attribution in context: the
    recorded `session_*` token aggregate (main model, incl. cache
    read/write), the per-bucket `*_cost_usd` dollar split
    (`_session_bill_cost`), the `subagent_*` server-side sub-model spend
    (the advisor) that `sessions.cost_usd` excludes, the `total_spend_*`
    (main bill + sub-agent = true spend) used as the "$X of $Y" denominator
    and footer total, and the `untagged_*` output remainder — so the panel
    reconciles to true spend instead of presenting attributed tokens as the
    whole.
    """
    from lib.orm.engine import get_connection

    conn = get_connection()
    try:
        # assistant_response spans are bucketed under a synthetic
        # `assistant_text` tool name so the chip can show prose tokens
        # alongside Bash/Read/etc. tools. assistant.thinking spans get
        # their own `assistant_thinking` bucket so extended-thinking
        # tokens (estimated from the captured thinking_text) come out
        # of the untagged remainder. Both span kinds only carry
        # output_tokens — the API doesn't expose per-block accounting
        # on the input side.
        rows = conn.execute("""
            SELECT
                CASE WHEN name = 'assistant_response' THEN 'assistant_text'
                     WHEN name = 'assistant.thinking' THEN 'assistant_thinking'
                     ELSE COALESCE(json_extract(attributes, '$.tool_name'),
                                   substr(name, 6))
                END AS tool,
                COUNT(*)         AS calls,
                SUM(COALESCE(input_tokens, 0))  AS in_tok,
                SUM(COALESCE(output_tokens, 0)) AS out_tok,
                SUM(COALESCE(image_tokens, 0))  AS img_tok,
                SUM(COALESCE(cost_usd, 0))      AS cost
            FROM session_spans
            WHERE trace_id = ?
              AND (name LIKE 'tool.%' OR name IN ('assistant_response', 'assistant.thinking'))
              AND (input_tokens IS NOT NULL OR output_tokens IS NOT NULL)
            GROUP BY tool
            ORDER BY (SUM(COALESCE(input_tokens,0)) + SUM(COALESCE(output_tokens,0))) DESC
        """, (trace_id,)).fetchall()
        breakdown = _tool_target_breakdown(conn, trace_id)
        rollup = [_rollup_row(r, breakdown) for r in rows]
        sess_row = conn.execute(
            "SELECT input_tokens, output_tokens, cache_read_tokens, "
            "cache_creation_tokens, cost_usd FROM sessions WHERE trace_id = ?",
            (trace_id,),
        ).fetchone()
        bill_cost = _session_bill_cost(conn, trace_id)
        # Sub-agent spend that `sessions.cost_usd` (main transcript only)
        # omits, from two sources billed on a separate channel:
        #  * server-side sub-models (the Kimi advisor; web_search / web_fetch)
        #    whose cost lands on the span but never in turn_usage; and
        #  * Claude Task-tool *and* workflow subagents, whose isolated
        #    transcripts (`subagents/[workflows/<wf>/]agent-*.jsonl`)
        #    reconcile_claude_subagents totals onto each `subagent.stop` marker.
        # Folding both in lets the footer total reflect TRUE spend rather than
        # the main-model-only bill, and keeps the "$X of $Y" numerator a real
        # subset of its denominator.
        subagent = conn.execute(
            "SELECT COALESCE(SUM(cost_usd), 0) AS cost, "
            "COALESCE(SUM(COALESCE(input_tokens, 0) "
            "+ COALESCE(output_tokens, 0)), 0) AS tokens "
            "FROM session_spans WHERE trace_id = ? "
            "AND (json_extract(attributes, '$.server_side') = 1 "
            "     OR (name = 'subagent.stop' AND cost_usd IS NOT NULL))",
            (trace_id,),
        ).fetchone()
    finally:
        conn.close()

    return rollup, _rollup_totals(
        rollup, sess_row, bill_cost,
        subagent_cost=float(subagent['cost']),
        subagent_tokens=int(subagent['tokens']),
    )
