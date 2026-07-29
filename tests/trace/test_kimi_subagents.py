"""Tests for lib.trace.kimi_subagents — nesting Kimi's flat subagent spans.

Kimi Code fires PreToolUse/PostToolUse for a subagent's tool calls under the
PARENT session_id, so they land as flat session spans. The reconciler reads the
subagent's own `agents/agent-N/wire.jsonl`, stamps `agent_id` onto those tool
spans, enriches the start/stop markers, and replays the subagent's turns so the
serve-time graft nests everything under the subagent.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

import lib.providers.kimi as kimi_provider
from lib.trace.kimi_subagents import (
    discover_subagent_sessions, reconcile_kimi_subagents,
)
from lib.trace.projection import _fetch_spans, _graft_orphans

_SID = "session_test_abc"


def _loop(event: dict, time: int = 0) -> dict:
    return {"type": "context.append_loop_event", "event": event, "time": time}


def _subagent_wire(path: Path, prefix: str, first_prompt: str,
                   tool_ids: list[str], final_text: str,
                   profile: str | None = "explore") -> None:
    """A minimal subagent wire: one prompt, a tool-call step, and a text step.
    Step uuids are prefixed so two subagents never collide (real Kimi uuids are
    globally unique; the replayed-turn span ids are derived from them).
    `profile` mirrors the real wire's `config.update.profileName` — the
    subagent's declared kind."""
    s1, s2 = f"{prefix}-s1", f"{prefix}-s2"
    records: list[dict] = [
        {"type": "metadata", "protocol_version": "1.4", "created_at": 1},
        {"type": "config.update", "cwd": "/proj", "modelAlias": "kimi-code/k3",
         "time": 1},
    ]
    if profile:
        records.append({"type": "config.update", "profileName": profile,
                        "systemPrompt": "You are now running as a subagent.",
                        "time": 1})
    records += [
        {"type": "turn.prompt",
         "input": [{"type": "text", "text": first_prompt}], "time": 1_000},
    ]
    records += [
        _loop({"type": "step.begin", "uuid": s1}),
        _loop({"type": "content.part", "stepUuid": s1,
               "part": {"type": "think", "think": "working"}}),
    ]
    for tid in tool_ids:
        records.append(_loop({"type": "tool.call", "stepUuid": s1,
                              "toolCallId": tid, "name": "Read",
                              "args": {"file_path": "/x"}}))
        records.append(_loop({"type": "tool.result", "toolCallId": tid,
                              "result": {"output": "data"}}))
    records.append(_loop({"type": "step.end", "uuid": s1,
                          "usage": {"inputOther": 10, "output": 5}}, time=2_000))
    records += [
        _loop({"type": "step.begin", "uuid": s2}),
        _loop({"type": "content.part", "stepUuid": s2,
               "part": {"type": "text", "text": final_text}}),
        _loop({"type": "step.end", "uuid": s2,
               "usage": {"inputOther": 5, "output": 5}}, time=3_000),
        {"type": "usage.record", "model": "kimi-code/kimi-for-coding"},
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(r) for r in records))


def _run_records(prefix: str, prompt: str, tool_ids: list[str],
                 final_text: str, base_ms: int) -> list[dict]:
    """The records of ONE subagent run: its `turn.prompt`, a tool step and a
    text step. Kimi reuses an `agent-N` slot, appending a later subagent's run
    to the same wire, so a wire is a concatenation of these."""
    s1, s2 = f"{prefix}-s1", f"{prefix}-s2"
    records: list[dict] = [
        {"type": "turn.prompt", "input": [{"type": "text", "text": prompt}],
         "time": base_ms},
        _loop({"type": "step.begin", "uuid": s1}),
        _loop({"type": "content.part", "stepUuid": s1,
               "part": {"type": "think", "think": "working"}}),
    ]
    for tid in tool_ids:
        records.append(_loop({"type": "tool.call", "stepUuid": s1,
                              "toolCallId": tid, "name": "Read",
                              "args": {"file_path": "/x"}}))
        records.append(_loop({"type": "tool.result", "toolCallId": tid,
                              "result": {"output": "data"}}))
    records += [
        _loop({"type": "step.end", "uuid": s1,
               "usage": {"inputOther": 10, "output": 5}}, time=base_ms + 1_000),
        _loop({"type": "step.begin", "uuid": s2}),
        _loop({"type": "content.part", "stepUuid": s2,
               "part": {"type": "text", "text": final_text}}),
        _loop({"type": "step.end", "uuid": s2,
               "usage": {"inputOther": 5, "output": 5}}, time=base_ms + 2_000),
        {"type": "usage.record", "model": "kimi-code/kimi-for-coding"},
    ]
    return records


