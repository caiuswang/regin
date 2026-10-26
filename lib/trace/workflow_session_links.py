"""Session-row bookkeeping for ingested workflow runs: tokens, cost, title,
origin, and the link back to the parent session's Workflow tool call."""

from __future__ import annotations

import json
from pathlib import Path

from lib.trace.workflow_run_files import RunRef, _read_jsonl


# --------------------------------------------------------------------------
# persistence
# --------------------------------------------------------------------------

def _clear_run(run_id: str) -> None:
    """Delete a run's existing rows so re-ingest can't double-count or leave
    stale tree-map rows (the live flat tree is replaced by the full tree)."""
    from lib.orm.engine import get_connection

    conn = get_connection()
    try:
        conn.execute("BEGIN IMMEDIATE")
        for table in ("session_spans", "session_trace_map", "sessions",
                      "turn_usage"):
            conn.execute(f"DELETE FROM {table} WHERE trace_id = ?", (run_id,))
        conn.commit()
    finally:
        conn.close()


def _session_token_split(agents_dir: Path, agent_ids: list[str]) -> dict:
    """Sum the per-agent token split (input / output / cache) from the agent
    transcripts via ``read_usage`` — the same source the normal aggregator and
    the per-turn spans use. Used to populate the session row with an honest
    output-only ``output_tokens`` instead of the manifest's grand
    ``totalTokens`` (which folds in input + cache; cache usually dominates)."""
    from lib.trace.transcript_usage import read_usage

    tot = {"input": 0, "output": 0, "cache_read": 0, "cache_creation": 0}
    for aid in agent_ids:
        if not aid:
            continue
        usage = read_usage(str(agents_dir / f"agent-{aid}.jsonl"), max_text_bytes=0)
        if usage is None:
            continue
        tot["input"] += usage.input_tokens or 0
        tot["output"] += usage.output_tokens or 0
        tot["cache_read"] += usage.cache_read_tokens or 0
        tot["cache_creation"] += usage.cache_creation_tokens or 0
    return tot


def _set_session_tokens(run_id: str, split: dict) -> None:
    """Stamp the run's token split onto the session row, matching how the
    normal per-turn aggregator (which never runs for workflow runs) populates a
    session: separate input / output / cache columns, so ``output_tokens`` is
    output-only.

    This is what keeps the "Tokens by tool" rollup honest — its untagged
    remainder is ``output_tokens - attributed_output``, so stuffing the grand
    ``totalTokens`` into ``output_tokens`` (the old behaviour) surfaced the
    whole cache+input total as bogus "untagged" output. ``peak_context_tokens``
    stays NULL — a fan-out has no single context window, so context% is blank.
    """
    if not any(split.values()):
        return
    from lib.orm.engine import get_connection

    conn = get_connection()
    try:
        conn.execute(
            "UPDATE sessions SET input_tokens = ?, output_tokens = ?, "
            "cache_read_tokens = ?, cache_creation_tokens = ? WHERE trace_id = ?",
            (split["input"], split["output"], split["cache_read"],
             split["cache_creation"], run_id))
        conn.commit()
    finally:
        conn.close()


def _run_turn_usage_rows(agents_dir: Path, agent_ids: list[str]) -> list[dict]:
    """Per-turn usage rows for a run's agents, sourced from the agent
    transcripts via ``read_usage`` and priced per-turn at each turn's own
    context tier — the same source and pricing the main session bill uses.

    Workflow agents never reach the live turn-usage hook, so the run otherwise
    has no ``turn_usage`` rows at all: ``_session_bill_cost`` (which reads
    turn_usage) then returns a zeroed split and the whole bill renders $0. These
    rows let the workflow trace bill exactly like a normal session.

    ``turn_uuid`` is namespaced by agent id (``<agent_id>:<uuid|idx>``) because a
    run's agents can legitimately reuse a turn uuid (and tests share one
    transcript across agents) — without the prefix they'd collide on the
    ``(trace_id, turn_uuid)`` primary key and over-write each other.
    """
    from lib.trace.transcript_usage import read_usage

    rows: list[dict] = []
    for aid in agent_ids:
        if not aid:
            continue
        usage = read_usage(str(agents_dir / f"agent-{aid}.jsonl"), max_text_bytes=0)
        if usage is None:
            continue
        for idx, turn in enumerate(usage.turns):
            rows.append(_turn_usage_row(aid, idx, turn, usage.model))
    return rows


