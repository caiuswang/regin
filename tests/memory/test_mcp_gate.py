"""Tests for the `gate` tool on the memory MCP server.

This is the MCP-native form of `regin gate <name> --session <id>`: it PASSes
iff the session emitted the gate's spans. It shares `GATES` / `span_count` with
the CLI, so these tests focus on the tool's own contract — name validation, the
required-and-not-inferred session id (the shared-server footgun), and that the
verdict text matches the CLI. The autouse `tmp_db` fixture isolates the DB.
"""

from __future__ import annotations

from lib.orm import SessionLocal
from lib.orm.models import SessionSpan
from lib.memory.mcp_server import gate


def _span(trace_id: str, name: str, span_id: str) -> SessionSpan:
    return SessionSpan(
        trace_id=trace_id, span_id=span_id, parent_id=None, name=name,
        kind="internal", start_time="2026-06-30 10:00:00",
    )


def _seed(trace_id: str, names: list[str]) -> None:
    with SessionLocal() as s:
        for i, name in enumerate(names):
            s.add(_span(trace_id, name, f"{trace_id}-{i}"))
        s.commit()


def test_gate_passes_when_recall_spans_present():
    _seed("sid-pass", ["tool.mcp__memory__index_root", "tool.mcp__memory__recall"])
    out = gate("recall-ran", "sid-pass")
    assert "GATE PASS" in out
    assert "spans this session: 2" in out


def test_gate_fails_when_no_spans():
    _seed("sid-fail", ["tool.Bash", "tool.Read"])
    out = gate("recall-ran", "sid-fail")
    assert "GATE FAIL" in out
    assert "spans this session: 0" in out


def test_gate_supports_task_recall_gate():
    _seed("sid-task", ["memory.recall.task"])
    out = gate("task-recall-ran", "sid-task")
    assert "GATE PASS" in out


def test_unknown_gate_name_lists_valid_gates():
    out = gate("bogus", "sid-pass")
    assert "unknown gate" in out
    assert "recall-ran" in out
    assert "GATE PASS" not in out


def test_missing_session_id_errors_without_inferring():
    # The shared server must NOT fall back to its own env (the spawner's id);
    # an empty session id is an instructive error, never a wrong-session check.
    _seed("sid-pass", ["tool.mcp__memory__index_root"])
    out = gate("recall-ran", "")
    assert "session_id is required" in out
    assert "GATE PASS" not in out
    assert "GATE FAIL" not in out
