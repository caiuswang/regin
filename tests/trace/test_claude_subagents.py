"""Tests for lib.trace.claude_subagents — attributing Claude Task-tool
subagent API spend onto the parent session's bill.

Claude writes each subagent's conversation to a sibling
``<projects>/<cwd>/<session_id>/subagents/agent-<id>.jsonl`` (not as parent
sidechains), so its token spend is invisible to ``turn_usage``. The reconciler
totals each subagent transcript and stamps cost/tokens onto its
``subagent.stop`` marker, which ``fetch_tool_token_rollup`` folds into the
``subagent_*`` line — without touching the main-model bill.
"""

from __future__ import annotations

import json
import sqlite3

import pytest

import lib.providers.claude as claude_provider
from lib.trace.claude_subagents import (
    discover_subagent_sessions, reconcile_claude_subagents,
)
from lib.trace.trace_service import fetch_tool_token_rollup

_SID = "claude_sub_sess"


def _assistant(uuid: str, parent: str, model: str, usage: dict) -> dict:
    return {
        "type": "assistant", "uuid": uuid, "parentUuid": parent,
        "timestamp": "2026-06-01T00:00:10.000Z",
        "message": {
            "id": f"msg_{uuid}", "role": "assistant", "model": model,
            "content": [{"type": "text", "text": "subagent working"}],
            "usage": usage,
        },
    }


def _write_subagent_transcript(path, model: str) -> None:
    """A minimal two-turn Claude subagent transcript carrying API usage."""
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = [
        {"type": "user", "uuid": "u1", "parentUuid": None,
         "timestamp": "2026-06-01T00:00:09.000Z",
         "message": {"role": "user", "content": "go"}},
        _assistant("a1", "u1", model, {
            "input_tokens": 100, "output_tokens": 50,
            "cache_read_input_tokens": 10_000, "cache_creation_input_tokens": 200}),
        _assistant("a2", "a1", model, {
            "input_tokens": 80, "output_tokens": 40,
            "cache_read_input_tokens": 20_000, "cache_creation_input_tokens": 100}),
    ]
    path.write_text("\n".join(json.dumps(r) for r in rows))


@pytest.fixture
def claude_home(tmp_path, monkeypatch):
    """Point ClaudeProvider's projects dir at a temp tree with one session
    that has two subagents, and return the projects base."""
    projects = tmp_path / "projects"
    monkeypatch.setattr(
        claude_provider.ClaudeProvider, "transcript_projects_dir",
        lambda self: projects,
    )
    sub = projects / "-proj-hash" / _SID / "subagents"
    _write_subagent_transcript(sub / "agent-aaa111.jsonl", "claude-opus-4-8")
    _write_subagent_transcript(sub / "agent-bbb222.jsonl", "claude-haiku-4-5")
    return projects


def _seed_session(db_path) -> None:
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute(
            "INSERT INTO sessions (trace_id, started_at, last_seen, "
            "model, input_tokens, output_tokens, cost_usd) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (_SID, "2026-06-01T00:00:00", "2026-06-01T00:00:00",
             "claude-opus-4-8", 1000, 2000, 5.0),
        )

        def span(span_id, name, attrs):
            conn.execute(
                "INSERT INTO session_spans (trace_id, span_id, name, "
                "start_time, attributes) VALUES (?, ?, ?, ?, ?)",
                (_SID, span_id, name, "2026-06-01T00:00:30", json.dumps(attrs)),
            )

        # One stop marker per subagent, carrying the agent_id the reconciler
        # matches on (the agent-<id> filename stem).
        span("T0", "subagent.stop", {"agent_id": "aaa111", "agent_type": "general-purpose"})
        span("T1", "subagent.stop", {"agent_id": "bbb222", "agent_type": "general-purpose"})
        conn.commit()
    finally:
        conn.close()


def _stop_costs(db_path) -> list:
    conn = sqlite3.connect(str(db_path))
    try:
        return [
            (r[0], r[1], r[2], r[3]) for r in conn.execute(
                "SELECT json_extract(attributes,'$.agent_id'), cost_usd, "
                "input_tokens, output_tokens FROM session_spans "
                "WHERE trace_id=? AND name='subagent.stop' ORDER BY span_id",
                (_SID,),
            )
        ]
    finally:
        conn.close()