def _append_run(path: Path, prefix: str, prompt: str, tool_ids: list[str],
                final_text: str, base_ms: int = 100_000) -> None:
    """Append a SECOND subagent's run to an existing wire — a reused slot."""
    with path.open("a") as fh:
        fh.write("\n" + "\n".join(
            json.dumps(r) for r in
            _run_records(prefix, prompt, tool_ids, final_text, base_ms)))


@pytest.fixture
def kimi_home(tmp_path, monkeypatch):
    """Point the Kimi provider's sessions dir at a temp tree with two
    subagents (alpha, beta) under one session, and return the session root."""
    monkeypatch.setattr(kimi_provider, "_KIMI_HOME", tmp_path)
    agents = tmp_path / "sessions" / "wd_proj_hash" / _SID / "agents"
    (agents / "main").mkdir(parents=True)
    (agents / "main" / "wire.jsonl").write_text("")
    _subagent_wire(agents / "agent-0" / "wire.jsonl", "a",
                   "<git-context>x</git-context>\nExplore alpha subsystem now",
                   ["call-a1", "call-a2"], "alpha done")
    _subagent_wire(agents / "agent-1" / "wire.jsonl", "b",
                   "<git-context>x</git-context>\nExplore beta subsystem now",
                   ["call-b1"], "beta done")
    return agents


def _seed(db_path, *, with_prompt_preview: bool, with_stops: bool = True) -> None:
    """Seed the flat parent trace exactly as Kimi's hooks leave it: PENDING
    `tool.Agent` launches (Kimi fires no PostToolUse for an Agent call), the
    subagents' tool calls leaked flat under the parent prompt, and markers whose
    `agent_type` is the PROVIDER id. `with_stops=False` is the reference
    session's shape — SubagentStop never fired."""
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute(
            "INSERT INTO sessions (trace_id, started_at, last_seen) VALUES (?, ?, ?)",
            (_SID, "2026-06-01T00:00:00", "2026-06-01T00:00:00"),
        )

        def span(span_id, name, ts, attrs, tool_use_id=None, parent_id=None,
                 status="OK"):
            conn.execute(
                "INSERT INTO session_spans (trace_id, span_id, name, start_time, "
                "attributes, tool_use_id, parent_id, status_code) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (_SID, span_id, name, ts, json.dumps(attrs), tool_use_id,
                 parent_id, status),
            )

        span("conv", "conversation", "2026-06-01T00:00:00", {})
        span("prompt-1", "prompt", "2026-06-01T00:00:01", {}, parent_id="conv")
        # Launch spans carry the prompt that re-identifies each subagent. Kimi
        # only ever emits the PreToolUse placeholder, so they stay PENDING and
        # the tool_use_id lives in attributes, not the column.
        span("pending-launch-0", "tool.Agent", "2026-06-01T00:00:02",
             {"prompt": "Explore alpha subsystem now", "subagent_type": "explore",
              "description": "alpha", "tool_use_id": "launch-0"},
             parent_id="prompt-1", status="PENDING")
        span("pending-launch-1", "tool.Agent", "2026-06-01T00:00:03",
             {"prompt": "Explore beta subsystem now", "subagent_type": "explore",
              "description": "beta", "tool_use_id": "launch-1"},
             parent_id="prompt-1", status="PENDING")
        # Leaked subagent tool spans (flat under the prompt, no agent_id).
        for tid in ("call-a1", "call-a2", "call-b1"):
            span(f"tsp-{tid}", "tool.Read", "2026-06-01T00:00:04",
                 {"tool_name": "Read"}, tool_use_id=tid, parent_id="prompt-1")
        # Hook-emitted markers without agent_id. prompt_preview lets the
        # reconciler bind by content; omit it to exercise the order fallback.
        a0 = {"agent_type": "kimi", "agent_name": "explore"}
        a1 = {"agent_type": "kimi", "agent_name": "explore"}
        if with_prompt_preview:
            a0["prompt_preview"] = "Explore alpha subsystem now"
            a1["prompt_preview"] = "Explore beta subsystem now"
        span("S0", "subagent.start", "2026-06-01T00:00:05", a0, parent_id="prompt-1")
        span("S1", "subagent.start", "2026-06-01T00:00:06", a1, parent_id="prompt-1")
        if with_stops:
            span("T0", "subagent.stop", "2026-06-01T00:00:30", {"agent_type": "kimi"}, parent_id="prompt-1")
            span("T1", "subagent.stop", "2026-06-01T00:00:31", {"agent_type": "kimi"}, parent_id="prompt-1")
        conn.commit()
    finally:
        conn.close()


