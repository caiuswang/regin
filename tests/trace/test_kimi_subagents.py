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
                   tool_ids: list[str], final_text: str) -> None:
    """A minimal subagent wire: one prompt, a tool-call step, and a text step.
    Step uuids are prefixed so two subagents never collide (real Kimi uuids are
    globally unique; the replayed-turn span ids are derived from them)."""
    s1, s2 = f"{prefix}-s1", f"{prefix}-s2"
    records: list[dict] = [
        {"type": "metadata", "protocol_version": "1.4", "created_at": 1},
        {"type": "turn.prompt",
         "input": [{"type": "text", "text": first_prompt}], "time": 1_000},
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


def _seed(db_path, *, with_prompt_preview: bool) -> None:
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute(
            "INSERT INTO sessions (trace_id, started_at, last_seen) VALUES (?, ?, ?)",
            (_SID, "2026-06-01T00:00:00", "2026-06-01T00:00:00"),
        )

        def span(span_id, name, ts, attrs, tool_use_id=None, parent_id=None):
            conn.execute(
                "INSERT INTO session_spans (trace_id, span_id, name, start_time, "
                "attributes, tool_use_id, parent_id) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (_SID, span_id, name, ts, json.dumps(attrs), tool_use_id, parent_id),
            )

        span("conv", "conversation", "2026-06-01T00:00:00", {})
        span("prompt-1", "prompt", "2026-06-01T00:00:01", {}, parent_id="conv")
        # Launch spans carry the prompt that re-identifies each subagent.
        span("agent-0", "tool.Agent", "2026-06-01T00:00:02",
             {"prompt": "Explore alpha subsystem now", "subagent_type": "explore",
              "description": "alpha"}, tool_use_id="launch-0", parent_id="prompt-1")
        span("agent-1", "tool.Agent", "2026-06-01T00:00:03",
             {"prompt": "Explore beta subsystem now", "subagent_type": "explore",
              "description": "beta"}, tool_use_id="launch-1", parent_id="prompt-1")
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
    assert result == {"subagents": 2, "tool_spans": 3, "turns": 4}
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


def test_no_subagents_is_noop(tmp_db, monkeypatch, tmp_path):
    monkeypatch.setattr(kimi_provider, "_KIMI_HOME", tmp_path)
    assert reconcile_kimi_subagents("session_missing") == {
        "subagents": 0, "tool_spans": 0, "turns": 0}
