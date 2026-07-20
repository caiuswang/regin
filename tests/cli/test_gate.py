"""Tests for `regin gate recall-ran` and the span-gate helper.

The gate makes goal-verified-treenav's unenforced recall arm checkable: it
PASSes iff the session emitted memory-tree-nav / recall spans, FAILs (exit 1)
otherwise. The autouse `tmp_db` fixture isolates the SQLite file per test.
"""

from __future__ import annotations

import json

from typer.testing import CliRunner

from lib.orm import SessionLocal
from lib.orm.models import SessionSpan
from lib.trace.span_gates import GATES, RECALL_ARM, span_count
from cli.app import app


runner = CliRunner()


def _span(trace_id: str, name: str, span_id: str) -> SessionSpan:
    return SessionSpan(
        trace_id=trace_id, span_id=span_id, parent_id=None, name=name,
        kind="internal", start_time="2026-06-24 10:00:00",
    )


def _seed(trace_id: str, names: list[str]) -> None:
    with SessionLocal() as s:
        for i, name in enumerate(names):
            s.add(_span(trace_id, name, f"{trace_id}-{i}"))
        s.commit()


def test_span_count_matches_like_and_exact():
    _seed("sid-pass", [
        "tool.mcp__memory__index_root",      # LIKE match
        "tool.mcp__memory__index_expand",    # LIKE match
        "tool.mcp__memory__recall",          # exact match
        "tool.Bash",                         # no match
        "root",                              # no match
    ])
    assert span_count("sid-pass", RECALL_ARM) == 3


def test_span_count_zero_for_unrelated_session():
    _seed("sid-fail", ["tool.Bash", "tool.Read", "root"])
    assert span_count("sid-fail", RECALL_ARM) == 0


def test_recall_ran_passes_when_spans_present():
    _seed("sid-pass", ["tool.mcp__memory__index_root", "tool.mcp__memory__recall"])
    result = runner.invoke(app, ["gate", "recall-ran", "--session", "sid-pass"])
    assert result.exit_code == 0
    assert "GATE PASS" in result.stdout


def test_recall_ran_does_not_claim_a_skip_when_no_spans():
    # Was a hard FAIL. It cannot be: from the CLI, 0 spans is equally
    # consistent with "the memory MCP was never loaded". See
    # test_recall_ran_is_inconclusive_not_failed_from_the_cli below, and the
    # MCP-path counterpart that DOES fail hard, in tests/memory/test_mcp_gate.
    result = runner.invoke(app, ["gate", "recall-ran", "--session", "nope"])
    assert result.exit_code != 0
    assert "GATE PASS" not in result.stdout


def test_recall_ran_json_output():
    _seed("sid-json", ["tool.mcp__memory__index_fetch"])
    result = runner.invoke(
        app, ["gate", "recall-ran", "--session", "sid-json", "--json"])
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload == {
        "gate": "recall-ran", "session": "sid-json", "spans": 1,
        "pass": True, "status": "PASS", "capability_proven": False}


def test_recall_ran_is_inconclusive_not_failed_from_the_cli():
    # The CLI cannot see which MCP servers the session loaded, so 0 spans does
    # not prove a skip. Accusing the agent of skipping when the tool may never
    # have existed is the unfollowable-instruction bug that retired
    # `ui-verified`; here it must degrade to INCONCLUSIVE instead.
    result = runner.invoke(app, ["gate", "recall-ran", "--session", "sid-none"])
    assert result.exit_code == 2
    assert "INCONCLUSIVE" in result.stdout
    assert "GATE PASS" not in result.stdout
    assert "you skipped" not in result.stdout


def test_task_recall_still_fails_hard_because_its_tool_is_the_cli():
    # Contrast with the above: this span comes from `regin memory
    # recall-for-task`, so anyone who can run `regin gate` could have run the
    # step. Absence here IS a skip and must stay a hard wall.
    result = runner.invoke(app, ["gate", "task-recall-ran", "--session", "sid-none"])
    assert result.exit_code == 1
    assert "GATE FAIL" in result.stdout
    assert "skipped" in result.stdout


def test_recall_ran_passes_on_spans_from_the_cli():
    _seed("sid-arm", ["tool.mcp__memory__index_root"])
    result = runner.invoke(app, ["gate", "recall-ran", "--session", "sid-arm"])
    assert result.exit_code == 0
    assert "GATE PASS" in result.stdout


def test_json_payload_carries_status_and_capability():
    result = runner.invoke(
        app, ["gate", "recall-ran", "--session", "sid-none", "--json"])
    payload = json.loads(result.stdout)
    assert payload["status"] == "INCONCLUSIVE"
    assert payload["pass"] is False
    assert payload["capability_proven"] is False


def test_ui_verified_gate_is_retired():
    # It counted Playwright MCP spans, so a session without that MCP failed
    # identically to one that skipped the render — unpassable rather than
    # strict. The invariant lives in frontend/tests/responsive.spec.js now.
    assert "ui-verified" not in GATES
    result = runner.invoke(app, ["gate", "ui-verified", "--session", "sid-any"])
    assert result.exit_code != 0
    assert "GATE PASS" not in result.stdout