def _agent_id_of(db_path, tool_use_id) -> str | None:
    conn = sqlite3.connect(str(db_path))
    try:
        row = conn.execute(
            "SELECT json_extract(attributes, '$.agent_id') FROM session_spans "
            "WHERE trace_id = ? AND tool_use_id = ?",
            (_SID, tool_use_id),
        ).fetchone()
        return row[0] if row else None
    finally:
        conn.close()


@pytest.mark.parametrize("with_prompt_preview", [True, False])
def test_tool_spans_get_subagent_agent_id(tmp_db, kimi_home, with_prompt_preview):
    _seed(tmp_db, with_prompt_preview=with_prompt_preview)
    result = reconcile_kimi_subagents(_SID)
    assert result == {"subagents": 2, "tool_spans": 3, "turns": 4,
                      "launches_closed": 2}
    # Each leaked tool span now carries its launching agent's id.
    assert _agent_id_of(tmp_db, "call-a1") == "launch-0"
    assert _agent_id_of(tmp_db, "call-a2") == "launch-0"
    assert _agent_id_of(tmp_db, "call-b1") == "launch-1"


def test_markers_enriched_and_turns_replayed(tmp_db, kimi_home):
    _seed(tmp_db, with_prompt_preview=True)
    reconcile_kimi_subagents(_SID)
    conn = sqlite3.connect(str(tmp_db))
    try:
        starts = dict(conn.execute(
            "SELECT span_id, json_extract(attributes, '$.agent_id') "
            "FROM session_spans WHERE trace_id = ? AND name = 'subagent.start'",
            (_SID,)).fetchall())
        # Content match binds S0->alpha, S1->beta (not order-swapped).
        assert starts == {"S0": "launch-0", "S1": "launch-1"}
        stop_prev = conn.execute(
            "SELECT json_extract(attributes, '$.result_preview') FROM session_spans "
            "WHERE trace_id = ? AND name = 'subagent.stop' "
            "AND json_extract(attributes, '$.agent_id') = 'launch-0'",
            (_SID,)).fetchone()[0]
        assert stop_prev == "alpha done"
        # Replayed assistant turns are tagged with the agent id.
        turn_agents = [r[0] for r in conn.execute(
            "SELECT json_extract(attributes, '$.agent_id') FROM session_spans "
            "WHERE trace_id = ? AND name IN ('assistant_response', 'assistant.thinking')",
            (_SID,)).fetchall()]
        assert set(turn_agents) == {"launch-0", "launch-1"}
        assert len(turn_agents) == 4
    finally:
        conn.close()


def _read_tool_ids_under(grafted, parent_id) -> set:
    """tool_use_ids of the tool.Read spans directly parented under `parent_id`."""
    return {
        s["tool_use_id"] for s in grafted
        if s.get("parent_id") == parent_id and s["name"] == "tool.Read"
    }


