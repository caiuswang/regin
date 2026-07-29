"""Reconcile Kimi Code subagent activity into nested subagent traces.

Claude Code runs a subagent in isolation: its tool calls never fire the
parent's hooks, so the only parent-session spans are `subagent.start` /
`subagent.stop` plus the subagent's replayed assistant turns. Kimi Code is
different — it fires `PreToolUse` / `PostToolUse` for a subagent's tool calls
under the PARENT `session_id`. Without help those land as flat session spans
in the main trace, mixed in with the main agent's own tools.

The hook payloads carry no subagent identifier, but Kimi writes each
subagent's own event stream to
``<sessions>/wd_*/<sid>/agents/agent-N/wire.jsonl`` (sibling of ``main``), and
a tool call's ``toolCallId`` there equals the parent hook's ``tool_use_id``.
This pass reads those sibling wires and:

  * stamps ``attributes.agent_id`` onto each subagent-owned tool span, so the
    serve-time ``_reparent_subagents`` (lib/trace/projection.py) nests it under
    the subagent;
  * enriches the hook-emitted ``subagent.start`` / ``subagent.stop`` markers
    with that ``agent_id``, the subagent's identity, and its final response
    (Kimi's ``SubagentStop`` carries the summary as ``response``); and
  * emits the subagent's own ``assistant_response`` / ``assistant.thinking``
    turns plus its ``prompt-sa-<agent_id>`` task-prompt anchor, also tagged
    ``agent_id`` (parity with Claude's ``emit_subagent_responses``); and
  * closes the launching ``tool.Agent`` span, which Kimi leaves ``PENDING``
    forever because it fires no ``PostToolUse`` for an ``Agent`` call.

``agent_id`` is the launching ``tool.Agent`` call's ``tool_use_id``, recovered
by matching that span's stored ``prompt`` as a substring of the subagent
wire's first prompt. Idempotent: deterministic span ids + value-stable UPDATEs,
safe to re-run on every ``SubagentStart``/``SubagentStop``, on the
``Stop``/``SessionEnd`` sweep, and during backfill.

A wire dir is a SLOT, not a subagent: Kimi reuses ``agent-N`` for a later
subagent and appends that run to the same file, so one wire can hold several
subagents back to back (each opening with its own ``turn.prompt``). Runs are
therefore split out before anything is bound — see :func:`_split_runs`.

``live=True`` marks a pass that can see a subagent mid-flight (the trace
view's rescan poll). The newest run of each wire is then left OPEN: no
synthetic ``subagent.stop``, no closing of its ``tool.Agent`` launch, and no
order-fallback claim on a stop marker — all three would render a running
agent as finished. A later ``SubagentStop``/``Stop``/``SessionEnd`` pass runs
with ``live=False`` and closes it for real.
"""

from __future__ import annotations

import glob
import json
from pathlib import Path

from lib.activity_log import get_activity_logger

_log = get_activity_logger("trace_ingest")

_RESULT_PREVIEW_MAX = 200
# Match enough of the launch prompt to be unambiguous without tripping over
# the trailing-whitespace / cap differences between the stored span prompt and
# the git-context-wrapped copy in the subagent wire.
_PROMPT_MATCH_CHARS = 200
# Same clamp the hook-side prompt anchors use
# (`turn_trace.span_posters._PROMPT_ANCHOR_TEXT_MAX_BYTES`), so a scoped
# subagent view opens with the same truncation regardless of which path wrote
# the anchor.
_PROMPT_TEXT_MAX_BYTES = 8 * 1024
_EMPTY_RESULT = {"subagents": 0, "tool_spans": 0, "turns": 0, "launches_closed": 0}
# Prompt-anchor uuids the Kimi parser mints for a `turn.prompt` record. A
# `turn.steer` anchors on `katt-*` instead, which is why a steer cannot be
# mistaken for the start of the next subagent in a reused slot.
_RUN_ANCHOR_PREFIX = "kprompt-"


def discover_subagent_sessions() -> list[str]:
    """Trace ids of Kimi sessions that have at least one subagent wire dir
    (``agents/agent-*/wire.jsonl``). Used by the backfill CLI to find every
    session whose subagent spans still need nesting."""
    from lib.providers.kimi import KimiProvider
    base = KimiProvider().transcript_projects_dir()
    out: list[str] = []
    seen: set[str] = set()
    for p in glob.glob(str(base / "*" / "*" / "agents" / "agent-*" / "wire.jsonl")):
        sid = Path(p).parents[2].name  # agent-N -> agents -> <session_id>
        if sid not in seen:
            seen.add(sid)
            out.append(sid)
    return out


