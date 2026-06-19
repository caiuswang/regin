"""Shared fixtures for the session-grader test suite.

`make_span` / `seed_spans` mirror the span factory pattern used by
tests/trace: dicts shaped like the merged projection rows the grader
consumes, inserted raw into `session_spans` when a DB-backed path is
under test. `make_evidence` builds an EvidenceIndex directly from span
dicts, bypassing the DB for the pure pipeline tests.
"""

from __future__ import annotations

import json

import pytest

from lib.grader.evidence import EvidenceIndex, build_evidence


def make_span(span_id: str, name: str, *, status: str = "OK",
              start: str = "2026-06-12T10:00:00", **attrs) -> dict:
    """One projection-shaped span dict."""
    return {
        "span_id": span_id,
        "name": name,
        "status_code": status,
        "start_time": start,
        "attributes": attrs,
    }


def prompt_span(text: str, span_id: str = "p1") -> dict:
    return make_span(span_id, "prompt", text=text)


def response_span(text: str, span_id: str = "r1") -> dict:
    return make_span(span_id, "assistant_response", text=text)


def read_span(span_id: str, path: str, content: str = "") -> dict:
    return make_span(span_id, "tool.Read", tool_name="Read",
                     file_path=path, content=content)


def bash_span(span_id: str, command: str, *, stdout: str = "",
              stderr: str = "", status: str = "OK") -> dict:
    return make_span(span_id, "tool.Bash", tool_name="Bash", status=status,
                     command=command, stdout=stdout, stderr=stderr)


def failure_span(span_id: str, tool_name: str, *, command: str = "",
                 error: str = "Exit code 1", file_path: str = "") -> dict:
    """The HOOK capture shape for a failed call: span name `tool.failure`,
    the real tool only in attrs, error text under `error` (see
    hook_manager/handlers/post_tool_failure.py). Distinct from the
    workflow-ingest shape (tool.<Name> + status ERROR) built by
    bash_span(status='ERROR')."""
    attrs = {"tool_name": tool_name, "error": error}
    if command:
        attrs["command"] = command
    if file_path:
        attrs["file_path"] = file_path
    return make_span(span_id, "tool.failure", status="ERROR", **attrs)


def edit_span(span_id: str, path: str, diff: str = "") -> dict:
    return make_span(span_id, "tool.Edit", tool_name="Edit",
                     file_path=path, diff=diff)


def fetch_span(span_id: str, url: str) -> dict:
    return make_span(span_id, "tool.WebFetch", tool_name="WebFetch", url=url)


def grep_span(span_id: str, pattern: str) -> dict:
    return make_span(span_id, "tool.Grep", tool_name="Grep", pattern=pattern)


@pytest.fixture
def make_evidence(monkeypatch):
    """Build an EvidenceIndex from span dicts without touching the DB."""
    import lib.grader.evidence as evidence_mod

    def _build(spans: list[dict], *, trace_id: str = "t-grader",
               session_row: dict | None = None) -> EvidenceIndex:
        row = session_row if session_row is not None else {
            "cost_usd": 0.5, "prompts": 1, "input_tokens": 1000,
            "cache_read_tokens": 100, "cache_creation_tokens": 50,
        }
        monkeypatch.setattr(evidence_mod, "_load_session_row",
                            lambda tid: row)
        return build_evidence(trace_id, spans)

    return _build


def seed_spans(trace_id: str, spans: list[dict]) -> None:
    """Insert spans + a sessions row into the (tmp) primary DB."""
    from lib.orm.engine import get_connection

    conn = get_connection()
    try:
        for span in spans:
            conn.execute(
                "INSERT INTO session_spans (trace_id, span_id, name, "
                "start_time, attributes, status_code) VALUES (?,?,?,?,?,?)",
                (trace_id, span["span_id"], span["name"], span["start_time"],
                 json.dumps(span["attributes"]), span["status_code"]))
        conn.execute(
            "INSERT OR REPLACE INTO sessions (trace_id, title, status, "
            "started_at, last_seen, prompts, tool_calls, cost_usd, "
            "input_tokens, cache_read_tokens, cache_creation_tokens, is_test) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (trace_id, "grader test session", "ended",
             "2026-06-12T10:00:00", "2026-06-12T11:00:00", 1,
             sum(1 for s in spans if s["name"].startswith("tool.")),
             0.5, 1000, 100, 50, 0))
        conn.commit()
    finally:
        conn.close()


class StubLLM:
    """Scripted judge: returns queued answers in order, records prompts."""

    def __init__(self, *answers: str):
        self.answers = list(answers)
        self.prompts: list[str] = []
        self.judge_id = "stub"

    def complete(self, prompt: str, *, max_tokens: int = 1024):
        self.prompts.append(prompt)
        return self.answers.pop(0) if self.answers else None