def test_serve_time_graft_nests_under_subagent(tmp_db, kimi_home):
    _seed(tmp_db, with_prompt_preview=True)
    reconcile_kimi_subagents(_SID)
    conn = sqlite3.connect(str(tmp_db))
    conn.row_factory = sqlite3.Row
    try:
        grafted = _graft_orphans(_fetch_spans(conn, _SID))
    finally:
        conn.close()
    starts = {s["span_id"]: s for s in grafted if s["name"] == "subagent.start"}
    # S0 owns alpha's two Read tools; S1 owns beta's one.
    assert _read_tool_ids_under(grafted, "S0") == {"call-a1", "call-a2"}
    assert _read_tool_ids_under(grafted, "S1") == {"call-b1"}
    # Each subagent.start stays anchored under the launching prompt.
    assert starts["S0"]["parent_id"] == "prompt-1"


def test_idempotent_no_duplicate_markers(tmp_db, kimi_home):
    _seed(tmp_db, with_prompt_preview=True)
    reconcile_kimi_subagents(_SID)
    reconcile_kimi_subagents(_SID)
    conn = sqlite3.connect(str(tmp_db))
    try:
        counts = dict(conn.execute(
            "SELECT name, COUNT(*) FROM session_spans WHERE trace_id = ? "
            "AND name LIKE 'subagent.%' GROUP BY name", (_SID,)).fetchall())
    finally:
        conn.close()
    assert counts == {"subagent.start": 2, "subagent.stop": 2}


def test_inserts_markers_when_session_recorded_none(tmp_db, kimi_home):
    """A session whose hooks never recorded markers gets fresh ones inserted so
    the stamped tool spans still have an anchor to nest under."""
    conn = sqlite3.connect(str(tmp_db))
    try:
        conn.execute(
            "INSERT INTO sessions (trace_id, started_at, last_seen) VALUES (?, ?, ?)",
            (_SID, "2026-06-01T00:00:00", "2026-06-01T00:00:00"))
        conn.execute(
            "INSERT INTO session_spans (trace_id, span_id, name, start_time, "
            "attributes, tool_use_id) VALUES (?, 'tsp', 'tool.Read', "
            "'2026-06-01T00:00:04', '{}', 'call-a1')", (_SID,))
        conn.commit()
    finally:
        conn.close()
    result = reconcile_kimi_subagents(_SID)
    assert result["subagents"] == 2
    conn = sqlite3.connect(str(tmp_db))
    try:
        starts = conn.execute(
            "SELECT COUNT(*) FROM session_spans WHERE trace_id = ? "
            "AND name = 'subagent.start'", (_SID,)).fetchone()[0]
    finally:
        conn.close()
    assert starts == 2  # inserted from the wire dirs


def test_discover_finds_session_with_subagents(tmp_db, kimi_home):
    assert discover_subagent_sessions() == [_SID]


def test_provider_lists_subagent_wires_beside_main(kimi_home):
    """The live rescan stats these for freshness. Kimi puts a subagent in a
    SIBLING of `main/`, so the Claude default (`<session>/subagents/*.jsonl`)
    finds nothing and a streaming subagent looks idle."""
    paths = kimi_provider.KimiProvider().subagent_transcript_paths(
        str(kimi_home / "main" / "wire.jsonl"))
    assert paths == [str(kimi_home / "agent-0" / "wire.jsonl"),
                     str(kimi_home / "agent-1" / "wire.jsonl")]


def test_no_subagents_is_noop(tmp_db, monkeypatch, tmp_path):
    monkeypatch.setattr(kimi_provider, "_KIMI_HOME", tmp_path)
    assert reconcile_kimi_subagents("session_missing") == {
        "subagents": 0, "tool_spans": 0, "turns": 0, "launches_closed": 0}


# ── agent_id / identity / launch closing (provider parity) ─────────────────

def _rows(db_path, sql, params=()):
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in conn.execute(sql, (_SID, *params))]
    finally:
        conn.close()


def test_agent_id_column_populated_for_scope_pane(tmp_db, kimi_home):
    """The `?agent=` scope pane and the roster group on the agent_id COLUMN
    (pending_spans.AGENT_ID_SQL), not just the JSON attribute."""
    _seed(tmp_db, with_prompt_preview=True)
    reconcile_kimi_subagents(_SID)
    by_agent: dict = {}
    for r in _rows(tmp_db, "SELECT agent_id, name FROM session_spans "
                           "WHERE trace_id = ? AND agent_id IS NOT NULL"):
        by_agent.setdefault(r["agent_id"], []).append(r["name"])
    assert set(by_agent) == {"launch-0", "launch-1"}
    # Markers, leaked tools, replayed turns and the task prompt are all scoped.
    assert set(by_agent["launch-0"]) == {
        "subagent.start", "subagent.stop", "tool.Read",
        "assistant_response", "assistant.thinking", "prompt"}