def _agents_dir(trace_id: str) -> Path | None:
    """The ``agents/`` directory for a Kimi session, or None if not found."""
    from lib.providers.kimi import KimiProvider
    base = KimiProvider().transcript_projects_dir()
    matches = glob.glob(str(base / "*" / trace_id / "agents"))
    return Path(matches[0]) if matches else None


def _agent_index(name: str) -> int:
    """Numeric suffix of an ``agent-N`` dir name (for creation-order sort)."""
    try:
        return int(name.rsplit("-", 1)[1])
    except (IndexError, ValueError):
        return 0


def _subagent_wires(agents_dir: Path) -> list[tuple[str, str]]:
    """`(dir_name, wire_path)` for each non-main subagent, in creation order."""
    out: list[tuple[str, str]] = []
    for d in agents_dir.glob("agent-*"):
        wire = d / "wire.jsonl"
        if wire.is_file():
            out.append((d.name, str(wire)))
    out.sort(key=lambda t: _agent_index(t[0]))
    return out


def _load_launches(conn, trace_id: str) -> list[dict]:
    """The session's `tool.Agent` launch spans: one entry per distinct
    `tool_use_id`, carrying its stored prompt / subagent_type / description."""
    rows = conn.execute(
        "SELECT tool_use_id, attributes FROM session_spans "
        "WHERE trace_id = ? AND name = 'tool.Agent'",
        (trace_id,),
    ).fetchall()
    out: list[dict] = []
    seen: set[str] = set()
    for r in rows:
        attrs = json.loads(r["attributes"])
        tu = r["tool_use_id"] or attrs.get("tool_use_id")
        if not tu or tu in seen:
            continue
        seen.add(tu)
        out.append({
            "tool_use_id": tu,
            "prompt": (attrs.get("prompt") or "").strip(),
            "subagent_type": attrs.get("subagent_type"),
            "description": attrs.get("description"),
        })
    return out


def _match_launch(first_prompt: str, launches: list[dict], used: set[str]) -> dict | None:
    """The launch whose prompt is a prefix-substring of the subagent's first
    prompt and not already claimed."""
    for lc in launches:
        head = lc["prompt"][:_PROMPT_MATCH_CHARS]
        if head and lc["tool_use_id"] not in used and head in first_prompt:
            return lc
    return None


def _is_launch_prompt(text: str, launches: list[dict]) -> bool:
    """True when some `tool.Agent` launch prompt opens `text` — i.e. this wire
    prompt is a subagent LAUNCH rather than a continuation of the current run."""
    return any(
        lc["prompt"][:_PROMPT_MATCH_CHARS]
        and lc["prompt"][:_PROMPT_MATCH_CHARS] in text
        for lc in launches
    )


def _run_anchors(usage, launches: list[dict]) -> list[str]:
    """The prompt anchors that each open a subagent run, in turn order.

    The wire's first prompt always opens one; a later prompt only does when it
    carries a launch prompt, so a wire prompt with no `tool.Agent` behind it
    stays inside the run it interrupted rather than splitting off a phantom
    subagent that would then claim someone else's marker."""
    anchors: list[str] = []
    for turn in usage.turns:
        uid = turn.prompt_uuid
        if not (isinstance(uid, str) and uid.startswith(_RUN_ANCHOR_PREFIX)):
            continue
        if uid in anchors:
            continue
        if anchors and not _is_launch_prompt(usage.prompt_texts.get(uid, ""), launches):
            continue
        anchors.append(uid)
    return anchors


def _run_usage(usage, anchor: str, turns: list):
    """`usage` narrowed to one run — its own turns and its own launch prompt."""
    from dataclasses import replace
    return replace(
        usage,
        turns=turns,
        prompt_texts={anchor: usage.prompt_texts[anchor]}
        if anchor in usage.prompt_texts else {},
        prompt_timestamps={anchor: usage.prompt_timestamps[anchor]}
        if anchor in usage.prompt_timestamps else {},
    )


