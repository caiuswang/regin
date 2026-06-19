"""Attribute Claude Code subagent (Task tool) API spend to the parent session.

Claude Code runs each Task-tool subagent in isolation and writes its full
conversation to a sibling file ``<projects>/<cwd>/<session_id>/subagents/
agent-<agent_id>.jsonl`` — NOT as ``isSidechain`` turns in the parent
transcript. Workflow-tool subagents land one level deeper, under
``subagents/workflows/<wf_id>/agent-<agent_id>.jsonl``. So the parent session's
``turn_usage`` (built from the main transcript) never sees a subagent's token
spend, and the session bill under-reports by the whole subagent cost (often
~half of a fan-out session, and effectively all of a workflow's spend).

This pass reads those sibling transcripts and stamps each subagent's total
cost / input / output onto its ``subagent.stop`` marker span (emitted by the
``SubagentStop`` hook, one per subagent). ``fetch_tool_token_rollup`` then sums
those markers into the ``subagent_*`` line — the same separate-line treatment
the Kimi server-side advisor gets — so ``total_spend`` reflects true spend
without inflating the main-model bill (``sessions.cost_usd``).

Totalling over *all* turns of each subagent (not just the text/thinking turns
that get their own replayed spans) keeps pure tool-use turns' cache-read and
output in the bill. Idempotent: value-stable UPDATEs keyed on the marker's
``agent_id``, safe to re-run on every ``SubagentStop`` and during backfill.
"""

from __future__ import annotations

import glob
import json
from pathlib import Path

from lib.activity_log import get_activity_logger

_log = get_activity_logger("trace_ingest")


def _subagents_dirs(trace_id: str) -> list[Path]:
    """Every ``<cwd>/<trace_id>/subagents`` directory for the session (usually
    one; a list because the cwd segment is globbed)."""
    from lib.providers.claude import ClaudeProvider
    base = ClaudeProvider().transcript_projects_dir()
    return [
        Path(p) for p in glob.glob(str(base / "*" / trace_id / "subagents"))
        if Path(p).is_dir()
    ]


def _agent_transcripts(trace_id: str) -> list[tuple[str, str]]:
    """`(agent_id, transcript_path)` for each subagent of the session, where
    `agent_id` is the ``agent-<id>.jsonl`` stem — the same id the SubagentStop
    hook stamped onto the marker spans.

    Recurses (``rglob``) so workflow-tool subagents — written one level deeper
    under ``subagents/workflows/<wf_id>/agent-*.jsonl`` — are picked up too, not
    just top-level Task-tool subagents. Both kinds emit a ``subagent.stop``
    marker keyed by this same stem, so the deeper transcripts get stamped just
    like the shallow ones."""
    out: list[tuple[str, str]] = []
    seen: set[str] = set()
    for d in _subagents_dirs(trace_id):
        for jsonl in sorted(d.rglob("agent-*.jsonl")):
            agent_id = jsonl.stem[len("agent-"):]
            if agent_id and agent_id not in seen:
                seen.add(agent_id)
                out.append((agent_id, str(jsonl)))
    return out


def discover_subagent_sessions() -> list[str]:
    """Trace ids of Claude sessions whose ``subagents`` dir holds any
    ``agent-*.jsonl`` (at any depth — including nested workflow subagents) —
    the backfill CLI's work list."""
    from lib.providers.claude import ClaudeProvider
    base = ClaudeProvider().transcript_projects_dir()
    out: list[str] = []
    seen: set[str] = set()
    for d in glob.glob(str(base / "*" / "*" / "subagents")):
        p = Path(d)
        if not p.is_dir() or next(p.rglob("agent-*.jsonl"), None) is None:
            continue
        sid = p.parent.name  # subagents -> <sid>
        if sid not in seen:
            seen.add(sid)
            out.append(sid)
    return out


def _agent_totals(usage) -> tuple[float, int, int]:
    """`(cost_usd, input_tokens, output_tokens)` summed over every turn of one
    subagent transcript, each turn priced at its own context tier (mirroring
    the main bill's per-turn pricing). Cache read/write fold into the dollar
    total; the token columns stay input+output for the rollup line."""
    from lib.tokens.pricing import TokenBreakdown, cost as price_cost
    total_cost = 0.0
    total_in = total_out = 0
    for turn in usage.turns:
        in_tok = int(turn.input_tokens or 0)
        out_tok = int(turn.output_tokens or 0)
        total_in += in_tok
        total_out += out_tok
        model = turn.model or usage.model
        if not model:
            continue
        cache_r = int(turn.cache_read_tokens or 0)
        cache_w = int(turn.cache_creation_tokens or 0)
        usd = price_cost(
            model,
            TokenBreakdown(
                input_tokens=in_tok,
                output_tokens=out_tok,
                cache_read_tokens=cache_r,
                cache_creation_tokens=cache_w,
            ),
            context_tokens=in_tok + cache_r + cache_w,
        )
        if usd:
            total_cost += usd
    return total_cost, total_in, total_out


def _stamp_stop_marker(conn, trace_id: str, agent_id: str,
                       cost: float, in_tok: int, out_tok: int) -> bool:
    """Write a subagent's totals onto its ``subagent.stop`` span. Returns True
    iff a marker was updated (False when the SubagentStop hook never recorded
    one — that subagent's spend is then absent from the bill until its marker
    lands)."""
    cur = conn.execute(
        "UPDATE session_spans "
        "   SET cost_usd = ?, input_tokens = ?, output_tokens = ? "
        " WHERE trace_id = ? AND name = 'subagent.stop' "
        "   AND json_extract(attributes, '$.agent_id') = ?",
        (cost, in_tok, out_tok, trace_id, agent_id),
    )
    return cur.rowcount > 0


def reconcile_claude_subagents(trace_id: str) -> dict:
    """Attribute a Claude session's subagent API spend onto its stop markers.

    Reads each ``subagents/agent-*.jsonl`` transcript, totals its per-turn
    cost / tokens, and stamps them on the matching ``subagent.stop`` span so
    ``fetch_tool_token_rollup`` folds them into the ``subagent_*`` line.
    Returns ``{subagents, stamped, cost_usd}``. A no-op (``subagents: 0``) when
    the session has no subagent dir. Idempotent.
    """
    if not isinstance(trace_id, str) or not trace_id:
        return {"subagents": 0, "stamped": 0, "cost_usd": 0.0}
    agents = _agent_transcripts(trace_id)
    if not agents:
        return {"subagents": 0, "stamped": 0, "cost_usd": 0.0}

    from lib.orm.engine import get_connection
    from lib.trace.transcript_usage import read_usage
    conn = get_connection()
    try:
        stamped = 0
        total_cost = 0.0
        for agent_id, path in agents:
            usage = read_usage(path, max_text_bytes=0)
            if usage is None:
                continue
            cost, in_tok, out_tok = _agent_totals(usage)
            if _stamp_stop_marker(conn, trace_id, agent_id, cost, in_tok, out_tok):
                stamped += 1
                total_cost += cost
        conn.commit()
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        raise
    finally:
        conn.close()

    result = {"subagents": len(agents), "stamped": stamped, "cost_usd": total_cost}
    _log.write("claude_subagents_reconciled", trace_id=trace_id, **result)
    return result