def test_marker_kind_is_the_subagent_not_the_provider(tmp_db, kimi_home):
    """`agent_type` on the marker must be the wire's `profileName`, not the
    provider id the SubagentStart hook writes there — that is what made every
    Kimi subagent card render as an anonymous row."""
    _seed(tmp_db, with_prompt_preview=True)
    reconcile_kimi_subagents(_SID)
    kinds = {r["k"] for r in _rows(
        tmp_db, "SELECT json_extract(attributes, '$.agent_type') AS k "
                "FROM session_spans WHERE trace_id = ? "
                "AND name IN ('subagent.start', 'subagent.stop')")}
    assert kinds == {"explore"}


def test_launch_span_is_closed(tmp_db, kimi_home):
    """Kimi fires no PostToolUse for an `Agent` call, so its launch placeholder
    would render PENDING forever (and then be demoted to 'interrupted')."""
    _seed(tmp_db, with_prompt_preview=True)
    before = _rows(tmp_db, "SELECT status_code, end_time FROM session_spans "
                           "WHERE trace_id = ? AND name = 'tool.Agent'")
    assert {r["status_code"] for r in before} == {"PENDING"}
    assert all(r["end_time"] is None for r in before)
    reconcile_kimi_subagents(_SID)
    after = _rows(tmp_db, "SELECT status_code, end_time, duration_ms "
                          "FROM session_spans WHERE trace_id = ? "
                          "AND name = 'tool.Agent'")
    assert {r["status_code"] for r in after} == {"OK"}
    assert all(r["end_time"] and r["duration_ms"] >= 0 for r in after)


def test_task_prompt_span_emitted_per_agent(tmp_db, kimi_home):
    """The scoped view opens on a TASK PROMPT card, same as Claude's
    `prompt-sa-<agent_id>` anchor."""
    _seed(tmp_db, with_prompt_preview=True)
    reconcile_kimi_subagents(_SID)
    prompts = {r["span_id"]: r["text"] for r in _rows(
        tmp_db, "SELECT span_id, json_extract(attributes, '$.text') AS text "
                "FROM session_spans WHERE trace_id = ? AND name = 'prompt' "
                "AND span_id LIKE 'prompt-sa-%'")}
    assert prompts == {
        "prompt-sa-launch-0": "Explore alpha subsystem now",
        "prompt-sa-launch-1": "Explore beta subsystem now",
    }


def test_recovers_when_subagent_stop_never_fired(tmp_db, kimi_home):
    """The reference-session failure: two `subagent.start` markers, no stop,
    every span agent_id-NULL. Reconciliation must still nest and identify —
    this is why it is also called from SubagentStart / the Stop sweep."""
    _seed(tmp_db, with_prompt_preview=True, with_stops=False)
    result = reconcile_kimi_subagents(_SID)
    assert result["subagents"] == 2
    assert _agent_id_of(tmp_db, "call-a1") == "launch-0"
    stops = _rows(tmp_db, "SELECT span_id, agent_id FROM session_spans "
                          "WHERE trace_id = ? AND name = 'subagent.stop'")
    # Inserted from the wire so the agent isn't stuck 'running' forever.
    assert {r["agent_id"] for r in stops} == {"launch-0", "launch-1"}
    assert {r["span_id"] for r in stops} == {
        "sa-stop-launch-0", "sa-stop-launch-1"}