def _turn_usage_row(aid: str, idx: int, turn, default_model: str | None) -> dict:
    """One ``turn_usage`` row dict for a workflow agent turn, priced at its own
    context tier (cache reads/writes fold into ``cost_usd``)."""
    from lib.tokens.pricing import TokenBreakdown, cost as price_cost

    in_tok = int(turn.input_tokens or 0)
    out_tok = int(turn.output_tokens or 0)
    cache_r = int(turn.cache_read_tokens or 0)
    cache_w = int(turn.cache_creation_tokens or 0)
    cache_w_1h = int(turn.cache_creation_1h_tokens or 0)
    ctx = in_tok + cache_r + cache_w
    model = turn.model or default_model
    usd = price_cost(model, TokenBreakdown(
        input_tokens=in_tok, output_tokens=out_tok,
        cache_read_tokens=cache_r, cache_creation_tokens=cache_w,
        cache_creation_1h_tokens=cache_w_1h,
    ), context_tokens=ctx) if model else None
    return {
        "turn_uuid": f"{aid}:{turn.uuid or idx}", "turn_index": idx,
        "timestamp": turn.timestamp or "", "model": model,
        "input_tokens": in_tok, "output_tokens": out_tok,
        "cache_read_tokens": cache_r, "cache_creation_tokens": cache_w,
        "cache_creation_1h_tokens": cache_w_1h,
        "context_used_tokens": ctx, "cost_usd": usd,
    }


def _set_session_cost(run_id: str, agents_dir: Path, agent_ids: list[str]) -> None:
    """Persist the run's per-turn usage + total cost onto its trace.

    Inserts one ``turn_usage`` row per agent turn (priced at its context tier)
    so the rollup's per-bucket bill split computes, and stamps the summed cost on
    ``sessions.cost_usd`` so the footer total is real — mirroring how the normal
    per-turn aggregator bills an interactive session (a path workflow runs never
    hit). ``_clear_run`` wipes the run's turn_usage first, so the insert is the
    final, deterministic value. No-op when no turn carries usage.
    """
    rows = _run_turn_usage_rows(agents_dir, agent_ids)
    if not rows:
        return
    from lib.orm.engine import get_connection

    conn = get_connection()
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.executemany(
            "INSERT OR REPLACE INTO turn_usage (trace_id, turn_uuid, turn_index, "
            "timestamp, model, input_tokens, output_tokens, cache_read_tokens, "
            "cache_creation_tokens, cache_creation_1h_tokens, "
            "context_used_tokens, cost_usd) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [(run_id, r["turn_uuid"], r["turn_index"], r["timestamp"], r["model"],
              r["input_tokens"], r["output_tokens"], r["cache_read_tokens"],
              r["cache_creation_tokens"], r["cache_creation_1h_tokens"],
              r["context_used_tokens"], r["cost_usd"])
             for r in rows])
        conn.execute(
            "UPDATE sessions SET cost_usd = ?, cache_creation_1h_tokens = ? "
            "WHERE trace_id = ?",
            (sum(r["cost_usd"] or 0.0 for r in rows),
             sum(r["cache_creation_1h_tokens"] for r in rows), run_id))
        conn.commit()
    finally:
        conn.close()


def _set_session_title(run_id: str, title: str | None) -> None:
    """Title the run's session row with the *workflow name*, not its objective.

    The synthetic ``prompt`` span carries the run's objective (the script
    description / manifest summary), so the normal earliest-prompt rule
    (`lib.trace.trace_service.ingest._handle_prompt_title`) would label the
    session with that whole sentence and tag it ``first_prompt`` — which
    then drives the wrong source chip + tooltip + truncation in the UI. The
    workflow *name* (``meta.name``, e.g. "review-changes") is the short
    canonical identifier, so we stamp it directly with an honest source.
    ``_clear_run`` wipes the row each re-ingest, so this UPDATE is the
    final, deterministic value.
    """
    if not title:
        return
    from lib.orm.engine import get_connection

    conn = get_connection()
    try:
        conn.execute(
            "UPDATE sessions SET title = ?, title_source = 'workflow_name' "
            "WHERE trace_id = ?", (title, run_id))
        conn.commit()
    finally:
        conn.close()


def _set_session_origin(run_id: str) -> None:
    """Mark the run's session row as a captured workflow on the *origin* axis.

    ``sessions.origin`` is orthogonal to ``agent_type``: ``agent_type`` is
    the launching agent's vendor ('claude' for a workflow run, since the
    Workflow tool is a Claude Code feature), while ``origin`` records what
    KIND of row this is — 'session' for a real interactive agent session
    (the default) vs 'workflow' for a captured dynamic-workflow run. The
    run-root span carries ``agent_type='claude'``, so without this stamp the
    row would be indistinguishable from a normal Claude session; here we set
    its ``origin`` so the Sessions list can filter captured runs in/out.
    ``_clear_run`` wipes the row each re-ingest, so this UPDATE is the
    final, deterministic value.
    """
    from lib.orm.engine import get_connection

    conn = get_connection()
    try:
        conn.execute(
            "UPDATE sessions SET origin = 'workflow' WHERE trace_id = ?",
            (run_id,))
        conn.commit()
    finally:
        conn.close()


