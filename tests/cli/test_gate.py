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
from lib.trace.span_gates import RECALL_ARM, UI_VERIFIED, span_count
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


def test_recall_ran_fails_when_no_spans():
    result = runner.invoke(app, ["gate", "recall-ran", "--session", "nope"])
    assert result.exit_code == 1
    assert "GATE FAIL" in result.stdout


def test_recall_ran_json_output():
    _seed("sid-json", ["tool.mcp__memory__index_fetch"])
    result = runner.invoke(
        app, ["gate", "recall-ran", "--session", "sid-json", "--json"])
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload == {
        "gate": "recall-ran", "session": "sid-json", "spans": 1, "pass": True}


def test_ui_verified_counts_browser_spans_ignores_bash():
    # Playwright run as a Bash-driven node script lands as opaque tool.Bash — the
    # gate must NOT count it, or it would rubber-stamp a diff-only "done".
    _seed("sid-ui", [
        "tool.mcp__plugin_playwright_playwright__browser_navigate",    # match
        "tool.mcp__plugin_playwright_playwright__browser_take_screenshot",  # match
        "tool.Bash",                                                   # no match
        "tool.mcp__memory__recall",                                    # no match
    ])
    assert span_count("sid-ui", UI_VERIFIED) == 2


def test_ui_verified_passes_with_browser_spans():
    _seed("sid-ui-pass", ["tool.mcp__plugin_playwright_playwright__browser_evaluate"])
    result = runner.invoke(app, ["gate", "ui-verified", "--session", "sid-ui-pass"])
    assert result.exit_code == 0
    assert "GATE PASS" in result.stdout


def test_ui_verified_fails_without_browser_spans():
    _seed("sid-ui-fail", ["tool.Bash", "tool.Read", "tool.Edit"])
    result = runner.invoke(app, ["gate", "ui-verified", "--session", "sid-ui-fail"])
    assert result.exit_code == 1
    assert "GATE FAIL" in result.stdout
