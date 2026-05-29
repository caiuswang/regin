"""Tests for dynamic-workflow run capture (`lib.trace.workflow_ingest`).

Fixtures are fully synthetic and built under ``tmp_path`` — no hardcoded
local run ids, no dependence on any real ~/.claude artifacts. The autouse
``tmp_db`` fixture (tests/conftest.py) points the ingest connection at a
fresh schema'd DB per test, so the DB-touching tests are isolated.
"""

from __future__ import annotations

import json
from pathlib import Path

from lib.orm.engine import get_connection
from lib.trace import workflow_ingest as W

RUN_ID = "wf_testrun01"

# A minimal agent transcript in the Claude Code shape `read_usage` parses:
# two assistant turns (deduped by message.id), the first issuing a Bash
# tool_use. cwd + timestamp on the first entry feed live start/repo tagging.
_AGENT_ENTRIES = [
    {"type": "user", "uuid": "u1", "timestamp": "2026-01-01T00:00:00Z",
     "cwd": "/tmp/repo", "message": {"role": "user", "content": "do X"}},
    {"type": "assistant", "uuid": "a1", "timestamp": "2026-01-01T00:00:01Z",
     "message": {"id": "m1", "model": "claude-haiku-4-5", "role": "assistant",
                 "stop_reason": "tool_use",
                 "usage": {"input_tokens": 10, "output_tokens": 20,
                           "cache_read_input_tokens": 0,
                           "cache_creation_input_tokens": 0},
                 "content": [{"type": "text", "text": "working"},
                             {"type": "tool_use", "id": "tu1", "name": "Bash",
                              "input": {"command": "ls"}}]}},
    {"type": "assistant", "uuid": "a2", "timestamp": "2026-01-01T00:00:02Z",
     "message": {"id": "m2", "model": "claude-haiku-4-5", "role": "assistant",
                 "stop_reason": "end_turn",
                 "usage": {"input_tokens": 12, "output_tokens": 30,
                           "cache_read_input_tokens": 0,
                           "cache_creation_input_tokens": 0},
                 "content": [{"type": "text", "text": "done"}]}},
]
_AGENT_JSONL = "\n".join(json.dumps(e) for e in _AGENT_ENTRIES) + "\n"

_JOURNAL = "\n".join(json.dumps(e) for e in [
    {"type": "started", "key": "v2:x", "agentId": "aAAA"},
    {"type": "started", "key": "v2:y", "agentId": "aBBB"},
    {"type": "result", "key": "v2:x", "agentId": "aAAA", "result": "{\"ok\":true}"},
]) + "\n"

_MANIFEST = {
    "runId": RUN_ID, "startTime": 1780000000000, "durationMs": 5000,
    "status": "completed", "workflowName": "synthetic-wf",
    "summary": "synthetic workflow for tests", "taskId": "task_test",
    "defaultModel": "claude-haiku-4-5", "agentCount": 2,
    "totalTokens": 100, "totalToolCalls": 1,
    "phases": [{"title": "Map", "detail": "d1"}, {"title": "Reduce", "detail": "d2"}],
    "workflowProgress": [
        {"type": "workflow_phase", "index": 1, "title": "Map"},
        {"type": "workflow_phase", "index": 2, "title": "Reduce"},
        {"type": "workflow_agent", "index": 1, "label": "a:one", "phaseIndex": 1,
         "phaseTitle": "Map", "agentId": "aAAA", "agentType": "Explore",
         "model": "claude-haiku-4-5", "state": "done", "startedAt": 1780000000100,
         "durationMs": 2000, "tokens": 50, "toolCalls": 1,
         "promptPreview": "p1", "resultPreview": "r1"},
        {"type": "workflow_agent", "index": 2, "label": "a:two", "phaseIndex": 2,
         "phaseTitle": "Reduce", "agentId": "aBBB", "agentType": "Explore",
         "model": "claude-haiku-4-5", "state": "done", "startedAt": 1780000002100,
         "durationMs": 2000, "tokens": 50, "toolCalls": 0,
         "promptPreview": "p2", "resultPreview": "r2"},
    ],
}


def _make_run(tmp_path: Path, *, with_manifest: bool) -> Path:
    """Build a synthetic run tree; return the projects-root to scan."""
    projects = tmp_path / "projects"
    sess = projects / "proj" / "sess"
    agents = sess / "subagents" / "workflows" / RUN_ID
    agents.mkdir(parents=True)
    (agents / "journal.jsonl").write_text(_JOURNAL)
    for aid in ("aAAA", "aBBB"):
        (agents / f"agent-{aid}.jsonl").write_text(_AGENT_JSONL)
        (agents / f"agent-{aid}.meta.json").write_text(
            json.dumps({"agentType": "Explore"}))
    scripts = sess / "workflows" / "scripts"
    scripts.mkdir(parents=True)
    (scripts / f"synthetic-{RUN_ID}.js").write_text(
        "export const meta = { name: 'synthetic-wf', "
        "description: 'synthetic workflow for tests' }\n")
    if with_manifest:
        (sess / "workflows" / f"{RUN_ID}.json").write_text(json.dumps(_MANIFEST))
    return projects