def _tag_workflow_sessions(run_id: str, parent_trace_id: str | None) -> None:
    """Auto-tag the run session AND its launching session with `workflow`.

    The builtin category axis is origin-only, so the *owner* session (a
    normal interactive session whose origin stays NULL) can't be reached
    through it — a stored `session_tags` row is the one mechanism that lets
    the tag facet select "everything workflow-related": the captured runs
    plus the sessions that launched them. The parent is tagged only once its
    session row exists (mirrors `_stamp_parent_link`'s skip-when-absent):
    `/api/session-tags` counts raw tag rows without joining sessions, so an
    orphan row would inflate the facet count. Re-ingests re-assert the tag
    (conflict-ignore, never downgrading a manual row) — which also means a
    hand-removed `workflow` tag returns on the next ingest pass; derived
    tags mirror the runs on disk.
    """
    from lib.orm.engine import get_connection
    from lib.trace.session_tags import upsert_auto_tags

    conn = get_connection()
    try:
        upsert_auto_tags(conn, run_id, ["workflow"])
        if parent_trace_id and conn.execute(
                "SELECT 1 FROM sessions WHERE trace_id = ?",
                (parent_trace_id,)).fetchone():
            upsert_auto_tags(conn, parent_trace_id, ["workflow"])
        conn.commit()
    finally:
        conn.close()


def _workflow_block(block: object) -> tuple[str, str] | None:
    """``(tool_use_id, script)`` if ``block`` is a ``Workflow`` tool_use."""
    if not isinstance(block, dict):
        return None
    if block.get("type") != "tool_use" or block.get("name") != "Workflow":
        return None
    tid, src = block.get("id"), (block.get("input") or {}).get("script")
    return (tid, src) if tid and isinstance(src, str) else None


def _iter_workflow_tool_uses(transcript: Path):
    """Yield ``(tool_use_id, script)`` for each ``Workflow`` call in a
    parent transcript's assistant content blocks."""
    for entry in _read_jsonl(transcript):
        if entry.get("type") != "assistant":
            continue
        content = entry.get("message", {}).get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            hit = _workflow_block(block)
            if hit:
                yield hit


def _find_parent_tool_use(transcript: Path, script: str) -> str | None:
    """tool_use_id of the parent ``Workflow`` call whose script launched a run.

    The runtime persists each run's script verbatim — byte-identical to the
    parent's ``Workflow`` tool_use ``input.script`` — so an exact match
    uniquely ties a run to the call that started it (distinct runs carry
    distinct scripts). Whitespace-stripped compare as a safety net.
    """
    target = script.strip()
    for tid, src in _iter_workflow_tool_uses(transcript):
        if src.strip() == target:
            return tid
    return None


def _iter_workflow_tool_results(transcript: Path):
    """Yield ``(tool_use_id, result_text)`` for each tool_result in a parent
    transcript. Tool results come back as content blocks on the following
    message; the ``Workflow`` result embeds the launched run's dir."""
    for entry in _read_jsonl(transcript):
        content = entry.get("message", {}).get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict) or block.get("type") != "tool_result":
                continue
            tid = block.get("tool_use_id")
            raw = block.get("content")
            if tid:
                yield tid, raw if isinstance(raw, str) else json.dumps(raw)


def _find_parent_tool_use_by_run_id(transcript: Path, run_id: str) -> str | None:
    """tool_use_id of the ``Workflow`` call that launched ``run_id``, recovered
    from the call's RESULT instead of its input script.

    The script lives in the tool_use ``input`` and is the first thing
    transcript compaction strips — which is why the script match misses on
    older sessions. The tool_result text (``Workflow launched … Transcript
    dir: …/workflows/<run_id>``) is far more durable, so matching the run dir
    path re-links runs whose launching script was summarised away. Returns the
    first match; a run relaunched/resumed under the same dir maps to several
    calls, and any of them is a valid anchor for the run's agents.
    """
    needle = f"workflows/{run_id}"
    for tid, text in _iter_workflow_tool_results(transcript):
        if needle in text:
            return tid
    return None


