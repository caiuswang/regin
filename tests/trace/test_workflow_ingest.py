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

# The run's persisted script. Byte-identical to the parent Workflow tool
# call's `input.script` — that exact-match is how `_stamp_parent_link`
# ties a run back to the tool call that launched it. The `phases` carry
# deliberately adversarial detail strings (a `]`/`{` inside a value, a
# comment with braces, mixed quotes, a trailing comma) so the meta parser is
# exercised on exactly the syntax a regex would mangle.
_SCRIPT_BODY = (
    "export const meta = {\n"
    "  name: 'synthetic-wf',\n"
    "  description: 'synthetic workflow for tests',\n"
    "  // a comment with } and ] must not fool the brace scanner\n"
    "  phases: [\n"
    "    { title: 'Map', detail: 'scan arrays [0] and {braces}' },\n"
    "    { title: 'Reduce', detail: \"merge, then done\", },\n"
    "  ],\n"
    "}\n"
)

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
    # Deliberately != the real read_usage output sum (50/agent × 2 = 100) so
    # tests prove the session token columns come from the transcripts, not this.
    "totalTokens": 999, "totalToolCalls": 1,
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
    (scripts / f"synthetic-{RUN_ID}.js").write_text(_SCRIPT_BODY)
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
    # prompt text is the script `description`, parsed from meta via tree-sitter
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


def test_session_tokens_are_real_split_not_manifest_total(tmp_path):
    """The session row carries the real input/output/cache split summed from
    the agent transcripts (read_usage) — so `output_tokens` is output-only and
    the tool-rollup's `untagged = output - attributed_output` stays honest —
    NOT the manifest's grand `totalTokens` (which folds in cache+input and made
    the whole total surface as bogus 'untagged' output)."""
    W.ingest_run(W.discover_runs(_make_run(tmp_path, with_manifest=True))[0],
                 deep=True, is_test=True)
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT input_tokens, output_tokens, cache_read_tokens, "
            "cache_creation_tokens FROM sessions WHERE trace_id=?",
            (RUN_ID,)).fetchone()
    finally:
        conn.close()
    # each agent transcript: input 10+12=22, output 20+30=50, no cache; ×2 agents
    assert row["output_tokens"] == 100      # output-only, not manifest's 999
    assert row["input_tokens"] == 44
    assert row["cache_read_tokens"] == 0
    assert row["cache_creation_tokens"] == 0


def test_agent_tool_count_uses_captured_spans_not_manifest(tmp_path):
    """The agent header's `tool_calls` counts the captured `tool.*` spans (what
    the conversation renders), not the manifest's `toolCalls` — which
    undercounts server-side tools like advisor. aBBB's manifest `toolCalls` is
    0, but its transcript makes one Bash call, so the deep span must report 1;
    with deep=False (no turn spans built) it falls back to the manifest's 0."""
    ref = W.discover_runs(_make_run(tmp_path, with_manifest=True))[0]
    manifest = W._read_json(ref.manifest_path)
    deep = _agents_by_id(W.build_full_spans(manifest, ref.agents_dir, deep=True, is_test=True))
    assert deep["aAAA"]["attributes"]["tool_calls"] == 1
    assert deep["aBBB"]["attributes"]["tool_calls"] == 1      # manifest said 0
    flat = _agents_by_id(W.build_full_spans(manifest, ref.agents_dir, deep=False, is_test=True))
    assert flat["aBBB"]["attributes"]["tool_calls"] == 0      # fallback to manifest


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
        # output-only, summed from the agent transcripts via read_usage
        # (50/agent × 2) — NOT the manifest's grand totalTokens (999)
        assert row["output_tokens"] == 100
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


def _row_attrs(conn, trace_id, where):
    row = conn.execute(
        f"SELECT attributes FROM session_spans WHERE trace_id=? AND {where}",
        (trace_id,)).fetchone()
    return json.loads(row["attributes"]) if row else None


def test_full_ingest_titles_session_with_workflow_name(tmp_path):
    projects = _make_run(tmp_path, with_manifest=True)
    W.ingest_run(W.discover_runs(projects)[0], deep=True, is_test=True)

    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT title, title_source FROM sessions WHERE trace_id=?",
            (RUN_ID,)).fetchone()
        # title is the short workflow NAME, not the long objective sentence
        assert (row["title"], row["title_source"]) == ("synthetic-wf", "workflow_name")
        # the run root records its launching Claude session (dir name)
        assert _row_attrs(conn, RUN_ID, "name='session.start'")["parent_trace_id"] == "sess"
        # the objective still surfaces as the opening prompt bubble
        assert _row_attrs(conn, RUN_ID, "name='prompt'")["text"] == "synthetic workflow for tests"
    finally:
        conn.close()