def _split_runs(usage, launches: list[dict]) -> list:
    """One usage per subagent RUN in a wire — usually the wire itself.

    Kimi reuses an `agent-N` slot for a later subagent, appending that run to
    the same wire. Reconciling the file as one subagent hands the second run's
    tool spans, turns and stop marker to the first run's `agent_id` and leaves
    the second with no identity at all (its `subagent.start` never gets an
    `agent_id`, so the roster cannot see it)."""
    anchors = _run_anchors(usage, launches)
    if len(anchors) < 2:
        return [usage]
    runs: list[tuple[str, list]] = [(anchors[0], [])]
    for turn in usage.turns:
        uid = turn.prompt_uuid
        if uid in anchors and uid != runs[-1][0]:
            runs.append((uid, []))
        runs[-1][1].append(turn)
    return [_run_usage(usage, uid, turns) for uid, turns in runs]


def _first_prompt(usage) -> str:
    """The subagent's first prompt text (the git-context-wrapped launch)."""
    for text in usage.prompt_texts.values():
        return text
    return ""


def _first_prompt_ts(usage) -> str | None:
    for ts in usage.prompt_timestamps.values():
        return ts
    return None


# The subagent's kind sits in the `profileName` of a `config.update` record at
# the head of its wire (line 2 in every observed session); read a short prefix
# rather than the whole file.
_PROFILE_SCAN_LINES = 40


def _wire_profile_name(wire_path: str) -> str | None:
    """The subagent's own declared kind (`config.update.profileName`, e.g.
    "explore") — the wire's ground truth for what this agent IS. Preferred over
    the launch span's `subagent_type` because a subagent whose launch could not
    be re-identified still gets a real name instead of "subagent"."""
    try:
        with open(wire_path) as fh:
            for idx, line in enumerate(fh):
                if idx >= _PROFILE_SCAN_LINES:
                    break
                if '"profileName"' not in line:
                    continue
                try:
                    name = json.loads(line).get("profileName")
                except ValueError:
                    continue
                if isinstance(name, str) and name.strip():
                    return name.strip()
    except OSError:
        return None
    return None


def _is_provider_tag(value) -> bool:
    """True when a stored `agent_type` is really a PROVIDER id ('kimi') rather
    than a subagent kind. The isinstance guard matters: the value comes back
    out of a span's JSON attributes and need not be a string."""
    if not isinstance(value, str) or not value.strip():
        return False
    from lib.providers.registry import is_provider_id
    return is_provider_id(value)


def _apply_kind(attrs: dict, kind: str | None) -> None:
    """Make `agent_type` name the SUBAGENT's kind. Kimi's SubagentStart hook
    writes its provider id there, which renders the card as an anonymous
    "kimi"/"agent" row; a value that is a real kind is left alone."""
    if kind and (not attrs.get("agent_type") or _is_provider_tag(attrs.get("agent_type"))):
        attrs["agent_type"] = kind


def _tool_ids(usage) -> list[str]:
    """Every tool-call id the subagent issued in its own wire."""
    ids: list[str] = []
    for turn in usage.turns:
        for tc in turn.tool_calls:
            tid = tc.get("id")
            if tid:
                ids.append(tid)
    return ids


def _turn_bounds(usage) -> tuple[str | None, str | None]:
    """`(first_ts, last_ts)` over the subagent's timestamped turns — the
    anchor times for an inserted start / stop marker."""
    times = [t.timestamp for t in usage.turns if t.timestamp]
    return (times[0], times[-1]) if times else (None, None)


def _result_preview(usage) -> str | None:
    """A flattened preview of the subagent's last assistant text."""
    last = ""
    for turn in usage.turns:
        if turn.text:
            last = turn.text
    if not last:
        return None
    flat = " ".join(str(last).split())
    if len(flat) > _RESULT_PREVIEW_MAX:
        flat = flat[:_RESULT_PREVIEW_MAX] + "…"
    return flat


def _stamp_tool_spans(conn, trace_id: str, agent_id: str, tool_ids: list[str]) -> int:
    """Set `attributes.agent_id` on every subagent-owned tool span (matched by
    `tool_use_id` column or `attributes.tool_use_id`). Returns rows touched."""
    if not tool_ids:
        return 0
    ph = ",".join("?" * len(tool_ids))
    cur = conn.execute(
        f"UPDATE session_spans "
        f"   SET attributes = json_set(attributes, '$.agent_id', ?), "
        f"       agent_id = ? "
        f" WHERE trace_id = ? "
        f"   AND (tool_use_id IN ({ph}) "
        f"        OR json_extract(attributes, '$.tool_use_id') IN ({ph}))",
        [agent_id, agent_id, trace_id, *tool_ids, *tool_ids],
    )
    return cur.rowcount