def _stamp_parent_link(run_ref: RunRef, workflow_name: str | None,
                       agent_ids: list[str] | None = None) -> None:
    """Cross-link a run with the ``tool.Workflow`` span that launched it.

    Stamps ``workflow_run_id`` + ``workflow_name`` onto the parent session's
    tool span (so the session view can render an inline "⚙ <name> · view run
    →" card) and ``parent_span_id`` onto the run root (so the run view's
    backlink deep-links to that exact tool call).

    Also re-parents the parent session's own ``subagent.start`` spans for this
    run's agents under that ``tool.Workflow`` span. Claude Code's SubagentStart
    hook records those in the *launching* session as orphans (``parent_id``
    NULL, carrying only ``agent_id``/``agent_type``) — so the session view
    floated them as siblings of the tool call. Matching ``agent_id`` against
    the run's manifest agent set makes the parent→child edge real in the DB,
    so the timeline nests + lazily folds them natively instead of the frontend
    grafting them by time window.
    Best-effort and idempotent: a missing script, an unmatched call, or a
    parent span that hasn't been ingested yet just skips silently — the
    ``parent_trace_id`` already on the root keeps the backlink working
    regardless.

    One-shot dependency: a terminal run is ingested once (the watcher gates
    on the now-stable manifest mtime), so the forward stamp only lands if
    the parent ``tool.Workflow`` span already exists then. In practice the
    PostToolUse hook records it at *launch* — long before completion — so it
    does. If it were ever absent, only the forward "view run →" link is lost;
    the backlink still resolves via ``parent_trace_id``.
    """
    parent_trace_id = run_ref.session_dir.name
    from lib.orm.engine import get_connection

    conn = get_connection()
    try:
        parent_span_id = _resolve_parent_tool_span(conn, run_ref, parent_trace_id)
        if parent_span_id is None:
            return
        conn.execute(
            "UPDATE session_spans SET attributes = json_set("
            "attributes, '$.workflow_run_id', ?, '$.workflow_name', ?) "
            "WHERE trace_id = ? AND span_id = ?",
            (run_ref.run_id, workflow_name, parent_trace_id, parent_span_id))
        conn.execute(
            "UPDATE session_spans "
            "SET attributes = json_set(attributes, '$.parent_span_id', ?) "
            "WHERE trace_id = ? AND name = 'session.start'",
            (parent_span_id, run_ref.run_id))
        # Re-parent this run's launching-session subagents under the tool span.
        _reparent_session_subagents(
            conn, parent_trace_id, parent_span_id, agent_ids)
        conn.commit()
        # All three writes above are in-place attribute/parent rewrites, which
        # leave every read-side identifier untouched.
        from lib.trace.trace_service import projection_cache
        projection_cache.invalidate((parent_trace_id, run_ref.run_id))
    finally:
        conn.close()


def _resolve_parent_tool_span(conn, run_ref, parent_trace_id: str) -> str | None:
    """span_id of the launching ``tool.Workflow`` call for this run, or None.

    Two resolution paths, most-durable first:
      1. The span already carries ``workflow_run_id`` — stamped live at
         PostToolUse from the tool result. Transcript-independent, so it
         survives any later compaction.
      2. Resolve a ``tool_use_id`` from the parent transcript (exact script
         match, then the run-dir reference in the tool result) and look the
         span up by that. The legacy path for spans captured before (1).
    """
    row = conn.execute(
        "SELECT span_id FROM session_spans WHERE trace_id = ? "
        "AND name = 'tool.Workflow' "
        "AND json_extract(attributes, '$.workflow_run_id') = ?",
        (parent_trace_id, run_ref.run_id)).fetchone()
    if row is not None:
        return row[0]
    if run_ref.script_path is None or not run_ref.script_path.exists():
        return None
    try:
        script = run_ref.script_path.read_text(encoding="utf-8")
    except OSError:
        return None
    transcript = run_ref.session_dir.parent / f"{parent_trace_id}.jsonl"
    if not transcript.exists():
        return None
    tool_use_id = (_find_parent_tool_use(transcript, script)
                   or _find_parent_tool_use_by_run_id(transcript, run_ref.run_id))
    if not tool_use_id:
        return None
    row = conn.execute(
        "SELECT span_id FROM session_spans WHERE trace_id = ? "
        "AND name = 'tool.Workflow' "
        "AND json_extract(attributes, '$.tool_use_id') = ?",
        (parent_trace_id, tool_use_id)).fetchone()
    return row[0] if row is not None else None


def _reparent_session_subagents(conn, parent_trace_id: str, parent_span_id: str,
                                agent_ids: list[str] | None) -> None:
    """Set ``parent_id`` of the launching session's ``subagent.start`` spans to
    the run's ``tool.Workflow`` span, matched by ``agent_id``.

    Scoped to the run's own agent set, so a session that launched several
    workflows nests each run's agents under its own tool call. Idempotent — a
    re-ingest just re-asserts the same parent edge.
    """
    ids = [a for a in (agent_ids or []) if a]
    if not ids:
        return
    placeholders = ",".join("?" * len(ids))
    conn.execute(
        "UPDATE session_spans SET parent_id = ? "
        "WHERE trace_id = ? AND name = 'subagent.start' "
        f"AND json_extract(attributes, '$.agent_id') IN ({placeholders})",
        (parent_span_id, parent_trace_id, *ids))