def _parent_workflow_span(tool_use_id):
    """A synthetic parent-session `tool.Workflow` span (what the PostToolUse
    hook records when the main agent calls the Workflow tool)."""
    return {
        "trace_id": "sess", "span_id": "parentspan01", "parent_id": None,
        "name": "tool.Workflow", "kind": "internal",
        "start_time": "2026-01-01T00:00:00Z", "end_time": "2026-01-01T00:00:00Z",
        "duration_ms": 0, "status_code": "OK", "status_message": None,
        "attributes": {"tool_name": "Workflow", "tool_use_id": tool_use_id,
                       "agent_type": "claude", "is_test": True},
    }


def test_stamp_parent_link_cross_links_run_and_tool_span(tmp_path):
    from lib.trace.trace_service import ingest_session_spans
    projects = _make_run(tmp_path, with_manifest=True)
    # the launching Claude session: its transcript holds a Workflow tool_use
    # whose script is byte-identical to the run's persisted script.
    (projects / "proj" / "sess.jsonl").write_text(json.dumps({
        "type": "assistant", "uuid": "pa1",
        "message": {"role": "assistant", "content": [
            {"type": "tool_use", "id": "tuWF", "name": "Workflow",
             "input": {"script": _SCRIPT_BODY}}]}}) + "\n")
    span = _parent_workflow_span("tuWF")
    ingest_session_spans([(span, span["attributes"])])

    W.ingest_run(W.discover_runs(projects)[0], deep=True, is_test=True)

    conn = get_connection()
    try:
        # parent tool span -> run + name (drives the inline "⚙ <name> · view run →")
        parent = _row_attrs(conn, "sess", "span_id='parentspan01'")
        assert (parent["workflow_run_id"], parent["workflow_name"]) == (RUN_ID, "synthetic-wf")
        # run root -> parent tool span (drives the run view's backlink)
        assert _row_attrs(conn, RUN_ID, "name='session.start'")["parent_span_id"] == "parentspan01"
    finally:
        conn.close()


# ── Script meta parsing (tree-sitter AST, not regex / not eval) ─────────────

def _write_script(tmp_path: Path, body: str) -> Path:
    p = tmp_path / "wf.js"
    p.write_text(body)
    return p


def test_parse_script_meta_and_phases_from_ast(tmp_path):
    """name/description/phases come from a real JS parse of the `meta` literal,
    so they survive syntax a regex mangles: `]`/`{` inside a detail string, a
    comment containing braces, mixed single/double quotes, and a trailing
    comma (all present in `_SCRIPT_BODY`)."""
    ref = W.discover_runs(_make_run(tmp_path, with_manifest=False))[0]
    assert W._parse_script_meta(ref.script_path) == (
        "synthetic-wf", "synthetic workflow for tests")
    assert W._parse_script_phases(ref.script_path) == [
        {"title": "Map", "detail": "scan arrays [0] and {braces}"},
        {"title": "Reduce", "detail": "merge, then done"},
    ]


def test_build_flat_spans_stamps_phase_plan(tmp_path):
    """A live run (no manifest) carries the declared phase plan on its root, so
    the rail can preview phases before completion maps agents to them."""
    ref = W.discover_runs(_make_run(tmp_path, with_manifest=False))[0]
    root = _by_name(W.build_flat_spans(ref, is_test=True))["session.start"][0]
    assert [p["title"] for p in root["attributes"]["phase_plan"]] == ["Map", "Reduce"]


def test_meta_handles_backticks_and_braces(tmp_path):
    """The AST walk handles a template (backtick) string with an escape, and
    braces/brackets inside values — cases a regex or a JSON parser would miss."""
    meta = W._load_script_meta(_write_script(tmp_path,
        "export const meta = {\n"
        "  name: `multi\\nline`,\n"
        "  phases: [{ title: 'P', detail: 'a } and ] here' }],\n"
        "}\n"))
    assert meta["name"] == "multi\nline"
    assert meta["phases"][0]["detail"] == "a } and ] here"


def test_meta_skips_comment_and_decoy_identifier(tmp_path):
    """The query's `#eq?` predicate isolates the real `meta`: a leading comment
    (with braces/brackets), a `metadata` decoy identifier, and another
    object-valued const are all ignored."""
    meta = W._load_script_meta(_write_script(tmp_path,
        "/* header: metadata, a { and a ] inside */\n"
        "const metadata = 1\n"
        "const other = { name: 'WRONG', phases: ['x'] }\n"
        "export const meta = { name: 'real', phases: [] }\n"
        "await agent('x')\n"))
    assert meta == {"name": "real", "phases": []}


def test_meta_graceful_fallback(tmp_path):
    """The loader degrades to {} for a None/missing script and for a script
    with no `meta` declaration — so a weird script never breaks ingest."""
    assert W._load_script_meta(None) == {}
    assert W._load_script_meta(tmp_path / "nope.js") == {}
    assert W._load_script_meta(_write_script(tmp_path, "const y = 1\n")) == {}