def _enrich_marker(conn, row, agent_id: str, info: dict) -> None:
    """Add agent_id / identity / result_preview to an existing
    `subagent.start` or `subagent.stop` span, preserving other attributes."""
    attrs = json.loads(row["attributes"])
    attrs["agent_id"] = agent_id
    _apply_kind(attrs, info.get("agent_name"))
    if info.get("agent_name"):
        attrs.setdefault("agent_name", info["agent_name"])
    if info.get("description"):
        attrs.setdefault("description", info["description"])
    if info.get("result_preview") and not attrs.get("result_preview"):
        attrs["result_preview"] = info["result_preview"]
    conn.execute(
        "UPDATE session_spans SET attributes = ?, agent_id = ? "
        "WHERE trace_id = ? AND span_id = ?",
        (json.dumps(attrs), agent_id, row["trace_id"], row["span_id"]),
    )


def _synthetic_id(kind: str, agent_id: str) -> str:
    return f"sa-{kind}-{agent_id}"


def _is_synthetic(span_id: str) -> bool:
    """True for a marker THIS module inserted (vs one a hook posted). Only
    these are ours to delete."""
    return span_id.startswith("sa-start-") or span_id.startswith("sa-stop-")


def _delete_marker(conn, trace_id: str, span_id: str) -> None:
    conn.execute(
        "DELETE FROM session_spans WHERE trace_id = ? AND span_id = ?",
        (trace_id, span_id),
    )


def _take_own_synthetic(pool: list, kind: str, agent_id: str):
    """Claim (removing it) the synthetic marker a previous run inserted for
    THIS agent, matched by its deterministic span id. Claiming by id rather
    than by content is what stops one agent from adopting another's marker."""
    want = _synthetic_id(kind, agent_id)
    for i, m in enumerate(pool):
        if m["span_id"] == want:
            return pool.pop(i)
    return None


def _insert_marker(conn, trace_id: str, name: str, agent_id: str,
                   ts: str, info: dict) -> None:
    """Emit a fresh `subagent.start` / `subagent.stop` for a session whose
    hooks never recorded one (older Kimi sessions). Deterministic span id keyed
    on agent_id keeps it idempotent; parent-less so the chronological graft
    nests it under the launching prompt."""
    from lib.trace.trace_service.ingest import _insert_span_row
    kind = "start" if name.endswith("start") else "stop"
    attrs: dict = {"agent_id": agent_id}
    _apply_kind(attrs, info.get("agent_name"))
    if info.get("agent_name"):
        attrs["agent_name"] = info["agent_name"]
    if info.get("description"):
        attrs["description"] = info["description"]
    if kind == "stop" and info.get("result_preview"):
        attrs["result_preview"] = info["result_preview"]
    span = {
        "trace_id": trace_id,
        "span_id": _synthetic_id(kind, agent_id),
        "parent_id": None,
        "name": name,
        "kind": "internal",
        "start_time": ts,
        "end_time": ts,
        "duration_ms": 0,
        "status_code": "OK",
        "status_message": None,
    }
    _insert_span_row(conn, span, attrs)


def _overlap(a: str, b: str, n: int = 80) -> bool:
    """True when the leading `n` chars of either string sit inside the other —
    a cheap content match tolerant of the truncation/wrapping differences
    between a stored marker preview and a wire-derived prompt/response."""
    return bool(a and b and (a[:n] in b or b[:n] in a))


def _match_marker(pool: list, key: str | None, attr: str, *, by_content_only=False):
    """Claim one HOOK marker from `pool` (removing it): the marker whose stored
    `attr` overlaps `key` (robust against out-of-order parallel subagents),
    else the first remaining (creation-order fallback for older markers that
    predate the stored preview). None when the pool holds no hook marker.

    Synthetic markers are skipped — each agent claims its own by id first, and
    letting the content/order fallback adopt a leftover synthetic is what used
    to bind a marker to the wrong agent. `by_content_only` drops the order
    fallback: a run that may still be live must not adopt a stray stop marker
    just because it is next in line."""
    hooks = [m for m in pool if not _is_synthetic(m["span_id"])]
    if not hooks:
        return None
    if key:
        for m in hooks:
            value = json.loads(m["attributes"]).get(attr) or ""
            if _overlap(value, key):
                pool.remove(m)
                return m
    if by_content_only:
        return None
    pool.remove(hooks[0])
    return hooks[0]


