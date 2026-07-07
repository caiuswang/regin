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


def _read_agent_meta(jsonl_path: str) -> dict:
    """Read the sibling ``agent-<id>.meta.json`` — carries ``toolUseId``,
    ``spawnDepth``, ``agentType`` (camelCase, not run through the transcript
    snake-caser). Empty dict when absent/unreadable."""
    meta_path = Path(jsonl_path).with_suffix('.meta.json')
    try:
        return json.loads(meta_path.read_text())
    except (OSError, ValueError):
        return {}


def _entry_tool_use_ids(entry: dict) -> set[str]:
    """The ``tool_use`` block ids carried by one transcript entry's message."""
    content = (entry.get('message') or {}).get('content')
    if not isinstance(content, list):
        return set()
    return {
        block['id']
        for block in content
        if isinstance(block, dict) and block.get('type') == 'tool_use'
        and isinstance(block.get('id'), str) and block.get('id')
    }


def _agent_tool_use_ids(jsonl_path: str) -> set[str]:
    """The ``tool_use`` block ids appearing in one agent's transcript — the
    ids by which THIS agent launched its own children. A nested agent's
    ``meta.json.toolUseId`` resolves against the spawning agent's set."""
    ids: set[str] = set()
    try:
        text = Path(jsonl_path).read_text()
    except OSError:
        return ids
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        try:
            entry = json.loads(stripped)
        except ValueError:
            continue
        ids |= _entry_tool_use_ids(entry)
    return ids


def _tool_use_owners(agents: list[tuple[str, str]]) -> tuple[dict[str, str], set[str]]:
    """`(owner_by_tool_use, ambiguous_tool_use_ids)`: the first agent whose
    transcript contains each ``tool_use`` block id, plus the set of ids that
    turned up in MORE than one agent's transcript (a duplicate id across
    sibling transcripts — resolving from it would be a guess, not ground
    truth)."""
    owner_by_tool_use: dict[str, str] = {}
    ambiguous: set[str] = set()
    for agent_id, path in agents:
        for tid in _agent_tool_use_ids(path):
            owner = owner_by_tool_use.get(tid)
            if owner is None:
                owner_by_tool_use[tid] = agent_id
            elif owner != agent_id:
                ambiguous.add(tid)
    return owner_by_tool_use, ambiguous


def _drop_cyclic_parents(parents: dict[str, str]) -> dict[str, str]:
    """Drop any child→parent edge whose parent chain walks back to the child
    itself (a mutual/2-cycle, a longer cycle, or a self-reference). Precision
    over recall: a cyclic parent map feeds `_build_span_tree` a subtree with
    no reachable root (both ends have a "valid" parent), silently dropping the
    whole segment — a wrong/ambiguous link is worse than none."""
    dropped: set[str] = set()
    for child in parents:
        visited = {child}
        cur = parents.get(child)
        while cur is not None:
            if cur in visited:
                dropped.add(child)
                break
            visited.add(cur)
            cur = parents.get(cur)
    return {c: p for c, p in parents.items() if c not in dropped}


def _resolve_parent_agents(agents: list[tuple[str, str]]) -> dict[str, str]:
    """child agent_id → spawning parent agent_id, for depth>=2 agents only.

    ``subagents/`` is a flat namespace even for nested spawns; the true
    agent→agent edge is a child's ``meta.json.toolUseId`` matched against the
    ``tool_use`` block ids of a sibling agent's transcript. Depth-1 agents (and
    any whose ``toolUseId`` resolves to no sibling) are omitted — they keep the
    flat 'under main' behavior. A ``toolUseId`` appearing in more than one
    sibling transcript resolves to no one (ambiguous owner), and any
    self-reference or cycle among the resolved edges is dropped — precision
    over recall throughout."""
    metas: dict[str, dict] = {agent_id: _read_agent_meta(path) for agent_id, path in agents}
    owner_by_tool_use, ambiguous = _tool_use_owners(agents)
    parents: dict[str, str] = {}
    for agent_id, _path in agents:
        meta = metas.get(agent_id) or {}
        depth = meta.get('spawnDepth')
        tool_use_id = meta.get('toolUseId')
        if not isinstance(depth, int) or depth < 2 or not isinstance(tool_use_id, str):
            continue
        if tool_use_id in ambiguous:
            continue
        parent = owner_by_tool_use.get(tool_use_id)
        if parent and parent != agent_id:
            parents[agent_id] = parent
    return _drop_cyclic_parents(parents)


def _stamp_parent_agent(conn, trace_id: str, agent_id: str,
                        parent_agent_id: str) -> int:
    """Stamp ``attributes.parent_agent_id`` on every span owned by ``agent_id``
    (matched by the ``agent_id`` attribute the hook stamps on each subagent
    span). Attributes-only — no column, no schema change (design Move 3);
    value-stable ``json_set`` so re-runs are idempotent. Returns rows updated."""
    cur = conn.execute(
        "UPDATE session_spans "
        "   SET attributes = json_set(attributes, '$.parent_agent_id', ?) "
        " WHERE trace_id = ? "
        "   AND json_extract(attributes, '$.agent_id') = ?",
        (parent_agent_id, trace_id, agent_id),
    )
    return cur.rowcount or 0


def _reparent_nested_agents(conn, trace_id: str,
                            agents: list[tuple[str, str]]) -> int:
    """Stamp ``parent_agent_id`` for every depth>=2 agent whose spawning agent
    resolves. Returns the count of agents stamped (>=1 span updated)."""
    stamped = 0
    for agent_id, parent_agent_id in _resolve_parent_agents(agents).items():
        if _stamp_parent_agent(conn, trace_id, agent_id, parent_agent_id) > 0:
            stamped += 1
    return stamped


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
        nested_parented = _reparent_nested_agents(conn, trace_id, agents)
        conn.commit()
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        raise
    finally:
        conn.close()

    result = {"subagents": len(agents), "stamped": stamped,
              "cost_usd": total_cost, "nested_parented": nested_parented}
    _log.write("claude_subagents_reconciled", trace_id=trace_id, **result)
    return result