def test_reconcile_stamps_stop_markers(tmp_db, claude_home):
    _seed_session(tmp_db)

    result = reconcile_claude_subagents(_SID)
    assert (result["subagents"], result["stamped"]) == (2, 2)
    assert result["cost_usd"] > 0

    # Both stop markers got per-subagent totals (input+output summed).
    by_agent = {a: (c, i, o) for a, c, i, o in _stop_costs(tmp_db)}
    assert by_agent["aaa111"][1:] == (180, 90)  # input 100+80, output 50+40
    assert by_agent["bbb222"][0] > 0
    # Opus turns cost more than the same-shaped haiku turns.
    assert by_agent["aaa111"][0] > by_agent["bbb222"][0]


def test_reconcile_rolls_up_separate_line(tmp_db, claude_home):
    _seed_session(tmp_db)
    result = reconcile_claude_subagents(_SID)

    # Separate sub-agent line; main bill (session_cost_usd) untouched.
    _, totals = fetch_tool_token_rollup(_SID)
    assert totals["session_cost_usd"] == 5.0
    assert totals["subagent_cost_usd"] == pytest.approx(result["cost_usd"])
    assert totals["subagent_tokens"] == 540  # (180+90) * 2 subagents
    assert totals["total_spend_usd"] == pytest.approx(5.0 + result["cost_usd"])


def test_reconcile_is_idempotent(tmp_db, claude_home):
    _seed_session(tmp_db)
    first = reconcile_claude_subagents(_SID)
    second = reconcile_claude_subagents(_SID)
    assert first["cost_usd"] == pytest.approx(second["cost_usd"])
    _, totals = fetch_tool_token_rollup(_SID)
    assert totals["subagent_cost_usd"] == pytest.approx(first["cost_usd"])


def test_no_subagent_dir_is_noop(tmp_db, claude_home):
    result = reconcile_claude_subagents("session_without_subagents")
    assert result == {"subagents": 0, "stamped": 0, "cost_usd": 0.0}


def test_discover_finds_sessions_with_subagent_dirs(tmp_db, claude_home):
    assert _SID in discover_subagent_sessions()