def _place_one_marker(conn, trace_id: str, kind: str, agent_id: str, info: dict,
                      pool: list, key: str | None, attr: str, ts: str | None,
                      closed: bool = True) -> None:
    """Bind this subagent to exactly ONE marker span of `kind`.

    A synthetic marker is inserted when the hook's marker hasn't landed yet
    (Kimi fires `SubagentStop` late, and sometimes never). Once the real one
    does arrive both describe the same event, so the synthetic is dropped
    rather than left behind — without that, every reconcile pass that ran
    before the hook permanently doubled the subagent's marker count.

    `closed=False` marks a run that may still be executing: only a marker whose
    content matches it is claimed, nothing is synthesized, and a synthetic left
    by an earlier pass is deleted — a synthetic stop over a live subagent reads
    as 'finished' in the roster."""
    name = f"subagent.{kind}"
    mine = _take_own_synthetic(pool, kind, agent_id)
    hook = _match_marker(pool, key, attr, by_content_only=not closed)
    if hook is not None:
        _enrich_marker(conn, hook, agent_id, info)
        if mine is not None:
            _delete_marker(conn, trace_id, mine["span_id"])
    elif mine is not None and closed:
        _enrich_marker(conn, mine, agent_id, info)
    elif mine is not None:
        _delete_marker(conn, trace_id, mine["span_id"])
    elif ts and closed:
        _insert_marker(conn, trace_id, name, agent_id, ts, info)


def _place_markers(conn, trace_id: str, agent_id: str, info: dict, usage,
                   starts: list, stops: list, launch_prompt: str | None,
                   closed: bool = True) -> None:
    """Claim + enrich this subagent's start/stop markers, or insert fresh ones
    when the session recorded none. `starts`/`stops` are mutated (claimed
    markers are popped) so each is bound to exactly one subagent. A start
    marker is placed either way — a run that is still executing has already
    started."""
    first_ts, last_ts = _turn_bounds(usage)
    _place_one_marker(conn, trace_id, "start", agent_id, info, starts,
                      launch_prompt, "prompt_preview", first_ts)
    _place_one_marker(conn, trace_id, "stop", agent_id, info, stops,
                      info.get("result_preview"), "result_preview", last_ts,
                      closed)


def _drop_stale_synthetics(conn, trace_id: str, unclaimed: list) -> int:
    """Delete the synthetic markers no subagent claimed this pass. They are
    keyed on `agent_id`, which changes once a subagent's launching `tool.Agent`
    call becomes re-identifiable (positional fallback → real `tool_use_id`), so
    a re-key would otherwise strand the old marker forever. Only ids this
    module minted are touched; an unclaimed HOOK marker is left alone."""
    dropped = 0
    for row in unclaimed:
        if _is_synthetic(row["span_id"]):
            _delete_marker(conn, trace_id, row["span_id"])
            dropped += 1
    return dropped


def _claim_markers(conn, trace_id: str, name: str) -> list:
    """Every subagent marker of one kind for the trace, oldest first — the
    claimable pool (mutated as subagents claim from it). Includes markers a
    prior run already enriched so re-running re-binds the SAME marker by
    content/order rather than inserting a duplicate (idempotency)."""
    return conn.execute(
        "SELECT trace_id, span_id, attributes FROM session_spans "
        " WHERE trace_id = ? AND name = ? "
        " ORDER BY start_time ASC, id ASC",
        (trace_id, name),
    ).fetchall()