def test_second_run_changes_nothing(tmp_db, kimi_home):
    """Idempotency across two runs — the property every new trigger point
    (SubagentStart, the Stop/SessionEnd sweep, backfill) relies on."""
    _seed(tmp_db, with_prompt_preview=True, with_stops=False)
    first = reconcile_kimi_subagents(_SID)
    sql = ("SELECT span_id, name, status_code, end_time, duration_ms, "
           "agent_id, attributes FROM session_spans WHERE trace_id = ? "
           "ORDER BY span_id")
    snapshot = _rows(tmp_db, sql)
    second = reconcile_kimi_subagents(_SID)
    assert _rows(tmp_db, sql) == snapshot
    # `launches_closed` counts rows actually transitioned out of PENDING, so a
    # second pass legitimately reports 0 — everything else is value-stable.
    assert first["launches_closed"] == 2 and second["launches_closed"] == 0
    assert {k: v for k, v in second.items() if k != "launches_closed"} == \
           {k: v for k, v in first.items() if k != "launches_closed"}


def test_unmatched_launch_still_named_from_the_wire(tmp_db, kimi_home):
    """With no `tool.Agent` launch to match, the subagent falls back to a
    positional agent_id but still takes its kind from its own wire."""
    conn = sqlite3.connect(str(tmp_db))
    try:
        conn.execute(
            "INSERT INTO sessions (trace_id, started_at, last_seen) "
            "VALUES (?, ?, ?)", (_SID, "2026-06-01T00:00:00", "2026-06-01T00:00:00"))
        conn.commit()
    finally:
        conn.close()
    reconcile_kimi_subagents(_SID)
    kinds = {r["k"] for r in _rows(
        tmp_db, "SELECT json_extract(attributes, '$.agent_type') AS k "
                "FROM session_spans WHERE trace_id = ? AND name = 'subagent.start'")}
    assert kinds == {"explore"}


def _add_stop_markers(db_path, *previews: str) -> None:
    """Append the hook-emitted `subagent.stop` markers that Kimi delivers late
    — often several reconcile passes after the subagent's wire went quiet."""
    conn = sqlite3.connect(str(db_path))
    try:
        for i, preview in enumerate(previews):
            conn.execute(
                "INSERT INTO session_spans (trace_id, span_id, name, start_time, "
                "attributes, status_code) VALUES (?, ?, 'subagent.stop', ?, ?, 'OK')",
                (_SID, f"hookstop-{i}", f"2026-06-01T00:01:0{i}",
                 json.dumps({"agent_type": "kimi", "result_preview": preview})),
            )
        conn.commit()
    finally:
        conn.close()


def test_late_hook_stop_supersedes_the_synthetic_marker(tmp_db, kimi_home):
    """Kimi fires `SubagentStop` long after the wire goes quiet, so an earlier
    sweep has already inserted a synthetic stop. When the real marker lands,
    the subagent must end up with ONE stop, not one per emitter."""
    _seed(tmp_db, with_prompt_preview=True, with_stops=False)
    reconcile_kimi_subagents(_SID)
    assert {r["span_id"] for r in _rows(
        tmp_db, "SELECT span_id FROM session_spans WHERE trace_id = ? "
                "AND name = 'subagent.stop'")} == {
        "sa-stop-launch-0", "sa-stop-launch-1"}

    _add_stop_markers(tmp_db, "alpha done", "beta done")
    reconcile_kimi_subagents(_SID)

    stops = _rows(tmp_db, "SELECT span_id, agent_id FROM session_spans "
                          "WHERE trace_id = ? AND name = 'subagent.stop'")
    assert {r["span_id"] for r in stops} == {"hookstop-0", "hookstop-1"}
    # Each real marker is bound to the agent whose result it reports.
    assert {r["span_id"]: r["agent_id"] for r in stops} == {
        "hookstop-0": "launch-0", "hookstop-1": "launch-1"}


def test_hook_markers_are_never_claimed_by_two_agents(tmp_db, kimi_home):
    """Content matching must not fall through to another agent's synthetic
    marker — that used to hand agent A's stop row agent B's id."""
    _seed(tmp_db, with_prompt_preview=True, with_stops=False)
    reconcile_kimi_subagents(_SID)
    _add_stop_markers(tmp_db, "alpha done")  # only ONE subagent's stop landed
    reconcile_kimi_subagents(_SID)

    stops = _rows(tmp_db, "SELECT span_id, agent_id FROM session_spans "
                          "WHERE trace_id = ? AND name = 'subagent.stop'")
    assert len(stops) == 2
    assert {r["agent_id"] for r in stops} == {"launch-0", "launch-1"}
    # The agent still waiting on its hook keeps its synthetic stand-in.
    assert {r["span_id"] for r in stops} == {"hookstop-0", "sa-stop-launch-1"}