def _write_meta(path, *, tool_use_id=None, spawn_depth=None, agent_type="general-purpose"):
    """Write a sibling agent-<id>.meta.json (camelCase, like Claude Code)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    meta = {"agentType": agent_type}
    if tool_use_id is not None:
        meta["toolUseId"] = tool_use_id
    if spawn_depth is not None:
        meta["spawnDepth"] = spawn_depth
    path.write_text(json.dumps(meta))


def _write_spawning_transcript(path, tool_use_id: str) -> None:
    """A parent-agent transcript whose assistant turn carries a `tool_use`
    block with `tool_use_id` — the block that launched the nested child."""
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = [
        {"type": "user", "uuid": "pu1", "parentUuid": None,
         "timestamp": "2026-06-01T00:00:09.000Z",
         "message": {"role": "user", "content": "spawn a child"}},
        {"type": "assistant", "uuid": "pa1", "parentUuid": "pu1",
         "timestamp": "2026-06-01T00:00:10.000Z",
         "message": {"id": "msg_pa1", "role": "assistant",
                     "model": "claude-opus-4-8",
                     "content": [{"type": "tool_use", "id": tool_use_id,
                                  "name": "Agent", "input": {}}],
                     "usage": {"input_tokens": 10, "output_tokens": 5}}},
    ]
    path.write_text("\n".join(json.dumps(r) for r in rows))


def test_reconcile_stamps_nested_parent_agent_id(tmp_db, claude_home):
    """A depth-2 child agent whose meta.toolUseId resolves against a sibling
    parent's tool_use block gets `attributes.parent_agent_id` stamped on ITS
    spans — the nested-spawn edge the flat subagents/ dir hides."""
    projects = claude_home
    sub = projects / "-proj-hash" / _SID / "subagents"
    # Parent agent 'aaa111' (from the fixture) launches child 'ddd444' via a
    # tool_use block; give the parent a spawning transcript + child a meta.
    _write_spawning_transcript(sub / "agent-aaa111.jsonl", "toolu_spawn_child")
    _write_subagent_transcript(sub / "agent-ddd444.jsonl", "claude-opus-4-8")
    _write_meta(sub / "agent-ddd444.meta.json",
                tool_use_id="toolu_spawn_child", spawn_depth=2)

    conn = sqlite3.connect(str(tmp_db))
    try:
        conn.execute(
            "INSERT INTO sessions (trace_id, started_at, last_seen, model) "
            "VALUES (?, ?, ?, ?)",
            (_SID, "2026-06-01T00:00:00", "2026-06-01T00:00:00", "claude-opus-4-8"),
        )
        # A span owned by the child agent (agent_id in attributes).
        conn.execute(
            "INSERT INTO session_spans (trace_id, span_id, name, start_time, attributes) "
            "VALUES (?, ?, ?, ?, ?)",
            (_SID, "start-ddd444", "subagent.start", "2026-06-01T00:00:20",
             json.dumps({"agent_id": "ddd444"})),
        )
        conn.commit()
    finally:
        conn.close()

    result = reconcile_claude_subagents(_SID)
    assert result["nested_parented"] == 1

    conn = sqlite3.connect(str(tmp_db))
    try:
        pa = conn.execute(
            "SELECT json_extract(attributes, '$.parent_agent_id') "
            "FROM session_spans WHERE trace_id=? AND span_id='start-ddd444'",
            (_SID,),
        ).fetchone()[0]
    finally:
        conn.close()
    assert pa == "aaa111"


def _seed_minimal_session(db_path, agent_ids: list) -> None:
    """Bare `sessions` row + one `subagent.start` span per agent_id, mirroring
    the child-span shape `test_reconcile_stamps_nested_parent_agent_id` seeds."""
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute(
            "INSERT INTO sessions (trace_id, started_at, last_seen, model) "
            "VALUES (?, ?, ?, ?)",
            (_SID, "2026-06-01T00:00:00", "2026-06-01T00:00:00", "claude-opus-4-8"),
        )
        for agent_id in agent_ids:
            conn.execute(
                "INSERT INTO session_spans (trace_id, span_id, name, start_time, attributes) "
                "VALUES (?, ?, ?, ?, ?)",
                (_SID, f"start-{agent_id}", "subagent.start", "2026-06-01T00:00:20",
                 json.dumps({"agent_id": agent_id})),
            )
        conn.commit()
    finally:
        conn.close()


def _parent_agent_id_of(db_path, span_id: str):
    conn = sqlite3.connect(str(db_path))
    try:
        return conn.execute(
            "SELECT json_extract(attributes, '$.parent_agent_id') "
            "FROM session_spans WHERE trace_id=? AND span_id=?",
            (_SID, span_id),
        ).fetchone()[0]
    finally:
        conn.close()


def test_reconcile_drops_mutual_cycle_neither_stamped(tmp_db, claude_home):
    """Two agents whose meta.toolUseId resolve to EACH OTHER (a mutual
    2-cycle) must have neither stamped — a cyclic parent map would feed
    `_build_span_tree` a subtree with no reachable root, silently dropping
    both agents' spans (precision over recall: a wrong link is worse than
    none)."""
    projects = claude_home
    sub = projects / "-proj-hash" / _SID / "subagents"
    _write_spawning_transcript(sub / "agent-cycA.jsonl", "toolu_to_cycB")
    _write_spawning_transcript(sub / "agent-cycB.jsonl", "toolu_to_cycA")
    _write_meta(sub / "agent-cycA.meta.json", tool_use_id="toolu_to_cycA", spawn_depth=2)
    _write_meta(sub / "agent-cycB.meta.json", tool_use_id="toolu_to_cycB", spawn_depth=2)

    _seed_minimal_session(tmp_db, ["cycA", "cycB"])

    result = reconcile_claude_subagents(_SID)
    assert result["nested_parented"] == 0
    assert _parent_agent_id_of(tmp_db, "start-cycA") is None
    assert _parent_agent_id_of(tmp_db, "start-cycB") is None


def test_reconcile_drops_self_reference(tmp_db, claude_home):
    """An agent whose meta.toolUseId resolves to a tool_use block in its OWN
    transcript (self-reference) is never stamped as its own parent."""
    projects = claude_home
    sub = projects / "-proj-hash" / _SID / "subagents"
    _write_spawning_transcript(sub / "agent-selfx.jsonl", "toolu_self")
    _write_meta(sub / "agent-selfx.meta.json", tool_use_id="toolu_self", spawn_depth=2)

    _seed_minimal_session(tmp_db, ["selfx"])

    result = reconcile_claude_subagents(_SID)
    assert result["nested_parented"] == 0
    assert _parent_agent_id_of(tmp_db, "start-selfx") is None


def test_reconcile_drops_ambiguous_tool_use_owner(tmp_db, claude_home):
    """A `toolUseId` that appears in TWO sibling transcripts resolves to no
    one — stamping from it would be a guess. The child naming it is skipped
    entirely rather than picking the alphabetically-first owner."""
    projects = claude_home
    sub = projects / "-proj-hash" / _SID / "subagents"
    _write_spawning_transcript(sub / "agent-ownerC.jsonl", "toolu_dup")
    _write_spawning_transcript(sub / "agent-ownerD.jsonl", "toolu_dup")
    _write_subagent_transcript(sub / "agent-childE.jsonl", "claude-opus-4-8")
    _write_meta(sub / "agent-childE.meta.json", tool_use_id="toolu_dup", spawn_depth=2)

    _seed_minimal_session(tmp_db, ["childE"])

    result = reconcile_claude_subagents(_SID)
    assert result["nested_parented"] == 0
    assert _parent_agent_id_of(tmp_db, "start-childE") is None


def test_reconcile_does_not_stamp_depth1_agents(tmp_db, claude_home):
    """A depth-1 subagent (no meta / spawnDepth<2) is never stamped with a
    parent_agent_id — it keeps the flat 'under main' behavior."""
    _seed_session(tmp_db)  # aaa111 / bbb222, no meta.json → depth-1
    result = reconcile_claude_subagents(_SID)
    assert result["nested_parented"] == 0

    conn = sqlite3.connect(str(tmp_db))
    try:
        rows = conn.execute(
            "SELECT json_extract(attributes, '$.parent_agent_id') "
            "FROM session_spans WHERE trace_id=?",
            (_SID,),
        ).fetchall()
    finally:
        conn.close()
    assert all(r[0] is None for r in rows)


def test_reconcile_stamps_nested_workflow_subagents(tmp_db, claude_home):
    """Workflow-tool subagents live one level deeper, under
    ``subagents/workflows/<wf>/agent-*.jsonl``; their spend must be attributed
    too (it was silently dropped when the reconciler globbed only top-level)."""
    projects = claude_home
    wf = projects / "-proj-hash" / _SID / "subagents" / "workflows" / "wf_x"
    _write_subagent_transcript(wf / "agent-ccc333.jsonl", "claude-opus-4-8")

    conn = sqlite3.connect(str(tmp_db))
    try:
        conn.execute(
            "INSERT INTO sessions (trace_id, started_at, last_seen, "
            "model, input_tokens, output_tokens, cost_usd) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (_SID, "2026-06-01T00:00:00", "2026-06-01T00:00:00",
             "claude-opus-4-8", 1000, 2000, 5.0),
        )
        conn.execute(
            "INSERT INTO session_spans (trace_id, span_id, name, "
            "start_time, attributes) VALUES (?, ?, ?, ?, ?)",
            (_SID, "T0", "subagent.stop", "2026-06-01T00:00:30",
             json.dumps({"agent_id": "aaa111"})),
        )
        conn.execute(
            "INSERT INTO session_spans (trace_id, span_id, name, "
            "start_time, attributes) VALUES (?, ?, ?, ?, ?)",
            (_SID, "T2", "subagent.stop", "2026-06-01T00:00:30",
             json.dumps({"agent_id": "ccc333"})),
        )
        conn.commit()
    finally:
        conn.close()

    reconcile_claude_subagents(_SID)
    # Both the top-level (aaa111) and the nested workflow (ccc333) subagent
    # transcripts are discovered and stamped.
    by_agent = {a: (c, i, o) for a, c, i, o in _stop_costs(tmp_db)}
    assert {"aaa111", "ccc333"} == set(by_agent)
    assert by_agent["ccc333"][1:] == (180, 90)
    assert by_agent["ccc333"][0] > 0
    assert _SID in discover_subagent_sessions()
