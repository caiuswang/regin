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