def _emit_subagent_turns(conn, trace_id: str, agent_id: str, usage) -> int:
    """Insert the subagent's assistant_response / assistant.thinking turns,
    tagged agent_id. Deterministic span ids make this idempotent."""
    from hook_manager.handlers.subagent_lifecycle import (
        _subagent_turn_emittable, subagent_turn_spans,
    )
    from lib.trace.trace_service.ingest import _insert_span_row
    emitted = 0
    for idx, turn in enumerate(usage.turns):
        if not _subagent_turn_emittable(turn, None):
            continue
        for row in subagent_turn_spans(turn, idx, agent_id, usage.model):
            _insert_span_row(conn, {
                "trace_id": trace_id,
                "span_id": row["span_id"],
                "parent_id": None,
                "name": row["name"],
                "kind": "internal",
                "start_time": row["start_time"],
                "end_time": row["start_time"],
                "duration_ms": row["duration_ms"],
                "status_code": "OK",
                "status_message": None,
            }, row["attributes"])
            emitted += 1
    return emitted


def _span_duration_ms(start: str | None, end: str | None) -> int:
    from lib.trace.pending_spans import parse_naive_ts
    first, last = parse_naive_ts(start), parse_naive_ts(end)
    if first is None or last is None or last < first:
        return 0
    return int((last - first).total_seconds() * 1000)


def _close_launch_span(conn, trace_id: str, tool_use_id: str | None,
                       end_ts: str | None) -> int:
    """Resolve the `tool.Agent` call that spawned this subagent.

    Kimi never fires `PostToolUse` for an `Agent` call, so its PreToolUse
    placeholder stays `PENDING` forever: the card renders as a launch that
    never returned, and the serve-time merge eventually demotes it to
    "interrupted" even though the subagent finished. Close it at the
    subagent's last turn instead. Only `PENDING` rows are rewritten, so a
    launch that genuinely errored or was denied keeps its verdict."""
    if not (tool_use_id and end_ts):
        return 0
    rows = conn.execute(
        "SELECT span_id, start_time FROM session_spans "
        " WHERE trace_id = ? AND name = 'tool.Agent' AND status_code = 'PENDING' "
        "   AND (tool_use_id = ? OR json_extract(attributes, '$.tool_use_id') = ?)",
        (trace_id, tool_use_id, tool_use_id),
    ).fetchall()
    for row in rows:
        conn.execute(
            "UPDATE session_spans SET status_code = 'OK', end_time = ?, "
            "       duration_ms = ? WHERE trace_id = ? AND span_id = ?",
            (end_ts, _span_duration_ms(row["start_time"], end_ts),
             trace_id, row["span_id"]),
        )
    return len(rows)


def _emit_launch_prompt(conn, trace_id: str, agent_id: str, info: dict,
                        usage) -> None:
    """Insert the subagent's TASK PROMPT anchor (`prompt-sa-<agent_id>`) — the
    same span Claude's path emits — so a scoped `?agent=` view opens on the
    task statement instead of an empty pane. Prefers the launch span's clean
    prompt over the wire's git-context-wrapped copy."""
    from lib.trace.trace_service.ingest import _insert_span_row
    text = info.get("launch_prompt") or _first_prompt(usage)
    ts = _first_prompt_ts(usage) or _turn_bounds(usage)[0]
    if not (text and ts):
        return
    capped = text.encode("utf-8")[:_PROMPT_TEXT_MAX_BYTES].decode("utf-8", "ignore")
    attrs = {"text": capped, "chars": len(text), "agent_id": agent_id}
    if len(capped) < len(text):
        attrs["text_truncated"] = True
    _insert_span_row(conn, {
        "trace_id": trace_id,
        "span_id": f"prompt-sa-{agent_id}",
        "parent_id": None,
        "name": "prompt",
        "kind": "internal",
        "start_time": ts,
        "end_time": ts,
        "duration_ms": 0,
        "status_code": "OK",
        "status_message": None,
    }, attrs)


def _identity(trace_id: str, wire_path: str, usage, launches: list[dict],
              used: set[str], idx: int) -> dict:
    """Who this subagent is: its `agent_id` (the launching `tool.Agent` call's
    tool_use_id when the launch can be re-identified, else a positional
    fallback), its kind, and its launch/result text."""
    launch = _match_launch(_first_prompt(usage), launches, used)
    if launch:
        used.add(launch["tool_use_id"])
    kind = _wire_profile_name(wire_path) or (
        launch.get("subagent_type") if launch else None) or "subagent"
    return {
        "agent_id": launch["tool_use_id"] if launch else f"{trace_id}:agent-{idx}",
        "tool_use_id": launch["tool_use_id"] if launch else None,
        "agent_name": kind,
        "description": launch.get("description") if launch else None,
        "launch_prompt": launch.get("prompt") if launch else None,
        "result_preview": _result_preview(usage),
    }