def _by_name(spans):
    out = {}
    for s in spans:
        out.setdefault(s["name"], []).append(s)
    return out


def _agents_by_id(spans):
    return {s["attributes"]["agent_id"]: s
            for s in spans if s["name"] == "subagent.start"}


def _parent_ids(spans):
    return {s["parent_id"] for s in spans}


def _phase_index(phase_spans):
    return {p["attributes"]["index"]: p["span_id"] for p in phase_spans}


def _all_have_output_tokens(spans):
    return all(s["attributes"].get("output_tokens") for s in spans)


def test_discover_runs_finds_run(tmp_path):
    projects = _make_run(tmp_path, with_manifest=True)
    refs = W.discover_runs(projects)
    assert len(refs) == 1
    ref = refs[0]
    assert ref.run_id == RUN_ID
    assert ref.terminal is True
    assert ref.script_path is not None


def test_discover_runs_live_is_not_terminal(tmp_path):
    projects = _make_run(tmp_path, with_manifest=False)
    ref = W.discover_runs(projects)[0]
    assert ref.terminal is False


def test_build_flat_spans_is_phaseless(tmp_path):
    projects = _make_run(tmp_path, with_manifest=False)
    ref = W.discover_runs(projects)[0]
    by = _by_name(W.build_flat_spans(ref, is_test=True))

    assert "workflow.phase" not in by                 # no phases live
    assert "session.end" not in by                    # run still going
    root = by["session.start"][0]
    assert root["attributes"]["agent_type"] == "workflow"
    assert root["attributes"]["workflow_status"] == "running"
    assert by["prompt"][0]["attributes"]["text"] == "synthetic workflow for tests"
    # agents hang directly off the run root while live
    assert _parent_ids(by["subagent.start"]) == {root["span_id"]}


def test_build_flat_spans_agent_state_and_tokens(tmp_path):
    projects = _make_run(tmp_path, with_manifest=False)
    ref = W.discover_runs(projects)[0]
    agents = _agents_by_id(W.build_flat_spans(ref, is_test=True))

    assert set(agents) == {"aAAA", "aBBB"}
    assert agents["aAAA"]["attributes"]["state"] == "done"      # had a result
    assert agents["aBBB"]["attributes"]["state"] == "running"   # started, no result
    # live token total comes from the transcript (20 + 30 = 50 per agent)
    assert agents["aAAA"]["attributes"]["tokens"] == 50


def test_build_full_spans_tree_and_deep_turns(tmp_path):
    projects = _make_run(tmp_path, with_manifest=True)
    ref = W.discover_runs(projects)[0]
    manifest = W._read_json(ref.manifest_path)
    spans = W.build_full_spans(manifest, ref.agents_dir, deep=True, is_test=True)
    by = _by_name(spans)

    root = by["session.start"][0]
    assert root["attributes"]["cwd"] == "/tmp/repo"            # repo tagging signal
    assert len(by["workflow.phase"]) == 2
    assert _parent_ids(by["workflow.phase"]) == {root["span_id"]}

    phase_ids = _phase_index(by["workflow.phase"])
    agents = _agents_by_id(spans)
    assert agents["aAAA"]["parent_id"] == phase_ids[1]         # Map
    assert agents["aBBB"]["parent_id"] == phase_ids[2]         # Reduce

    # deep: each agent's transcript -> 2 turns; aAAA's first turn -> 1 tool
    assert len(by["assistant_response"]) == 4                  # 2 turns x 2 agents
    assert _all_have_output_tokens(by["assistant_response"])
    assert len(by.get("tool.Bash", [])) == 2                  # one per agent transcript
    assert by["session.end"][0]["status_code"] == "OK"


def test_full_spans_enrich_tool_args_prompt_result(tmp_path):
    projects = _make_run(tmp_path, with_manifest=True)
    ref = W.discover_runs(projects)[0]
    manifest = W._read_json(ref.manifest_path)
    spans = W.build_full_spans(manifest, ref.agents_dir, deep=True, is_test=True)
    by = _by_name(spans)

    # tool rows carry label-driving attrs reconstructed from tool_input,
    # so the conversation shows "Bash: ls" rather than a bare "Bash"
    bash = by["tool.Bash"][0]["attributes"]
    assert bash["command_preview"] == "ls"
    assert bash["tool_input"] == {"command": "ls"}

    agents = _agents_by_id(spans)
    # full prompt sourced from the transcript's first user message (the
    # manifest only carried the "p1" preview)
    assert agents["aAAA"]["attributes"]["prompt"] == "do X"
    # full result sourced from the journal result event
    assert agents["aAAA"]["attributes"]["result_full"] == '{"ok":true}'