# ── reused agent slots + live (mid-run) passes ─────────────────────────────

def _seed_reused_slot(db_path, agents: Path) -> None:
    """A session whose SECOND subagent was appended to the FIRST one's wire
    (Kimi reuses `agent-N` once its run ended), with the parent-side spans the
    hooks leave: two launches, both runs' tool calls flat under the prompt, and
    a `subagent.start` per run."""
    _append_run(agents / "agent-0" / "wire.jsonl", "c",
                "<git-context>x</git-context>\nExplore gamma subsystem now",
                ["call-c1"], "gamma done")
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute(
            "INSERT INTO sessions (trace_id, started_at, last_seen) "
            "VALUES (?, ?, ?)", (_SID, "2026-06-01T00:00:00", "2026-06-01T00:00:00"))

        def span(span_id, name, ts, attrs, tool_use_id=None, status="OK"):
            conn.execute(
                "INSERT INTO session_spans (trace_id, span_id, name, start_time, "
                "attributes, tool_use_id, status_code) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (_SID, span_id, name, ts, json.dumps(attrs), tool_use_id, status))

        for n, kind in (("0", "alpha"), ("1", "beta"), ("2", "gamma")):
            span(f"pending-launch-{n}", "tool.Agent", f"2026-06-01T00:00:0{n}",
                 {"prompt": f"Explore {kind} subsystem now",
                  "subagent_type": "explore", "tool_use_id": f"launch-{n}"},
                 status="PENDING")
            span(f"S{n}", "subagent.start", f"2026-06-01T00:00:1{n}",
                 {"agent_type": "kimi",
                  "prompt_preview": f"Explore {kind} subsystem now"})
        for tid in ("call-a1", "call-a2", "call-b1", "call-c1"):
            span(f"tsp-{tid}", "tool.Read", "2026-06-01T00:00:04",
                 {"tool_name": "Read"}, tool_use_id=tid)
        conn.commit()
    finally:
        conn.close()


def test_reused_agent_slot_is_split_into_one_agent_per_run(tmp_db, kimi_home):
    """A wire dir is a SLOT, not a subagent. Reconciling a reused slot as one
    subagent handed the second run's spans to the first run's agent_id and left
    the second with no identity at all — so its tool calls read as the main
    agent's and the roster could not see it."""
    _seed_reused_slot(tmp_db, kimi_home)
    result = reconcile_kimi_subagents(_SID)
    assert result["subagents"] == 3  # 2 runs in agent-0's slot + agent-1
    assert _agent_id_of(tmp_db, "call-a1") == "launch-0"
    assert _agent_id_of(tmp_db, "call-c1") == "launch-2"
    starts = {r["span_id"]: r["agent_id"] for r in _rows(
        tmp_db, "SELECT span_id, agent_id FROM session_spans "
                "WHERE trace_id = ? AND name = 'subagent.start' "
                "AND span_id IN ('S0', 'S2')")}
    assert starts == {"S0": "launch-0", "S2": "launch-2"}


def test_reused_slot_keeps_each_runs_task_prompt(tmp_db, kimi_home):
    """Each run's scoped view must open on ITS OWN task prompt, not the prompt
    of whichever run happened to write the slot first."""
    _seed_reused_slot(tmp_db, kimi_home)
    reconcile_kimi_subagents(_SID)
    prompts = {r["span_id"]: r["text"] for r in _rows(
        tmp_db, "SELECT span_id, json_extract(attributes, '$.text') AS text "
                "FROM session_spans WHERE trace_id = ? AND name = 'prompt' "
                "AND span_id LIKE 'prompt-sa-launch-%'")}
    assert prompts["prompt-sa-launch-0"] == "Explore alpha subsystem now"
    assert prompts["prompt-sa-launch-2"] == "Explore gamma subsystem now"


def test_live_pass_leaves_the_newest_run_open(tmp_db, kimi_home):
    """A pass that can catch a subagent mid-flight (the trace view's rescan)
    must not record an end for it: a synthesized stop or a closed launch both
    render a running agent as 'finished'."""
    _seed(tmp_db, with_prompt_preview=True, with_stops=False)
    result = reconcile_kimi_subagents(_SID, live=True)
    assert result["launches_closed"] == 0
    assert _rows(tmp_db, "SELECT span_id FROM session_spans "
                         "WHERE trace_id = ? AND name = 'subagent.stop'") == []
    # ...while still binding what IS known, so the roster sees the agent at all.
    assert _agent_id_of(tmp_db, "call-a1") == "launch-0"
    assert {r["status_code"] for r in _rows(
        tmp_db, "SELECT status_code FROM session_spans WHERE trace_id = ? "
                "AND name = 'tool.Agent'")} == {"PENDING"}


def test_live_pass_clears_a_synthetic_stop_over_a_live_run(tmp_db, kimi_home):
    """A synthetic stop left by an earlier (non-live) pass has to go once a live
    pass sees the run still writing — otherwise the agent stays 'finished'."""
    _seed(tmp_db, with_prompt_preview=True, with_stops=False)
    reconcile_kimi_subagents(_SID)
    assert len(_rows(tmp_db, "SELECT span_id FROM session_spans "
                             "WHERE trace_id = ? AND name = 'subagent.stop'")) == 2
    reconcile_kimi_subagents(_SID, live=True)
    assert _rows(tmp_db, "SELECT span_id FROM session_spans "
                         "WHERE trace_id = ? AND name = 'subagent.stop'") == []


def test_live_pass_still_binds_a_hook_stop_that_landed(tmp_db, kimi_home):
    """`live` is 'may be running', not 'assume running': a real SubagentStop
    marker already in the trace still closes its agent."""
    _seed(tmp_db, with_prompt_preview=True, with_stops=False)
    _add_stop_markers(tmp_db, "alpha done", "beta done")
    reconcile_kimi_subagents(_SID, live=True)
    stops = _rows(tmp_db, "SELECT span_id, agent_id FROM session_spans "
                          "WHERE trace_id = ? AND name = 'subagent.stop'")
    assert {r["span_id"]: r["agent_id"] for r in stops} == {
        "hookstop-0": "launch-0", "hookstop-1": "launch-1"}


def test_stale_synthetic_is_swept_when_the_agent_id_rekeys(tmp_db, kimi_home):
    """A subagent's `agent_id` changes from the positional fallback to the real
    `tool_use_id` once its launch span becomes matchable. The marker keyed on
    the old id has to go, or the session shows twice the subagents it ran."""
    conn = sqlite3.connect(str(tmp_db))
    try:
        conn.execute(
            "INSERT INTO sessions (trace_id, started_at, last_seen) "
            "VALUES (?, ?, ?)", (_SID, "2026-06-01T00:00:00", "2026-06-01T00:00:00"))
        conn.commit()
    finally:
        conn.close()
    reconcile_kimi_subagents(_SID)
    assert {r["span_id"] for r in _rows(
        tmp_db, "SELECT span_id FROM session_spans WHERE trace_id = ? "
                "AND name = 'subagent.start'")} == {
        f"sa-start-{_SID}:agent-0", f"sa-start-{_SID}:agent-1"}

    conn = sqlite3.connect(str(tmp_db))
    try:
        conn.execute(
            "INSERT INTO session_spans (trace_id, span_id, name, start_time, "
            "attributes, status_code) VALUES (?, 'launch-span-0', 'tool.Agent', "
            "'2026-06-01T00:00:02', ?, 'PENDING')",
            (_SID, json.dumps({"prompt": "Explore alpha subsystem now",
                               "tool_use_id": "launch-0"})))
        conn.commit()
    finally:
        conn.close()
    reconcile_kimi_subagents(_SID)

    starts = {r["span_id"] for r in _rows(
        tmp_db, "SELECT span_id FROM session_spans WHERE trace_id = ? "
                "AND name = 'subagent.start'")}
    assert starts == {"sa-start-launch-0", f"sa-start-{_SID}:agent-1"}