def _reconcile_one(conn, trace_id: str, run: dict, launches: list[dict],
                   used: set[str], starts: list, stops: list, idx: int,
                   live: bool) -> dict:
    """Reconcile a single subagent run. Returns a small stats dict."""
    usage = run["usage"]
    closed = not (live and run["open"])
    info = _identity(trace_id, run["wire"], usage, launches, used, idx)
    agent_id = info["agent_id"]
    touched = _stamp_tool_spans(conn, trace_id, agent_id, _tool_ids(usage))
    _place_markers(conn, trace_id, agent_id, info, usage,
                   starts, stops, info["launch_prompt"], closed)
    _emit_launch_prompt(conn, trace_id, agent_id, info, usage)
    turns = _emit_subagent_turns(conn, trace_id, agent_id, usage)
    launches_closed = _close_launch_span(
        conn, trace_id, info["tool_use_id"], _turn_bounds(usage)[1]) if closed else 0
    return {"agent_id": agent_id, "tool_spans": touched, "turns": turns,
            "launches_closed": launches_closed}


def _collect_runs(wires: list[tuple[str, str]], launches: list[dict]) -> list[dict]:
    """Every subagent run across the session's wires, oldest first.

    Chronological order (not wire order) is what the marker pools are claimed
    in, so a run bound by the creation-order fallback lands on a marker from
    its own era. `open` flags the newest run of a slot — the only one that can
    still be executing, since a slot is reused only once its run has ended."""
    from lib.trace.kimi_transcript import read_usage_kimi
    runs: list[dict] = []
    for _name, wire_path in wires:
        usage = read_usage_kimi(wire_path)
        if usage is None:
            continue
        parts = _split_runs(usage, launches)
        for pos, part in enumerate(parts):
            runs.append({
                "wire": wire_path,
                "usage": part,
                "open": pos == len(parts) - 1,
                "ts": _turn_bounds(part)[0] or _first_prompt_ts(part) or "",
            })
    # A run whose time cannot be read sorts LAST (and keeps wire order among
    # its peers, the sort being stable): floating it to the front would let it
    # claim an older agent's marker through the creation-order fallback.
    runs.sort(key=lambda r: (not r["ts"], r["ts"]))
    return runs


def reconcile_kimi_subagents(trace_id: str, live: bool = False) -> dict:
    """Nest a Kimi session's flat subagent spans under their subagent trace.

    Reads the session's sibling ``agents/agent-*/wire.jsonl`` streams, splits
    each into its subagent runs, stamps ``agent_id`` onto the subagent-owned
    tool spans, enriches the ``subagent.start`` / ``subagent.stop`` markers,
    and replays the subagents' assistant turns. ``subagents`` counts RUNS, not
    wire dirs (a reused slot holds several). A no-op (``subagents: 0``) when
    the session has no subagent dirs. Idempotent.

    ``live=True`` (the trace view's rescan poll) leaves each slot's newest run
    open rather than declaring it finished — see the module docstring.
    """
    if not isinstance(trace_id, str) or not trace_id:
        return _EMPTY_RESULT.copy()
    agents_dir = _agents_dir(trace_id)
    if agents_dir is None:
        return _EMPTY_RESULT.copy()
    wires = _subagent_wires(agents_dir)
    if not wires:
        return _EMPTY_RESULT.copy()

    from lib.orm.engine import get_connection
    conn = get_connection()
    try:
        launches = _load_launches(conn, trace_id)
        starts = _claim_markers(conn, trace_id, "subagent.start")
        stops = _claim_markers(conn, trace_id, "subagent.stop")
        runs = _collect_runs(wires, launches)
        used: set[str] = set()
        tool_spans = turns = closed = 0
        for idx, run in enumerate(runs):
            stats = _reconcile_one(
                conn, trace_id, run, launches, used, starts, stops, idx, live,
            )
            tool_spans += stats["tool_spans"]
            turns += stats["turns"]
            closed += stats["launches_closed"]
        _drop_stale_synthetics(conn, trace_id, starts + stops)
        conn.commit()
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        raise
    finally:
        conn.close()

    result = {"subagents": len(runs), "tool_spans": tool_spans,
              "turns": turns, "launches_closed": closed}
    _log.write("kimi_subagents_reconciled", trace_id=trace_id, **result)
    return result