_THINK_AGENT = "\n".join(json.dumps(e) for e in [
    {"type": "user", "uuid": "u1", "timestamp": "2026-01-01T00:00:00Z",
     "cwd": "/tmp/repo", "message": {"role": "user", "content": "do X"}},
    {"type": "assistant", "uuid": "a1", "timestamp": "2026-01-01T00:00:01Z",
     "message": {"id": "m1", "model": "claude-x", "role": "assistant",
                 "stop_reason": "tool_use",
                 "usage": {"input_tokens": 1, "output_tokens": 5,
                           "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0},
                 "content": [{"type": "text", "text": "working"},
                             {"type": "tool_use", "id": "tu1", "name": "Bash",
                              "input": {"command": "ls"}}]}},
    {"type": "user", "uuid": "u2", "timestamp": "2026-01-01T00:00:02Z",
     "message": {"role": "user", "content": [
         {"type": "tool_result", "tool_use_id": "tu1",
          "content": "file1\nfile2", "is_error": False}]}},
    {"type": "assistant", "uuid": "a2", "timestamp": "2026-01-01T00:00:03Z",
     "message": {"id": "m2", "model": "claude-x", "role": "assistant",
                 "stop_reason": "end_turn",
                 "usage": {"input_tokens": 1, "output_tokens": 7,
                           "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0},
                 "content": [{"type": "thinking",
                              "thinking": "let me reason about this",
                              "signature": "sig"}]}},
    {"type": "assistant", "uuid": "a3", "timestamp": "2026-01-01T00:00:04Z",
     "message": {"id": "m3", "model": "claude-x", "role": "assistant",
                 "stop_reason": "end_turn",
                 "usage": {"input_tokens": 1, "output_tokens": 3,
                           "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0},
                 "content": [{"type": "text", "text": "done"}]}},
]) + "\n"


def test_agent_turn_spans_bash_output_and_thinking(tmp_path):
    adir = tmp_path / "ad"
    adir.mkdir()
    (adir / "agent-aZ.jsonl").write_text(_THINK_AGENT)
    by = _by_name(W._agent_turn_spans("wf_x", "wfagent-x", "aZ", adir, True))

    bash = by["tool.Bash"][0]["attributes"]
    assert bash["command"] == "ls"
    assert bash["stdout"] == "file1\nfile2"          # from the tool_result block
    # the thinking-only turn becomes an assistant.thinking head, not a response
    assert by["assistant.thinking"][0]["attributes"]["thinking_text"]
    assert len(by.get("assistant_response", [])) == 2  # "working" + "done"


def test_ingest_flat_then_full_is_idempotent(tmp_path):
    # 1) live ingest (no manifest) -> active, phaseless
    projects = _make_run(tmp_path, with_manifest=False)
    W.ingest_run(W.discover_runs(projects)[0], deep=True, is_test=True)

    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT agent_type, status FROM sessions WHERE trace_id=?",
            (RUN_ID,)).fetchone()
        assert row["agent_type"] == "workflow"
        assert row["status"] == "active"
        phases = conn.execute(
            "SELECT COUNT(*) c FROM session_spans WHERE trace_id=? "
            "AND name='workflow.phase'", (RUN_ID,)).fetchone()["c"]
        assert phases == 0                                    # no phases while live
    finally:
        conn.close()

    # 2) completion: manifest lands -> full tree replaces the flat one
    (projects / "proj" / "sess" / "workflows" / f"{RUN_ID}.json").write_text(
        json.dumps(_MANIFEST))
    ref = W.discover_runs(projects)[0]
    assert ref.terminal is True
    W.ingest_run(ref, deep=True, is_test=True)

    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT status, output_tokens FROM sessions WHERE trace_id=?",
            (RUN_ID,)).fetchone()
        assert row["status"] == "ended"
        assert row["output_tokens"] == 100                    # manifest totalTokens
        full_count = conn.execute(
            "SELECT COUNT(*) c FROM session_spans WHERE trace_id=?",
            (RUN_ID,)).fetchone()["c"]
        phases = conn.execute(
            "SELECT COUNT(*) c FROM session_spans WHERE trace_id=? "
            "AND name='workflow.phase'", (RUN_ID,)).fetchone()["c"]
        assert phases == 2                                    # phases now present
        # no stale rows from the flat pass: trace_map mirrors session_spans
        map_count = conn.execute(
            "SELECT COUNT(*) c FROM session_trace_map WHERE trace_id=?",
            (RUN_ID,)).fetchone()["c"]
        assert map_count == full_count
    finally:
        conn.close()

    # 3) re-ingest the terminal run -> identical (deterministic ids)
    W.ingest_run(ref, deep=True, is_test=True)
    conn = get_connection()
    try:
        again = conn.execute(
            "SELECT COUNT(*) c FROM session_spans WHERE trace_id=?",
            (RUN_ID,)).fetchone()["c"]
        assert again == full_count
    finally:
        conn.close()
