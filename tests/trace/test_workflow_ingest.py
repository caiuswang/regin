"""Tests for dynamic-workflow run capture (`lib.trace.workflow_ingest`).

Fixtures are fully synthetic and built under ``tmp_path`` — no hardcoded
local run ids, no dependence on any real ~/.claude artifacts. The autouse
``tmp_db`` fixture (tests/conftest.py) points the ingest connection at a
fresh schema'd DB per test, so the DB-touching tests are isolated.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

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

# A script WITH explicit `agent()` calls (unlike _SCRIPT_BODY, which has none)
# so the live confident-mapping path is exercised. Two agents aligned with
# _JOURNAL's two keys (v2:x→aAAA first, v2:y→aBBB second) and _MANIFEST's
# Map/Reduce agents. The 2nd agent omits `phase`, inheriting the preceding
# `phase('Reduce')` — exercising the phase-inheritance path.
_SCRIPT_WITH_AGENTS = (
    "export const meta = {\n"
    "  name: 'synthetic-wf',\n"
    "  description: 'synthetic workflow for tests',\n"
    "  phases: [\n"
    "    { title: 'Map', detail: 'd1' },\n"
    "    { title: 'Reduce', detail: 'd2' },\n"
    "  ],\n"
    "}\n"
    "phase('Map')\n"
    "const one = agent('do map', { label: 'a:one', phase: 'Map' })\n"
    "phase('Reduce')\n"
    "const two = agent('do reduce', { label: 'a:two' })\n"
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


def _make_run(tmp_path: Path, *, with_manifest: bool,
              script_body: str = _SCRIPT_BODY, journal: str = _JOURNAL,
              agent_jsonl: str = _AGENT_JSONL) -> Path:
    """Build a synthetic run tree; return the projects-root to scan.

    ``script_body`` / ``journal`` override the defaults so the live
    confident-mapping path (which reads `agent()` opts from the script and keys
    off journal `started` events) can be exercised. ``agent_jsonl`` overrides
    the per-agent transcript (e.g. to exercise the encrypted-thinking split).
    """
    projects = tmp_path / "projects"
    sess = projects / "proj" / "sess"
    agents = sess / "subagents" / "workflows" / RUN_ID
    agents.mkdir(parents=True)
    (agents / "journal.jsonl").write_text(journal)
    for aid in ("aAAA", "aBBB"):
        (agents / f"agent-{aid}.jsonl").write_text(agent_jsonl)
        (agents / f"agent-{aid}.meta.json").write_text(
            json.dumps({"agentType": "Explore"}))
    scripts = sess / "workflows" / "scripts"
    scripts.mkdir(parents=True)
    (scripts / f"synthetic-{RUN_ID}.js").write_text(script_body)
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
    # agent_type is now vendor-only: the Workflow tool is a Claude Code
    # feature, so the run-root span's vendor is always 'claude'. The
    # workflow-ness lives on the orthogonal `run_id` marker (which the
    # frontend uses to detect a workflow-root session.start) and on the
    # session row's `origin` axis.
    assert root["attributes"]["agent_type"] == "claude"
    assert root["attributes"]["run_id"] == RUN_ID
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
    # the live path stores the FULL result (not just a preview), matching the
    # completion path, so the RESULT card can offer "Show full" instead of being
    # permanently trimmed at the preview cap.
    assert agents["aAAA"]["attributes"]["result_full"] == '{"ok":true}'
    assert agents["aAAA"]["attributes"]["result_preview"] == '{"ok":true}'
    # an un-finished agent has no result yet — neither field is stamped.
    assert agents["aBBB"]["attributes"].get("result_full") is None
    # the dispatched prompt (the transcript's first user message) is stamped
    # live — a still-running agent shows its task prompt, not just a done one.
    assert agents["aBBB"]["attributes"]["prompt"] == "do X"
    assert agents["aAAA"]["attributes"]["prompt"] == "do X"


def test_build_flat_spans_expands_live_agent_turns(tmp_path):
    """A live run (no manifest yet) must still stream each running agent's
    per-turn / per-tool work, not just bare agent rows — otherwise the run's
    own trace view shows nothing happening while agents are mid-flight. The
    deep children reuse the full tree's `wfagent-`/`wfturn-` ids so the
    live->complete transition is idempotent."""
    projects = _make_run(tmp_path, with_manifest=False)
    ref = W.discover_runs(projects)[0]
    spans = W.build_flat_spans(ref, deep=True, is_test=True)
    by = _by_name(spans)

    # both agents' transcripts -> 2 turns each, first turn issuing one Bash
    assert len(by["assistant_response"]) == 4                  # 2 turns x 2 agents
    assert _all_have_output_tokens(by["assistant_response"])
    assert len(by["tool.Bash"]) == 2                          # one per agent
    # turn heads nest under their agent span (the live agent id == full-tree id)
    agent_ids = {s["span_id"] for s in by["subagent.start"]}
    assert {s["parent_id"] for s in by["assistant_response"]} <= agent_ids
    # opting out keeps the coarse agent-only tree
    flat = _by_name(W.build_flat_spans(ref, deep=False, is_test=True))
    assert "assistant_response" not in flat


# Encrypted-thinking agent transcript: turn 1 = text + signature-only
# thinking + a Bash tool; turn 2 = thinking ONLY (no text). The old head-span
# logic dropped turn 2 entirely (no thinking_text -> no span) and folded turn
# 1's reasoning into the response bucket.
_ENC_THINK_AGENT = "\n".join(json.dumps(e) for e in [
    {"type": "user", "uuid": "u1", "timestamp": "2026-01-01T00:00:00Z",
     "cwd": "/tmp/repo", "message": {"role": "user", "content": "do X"}},
    {"type": "assistant", "uuid": "a1", "timestamp": "2026-01-01T00:00:01Z",
     "message": {"id": "m1", "model": "claude-opus-4-8", "role": "assistant",
                 "stop_reason": "tool_use",
                 "usage": {"input_tokens": 10, "output_tokens": 500,
                           "cache_read_input_tokens": 0,
                           "cache_creation_input_tokens": 0},
                 "content": [{"type": "thinking", "thinking": "", "signature": "S" * 2000},
                             {"type": "text", "text": "answer"},
                             {"type": "tool_use", "id": "tu1", "name": "Bash",
                              "input": {"command": "ls"}}]}},
    {"type": "assistant", "uuid": "a2", "timestamp": "2026-01-01T00:00:02Z",
     "message": {"id": "m2", "model": "claude-opus-4-8", "role": "assistant",
                 "stop_reason": "end_turn",
                 "usage": {"input_tokens": 12, "output_tokens": 300,
                           "cache_read_input_tokens": 0,
                           "cache_creation_input_tokens": 0},
                 "content": [{"type": "thinking", "thinking": "", "signature": "S" * 900}]}},
]) + "\n"


def _agent_heads(spans, agent_id):
    """(assistant_response list, {turn_uuid: assistant.thinking}) for one agent."""
    by = _by_name([s for s in spans if s["attributes"].get("agent_id") == agent_id])
    thinking = {s["attributes"]["turn_uuid"]: s for s in by.get("assistant.thinking", [])}
    return by.get("assistant_response", []), thinking


def test_workflow_encrypted_thinking_splits_into_thinking_span(tmp_path):
    """Workflow parity for the encrypted-thinking split: a text+thinking turn
    emits BOTH a response (text estimate) and an assistant.thinking span
    (output - text); a thinking-only turn emits an assistant.thinking span
    where the old code emitted nothing (losing the tokens)."""
    from lib.tokens.token_estimator import estimate_text_tokens
    projects = _make_run(tmp_path, with_manifest=False, agent_jsonl=_ENC_THINK_AGENT)
    ref = W.discover_runs(projects)[0]
    resp, thinking = _agent_heads(W.build_flat_spans(ref, deep=True, is_test=True), "aAAA")
    text_out = estimate_text_tokens("answer")
    assert len(resp) == 1
    assert resp[0]["attributes"]["output_tokens"] == text_out         # text only
    assert len(thinking) == 2                                         # a1 (w/ text) + a2 (thinking-only)
    assert thinking["a1"]["attributes"]["output_tokens"] == 500 - text_out
    assert thinking["a2"]["attributes"]["output_tokens"] == 300       # was 0 spans before
    # invariant: response + thinking accounts for the whole turn output
    assert resp[0]["attributes"]["output_tokens"] + thinking["a1"]["attributes"]["output_tokens"] == 500
    # thinking card sorts before its response (1 ms stagger)
    assert thinking["a1"]["start_time"] < resp[0]["start_time"]


def test_live_state_mtime_tracks_agent_transcripts(tmp_path):
    """The watcher's re-ingest gate must follow agent transcripts, not just the
    journal: transcripts grow as agents stream output *between* the journal's
    start/result events, so gating on the journal alone freezes the live view
    mid-run."""
    import os

    projects = _make_run(tmp_path, with_manifest=False)
    ref = W.discover_runs(projects)[0]
    # Age the journal + every transcript, then "grow" one (fixed epochs — no
    # clock). state_mtime must follow the freshest transcript, not the journal.
    os.utime(ref.journal_path, (1_780_000_000, 1_780_000_000))
    for p in ref.agents_dir.glob("agent-*.jsonl"):
        os.utime(p, (1_780_000_000, 1_780_000_000))
    os.utime(ref.agents_dir / "agent-aAAA.jsonl", (1_780_000_500, 1_780_000_500))
    assert ref.state_mtime() == 1_780_000_500                 # not the journal's


def test_manifest_existing_renders_from_manifest(tmp_path):
    """A run renders from its manifest whenever one exists (the runtime writes
    it at pause and completion); the journal-only flat tree is used only when
    there's no manifest yet (never paused)."""
    projects = _make_run(tmp_path, with_manifest=False)
    assert W.discover_runs(projects)[0].terminal is False      # no manifest -> flat
    mf = projects / "proj" / "sess" / "workflows" / f"{RUN_ID}.json"
    mf.write_text(json.dumps({**_MANIFEST, "status": "killed"}))
    assert W.discover_runs(projects)[0].terminal is True       # paused snapshot -> manifest


def test_snapshot_stale_since_flags_resumed_run(tmp_path):
    """A non-completed manifest the run has progressed past (a resume started an
    agent it doesn't list) is a *stale* snapshot → `snapshot_stale_since`
    returns its mtime so the UI can flag it. A covering snapshot (just paused)
    or a completed run returns None."""
    import os

    projects = _make_run(tmp_path, with_manifest=False)
    mf = projects / "proj" / "sess" / "workflows" / f"{RUN_ID}.json"
    # Killed snapshot covering both journal agents (aAAA, aBBB) → current.
    mf.write_text(json.dumps({**_MANIFEST, "status": "killed"}))
    assert W.discover_runs(projects)[0].snapshot_stale_since() is None

    # Resume: the journal starts an agent the snapshot doesn't know → stale.
    ref = W.discover_runs(projects)[0]
    ref.journal_path.write_text(_JOURNAL + json.dumps(
        {"type": "started", "key": "v2:z", "agentId": "aZZZ"}) + "\n")
    os.utime(mf, (1_780_000_000, 1_780_000_000))
    assert W.discover_runs(projects)[0].snapshot_stale_since() == 1_780_000_000

    # A completed snapshot is final, never stale (even with extra journal agents).
    mf.write_text(json.dumps(_MANIFEST))                       # status 'completed'
    assert W.discover_runs(projects)[0].snapshot_stale_since() is None


def test_stale_snapshot_stamped_on_root(tmp_path):
    """`build_full_spans` stamps ``snapshot_stale_at`` on the run root when given,
    so the trace header can render a 'snapshot as of …' badge."""
    ref = W.discover_runs(_make_run(tmp_path, with_manifest=True))[0]
    by = _by_name(W.build_full_spans(
        W._read_json(ref.manifest_path), ref.agents_dir, deep=False, is_test=True,
        snapshot_stale_at="2026-05-30T00:00:00+00:00"))
    assert by["session.start"][0]["attributes"]["snapshot_stale_at"] \
        == "2026-05-30T00:00:00+00:00"
    # Default (current snapshot) leaves it unset.
    by2 = _by_name(W.build_full_spans(
        W._read_json(ref.manifest_path), ref.agents_dir, deep=False, is_test=True))
    assert "snapshot_stale_at" not in by2["session.start"][0]["attributes"]


def test_manifest_wins_over_journal_iteration_overcount(tmp_path):
    """After pause→resume→pause the journal accumulates *dead* agents from the
    superseded iteration. The render must use the manifest's canonical agent
    set (current iteration), NOT the inflated journal count — otherwise a
    re-run shows e.g. 183 agents when the run really has 100."""
    # Manifest knows aAAA, aBBB. The journal also logged a dead iteration-1
    # agent (aOLD) the manifest doesn't list.
    projects = _make_run(tmp_path, with_manifest=True, journal=_JOURNAL + json.dumps(
        {"type": "started", "key": "v2:old", "agentId": "aOLD"}) + "\n")
    ref = W.discover_runs(projects)[0]
    assert ref.terminal is True
    spans = W.build_full_spans(W._read_json(ref.manifest_path), ref.agents_dir,
                               deep=False, is_test=True)
    ids = {s["attributes"]["agent_id"] for s in spans if s["name"] == "subagent.start"}
    assert ids == {"aAAA", "aBBB"}                             # manifest's 2, not journal's 3


def test_full_spans_queued_agents_get_unique_ids(tmp_path):
    """A live/paused manifest lists *queued* agents with no agentId yet; they
    must get unique span ids (by manifest index) rather than all colliding on
    ``wfagent-…-None`` and rendering as one broken row."""
    manifest = {**_MANIFEST, "workflowProgress": _MANIFEST["workflowProgress"] + [
        {"type": "workflow_agent", "index": 3, "phaseIndex": 1, "label": "q-1",
         "state": "queued"},
        {"type": "workflow_agent", "index": 4, "phaseIndex": 2, "label": "q-2",
         "state": "queued"},
    ]}
    ref = W.discover_runs(_make_run(tmp_path, with_manifest=True))[0]
    spans = W.build_full_spans(manifest, ref.agents_dir, deep=False, is_test=True)
    ss = [s["span_id"] for s in spans if s["name"] == "subagent.start"]
    assert len(ss) == len(set(ss)) == 4                        # 2 real + 2 queued


def test_running_manifest_has_no_session_end(tmp_path):
    """A live ('running') manifest renders the full phase tree but no
    session.end — the run hasn't ended, so it must not render as finished."""
    ref = W.discover_runs(_make_run(tmp_path, with_manifest=True))[0]
    by = _by_name(W.build_full_spans({**_MANIFEST, "status": "running"},
                                     ref.agents_dir, deep=False, is_test=True))
    assert "session.end" not in by
    assert len(by["workflow.phase"]) == 2                      # phases still render


def test_build_flat_spans_groups_live_agents_under_phases(tmp_path):
    """When the script's `agent()` calls carry literal label/phase, the LIVE
    path synthesizes real `workflow.phase` spans, parents each agent under its
    phase, and stamps the script label — so the rail renders like a completed
    run instead of a flat generic-`workflow-subagent` band."""
    projects = _make_run(tmp_path, with_manifest=False,
                         script_body=_SCRIPT_WITH_AGENTS)
    ref = W.discover_runs(projects)[0]
    by = _by_name(W.build_flat_spans(ref, deep=True, is_test=True))

    phases = sorted(by["workflow.phase"], key=lambda s: s["attributes"]["index"])
    assert [p["attributes"]["title"] for p in phases] == ["Map", "Reduce"]
    assert phases[0]["attributes"]["detail"] == "d1"       # detail from meta.phases
    pid = {p["span_id"]: p["attributes"]["title"] for p in phases}
    agents = {a["attributes"]["label"]: pid[a["parent_id"]]
              for a in by["subagent.start"]}
    assert agents == {"a:one": "Map", "a:two": "Reduce"}   # a:two inherited phase
    assert len(by["assistant_response"]) == 4              # deep turns still stream


def test_live_phase_layout_matches_completion(tmp_path):
    """The live confident layout's per-agent (label, phase) equals the completed
    full tree's — so the live→complete re-ingest is a stable idempotent swap."""
    projects = _make_run(tmp_path, with_manifest=True,
                         script_body=_SCRIPT_WITH_AGENTS)
    ref = W.discover_runs(projects)[0]
    manifest = W._read_json(ref.manifest_path)

    def amap(spans):
        pid = {s["span_id"]: s["attributes"]["title"]
               for s in spans if s["name"] == "workflow.phase"}
        return {s["attributes"]["agent_id"]:
                (s["attributes"].get("label"), pid.get(s["parent_id"]))
                for s in spans if s["name"] == "subagent.start"}

    live = amap(W.build_flat_spans(ref, deep=True, is_test=True))
    full = amap(W.build_full_spans(manifest, ref.agents_dir, deep=True, is_test=True))
    assert live == full == {"aAAA": ("a:one", "Map"), "aBBB": ("a:two", "Reduce")}


def test_live_layout_falls_back_on_count_mismatch(tmp_path):
    """If the journal's distinct-key count != the script's `agent()` count (not
    every declared agent has started yet), order-mapping is unsafe → fall back
    to the coarse phaseless tree."""
    one_started = json.dumps(
        {"type": "started", "key": "v2:x", "agentId": "aAAA"}) + "\n"
    projects = _make_run(tmp_path, with_manifest=False,
                         script_body=_SCRIPT_WITH_AGENTS, journal=one_started)
    ref = W.discover_runs(projects)[0]
    by = _by_name(W.build_flat_spans(ref, deep=True, is_test=True))
    assert "workflow.phase" not in by                      # fallback: no phases
    assert len(by["subagent.start"]) == 1


def test_unstarted_phase_renders_as_empty_band(tmp_path):
    """A phase declared in meta.phases that no agent targets still renders as an
    empty numbered band — the real win over the flat 'Running' list."""
    script = (
        "export const meta = { name:'w', phases:["
        "{title:'Map',detail:'d1'},{title:'Reduce',detail:'d2'},"
        "{title:'Report',detail:'d3'}] }\n"
        "phase('Map')\nagent('a', { label: 'a:one', phase: 'Map' })\n"
        "phase('Reduce')\nagent('b', { label: 'a:two', phase: 'Reduce' })\n"
    )
    projects = _make_run(tmp_path, with_manifest=False, script_body=script)
    ref = W.discover_runs(projects)[0]
    by = _by_name(W.build_flat_spans(ref, deep=True, is_test=True))
    phases = sorted(by["workflow.phase"], key=lambda s: s["attributes"]["index"])
    assert [p["attributes"]["title"] for p in phases] == ["Map", "Reduce", "Report"]
    report = phases[2]["span_id"]
    assert not [a for a in by["subagent.start"] if a["parent_id"] == report]


def test_parse_script_agents_static_and_dynamic(tmp_path):
    """`_parse_script_agents` returns ordered {label,phase} for a static script,
    tolerates a dynamic label, and returns None for dynamic dispatch (loops /
    .map) or a non-literal phase — the structural fallback signal."""
    def parse(body):
        return W._parse_script_agents(_write_script(tmp_path, body))

    assert parse(_SCRIPT_WITH_AGENTS) == [
        {"label": "a:one", "phase": "Map"},
        {"label": "a:two", "phase": "Reduce"}]
    # dynamic label (template substitution) is cosmetic → kept None, still grouped
    assert parse("phase('P')\nagent('x', { label: `a:${i}`, phase: 'P' })\n") == [
        {"label": None, "phase": "P"}]
    # dynamic phase → None (structural)
    assert parse("agent('x', { label: 'a', phase: somePhase })\n") is None
    # agent inside .map() → None (dynamic count/order)
    assert parse("phase('P')\n[1,2].map(i => agent('x', { label: 'a' }))\n") is None
    # agent inside a for-loop → None
    assert parse("phase('P')\nfor (const i of xs) { agent('x', {label:'a'}) }\n") is None
    # no agent() calls → None (falls back; this is the _SCRIPT_BODY case)
    assert parse("const x = 1\n") is None


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


def test_workflow_bill_is_priced_not_zero(tmp_path):
    """A workflow run's agents never hit the live turn-usage hook, so the run
    used to carry no turn_usage and a NULL cost — the whole 'FULL SESSION BILL'
    rendered $0 despite real token spend. Ingest now inserts a priced turn_usage
    row per agent turn and stamps the summed cost, so the bill is real."""
    from lib.trace.trace_service import fetch_tool_token_rollup

    W.ingest_run(W.discover_runs(_make_run(tmp_path, with_manifest=True))[0],
                 deep=True, is_test=True)
    conn = get_connection()
    try:
        cost = conn.execute(
            "SELECT cost_usd FROM sessions WHERE trace_id=?", (RUN_ID,)).fetchone()[0]
        # 2 agents × 2 turns each, namespaced turn_uuid so no PK collision.
        n_turns = conn.execute(
            "SELECT COUNT(*) FROM turn_usage WHERE trace_id=?", (RUN_ID,)).fetchone()[0]
    finally:
        conn.close()
    assert n_turns == 4
    assert cost and cost > 0

    _, totals = fetch_tool_token_rollup(RUN_ID)
    # The per-bucket bill split (from turn_usage) and the footer are now real.
    assert totals["output_cost_usd"] > 0
    assert totals["session_cost_usd"] == pytest.approx(cost)
    assert totals["total_spend_usd"] > 0


def test_reingest_does_not_double_count_cost(tmp_path):
    """`_clear_run` wipes turn_usage too, so re-ingesting a run replaces its
    priced turns rather than stacking a second copy (which would double the
    cost and the turn count)."""
    projects = _make_run(tmp_path, with_manifest=True)
    W.ingest_run(W.discover_runs(projects)[0], deep=True, is_test=True)
    W.ingest_run(W.discover_runs(projects)[0], deep=True, is_test=True)
    conn = get_connection()
    try:
        n_turns = conn.execute(
            "SELECT COUNT(*) FROM turn_usage WHERE trace_id=?", (RUN_ID,)).fetchone()[0]
    finally:
        conn.close()
    assert n_turns == 4


def test_terminal_ingest_sets_origin_workflow_and_vendor_claude(tmp_path):
    """A freshly ingested (terminal) run splits the two orthogonal axes:
    `agent_type` is the vendor and is always 'claude' (the Workflow tool is a
    Claude Code feature — 'workflow' is NEVER a vendor anymore), while `origin`
    records what KIND of row this is and flips to 'workflow' so the Sessions
    list can filter captured runs in/out."""
    W.ingest_run(W.discover_runs(_make_run(tmp_path, with_manifest=True))[0],
                 deep=True, is_test=True)
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT agent_type, origin FROM sessions WHERE trace_id=?",
            (RUN_ID,)).fetchone()
    finally:
        conn.close()
    assert row["agent_type"] == "claude"
    assert row["origin"] == "workflow"


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
        # Vendor is always 'claude' (Workflow is a Claude Code feature) — the
        # `origin='workflow'` marker is stamped only by the terminal pass and
        # is asserted in step 2 below.
        assert row["agent_type"] == "claude"
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


def test_build_workflow_attrs_stamps_run_id_from_result():
    """The PostToolUse builder lifts the run_id out of the Workflow tool result
    (the run dir path) so the span carries `workflow_run_id` at capture —
    before any compaction can strip the launching script."""
    from hook_manager.handlers.post_tool_trace import _build_workflow_attrs
    # Synthetic result string — the run id below is invented by this test, not
    # read from any DB or run dir, so the test is self-contained and portable.
    run_id = "wf_synthetic01-x"
    attrs: dict = {}
    result = ("Workflow launched in background. Task ID: q9\n"
              f"Transcript dir: /u/.claude/projects/p/sess/subagents/workflows/{run_id}/agents")
    _build_workflow_attrs(attrs, {"script": "..."}, result, None)
    assert attrs["workflow_run_id"] == run_id
    # No run dir in the result (e.g. a failure) → nothing stamped, no crash.
    empty: dict = {}
    _build_workflow_attrs(empty, {}, "Workflow failed to launch.", None)
    assert "workflow_run_id" not in empty


def test_stamp_parent_link_recovers_via_result_when_script_compacted(tmp_path):
    """Transcript compaction strips a Workflow call's ``input.script`` but keeps
    its tool_result. The link is recovered from the result's run-dir reference
    (``…/workflows/<run_id>``), so the run still cross-links + re-parents its
    agents instead of orphaning them."""
    from lib.trace.trace_service import ingest_session_spans
    projects = _make_run(tmp_path, with_manifest=True)
    # Compacted transcript: the Workflow tool_use has a DIFFERENT script (the
    # real one was summarised away), but the tool_result still names the run dir.
    (projects / "proj" / "sess.jsonl").write_text(
        json.dumps({
            "type": "assistant", "uuid": "pa1",
            "message": {"role": "assistant", "content": [
                {"type": "tool_use", "id": "tuWF", "name": "Workflow",
                 "input": {"script": "// unrelated / summarised script"}}]}}) + "\n"
        + json.dumps({
            "type": "user", "uuid": "pu1",
            "message": {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "tuWF",
                 "content": f"Workflow launched in background. Task ID: abc\n"
                            f"Transcript dir: /x/subagents/workflows/{RUN_ID}/agents"}]}}) + "\n")
    span = _parent_workflow_span("tuWF")
    spans = [span, _orphan_workflow_subagent("sa-a", "aAAA")]
    ingest_session_spans([(s, s["attributes"]) for s in spans])

    W.ingest_run(W.discover_runs(projects)[0], deep=True, is_test=True)

    conn = get_connection()
    try:
        # script match missed; result-based recovery still linked the call
        assert _row_attrs(conn, "sess", "span_id='parentspan01'")["workflow_run_id"] == RUN_ID
        assert _row_attrs(conn, RUN_ID, "name='session.start'")["parent_span_id"] == "parentspan01"
        # and the orphan agent got re-parented under it
        assert conn.execute(
            "SELECT parent_id FROM session_spans WHERE trace_id='sess' AND span_id='sa-a'"
        ).fetchone()["parent_id"] == "parentspan01"
    finally:
        conn.close()


def _orphan_workflow_subagent(span_id, agent_id):
    """A parent-session `subagent.start` span as Claude Code's SubagentStart
    hook records it for a workflow agent: orphan (parent_id NULL), carrying
    only agent_id/agent_type."""
    return {
        "trace_id": "sess", "span_id": span_id, "parent_id": None,
        "name": "subagent.start", "kind": "internal",
        "start_time": "2026-01-01T00:01:00Z", "end_time": "2026-01-01T00:02:00Z",
        "duration_ms": 60000, "status_code": "OK", "status_message": None,
        "attributes": {"agent_type": "workflow-subagent", "agent_id": agent_id,
                       "is_test": True},
    }


def test_ingest_reparents_session_subagents_under_tool_workflow(tmp_path):
    """A workflow's own subagents land in the LAUNCHING session as orphan
    `subagent.start` spans (hook-captured, parent_id NULL). Ingest matches
    them to the run by agent_id and re-parents them under the `tool.Workflow`
    span, so the session timeline nests + folds them natively instead of
    floating them as siblings of the tool call."""
    from lib.trace.trace_service import ingest_session_spans
    projects = _make_run(tmp_path, with_manifest=True)
    (projects / "proj" / "sess.jsonl").write_text(json.dumps({
        "type": "assistant", "uuid": "pa1",
        "message": {"role": "assistant", "content": [
            {"type": "tool_use", "id": "tuWF", "name": "Workflow",
             "input": {"script": _SCRIPT_BODY}}]}}) + "\n")
    # parent session: the tool.Workflow call + this run's two agents (aAAA,
    # aBBB per _MANIFEST) as orphans, plus an unrelated agent that must NOT move.
    spans = [
        _parent_workflow_span("tuWF"),
        _orphan_workflow_subagent("sa-a", "aAAA"),
        _orphan_workflow_subagent("sa-b", "aBBB"),
        _orphan_workflow_subagent("sa-other", "aZZZ"),
    ]
    ingest_session_spans([(s, s["attributes"]) for s in spans])

    W.ingest_run(W.discover_runs(projects)[0], deep=True, is_test=True)

    conn = get_connection()
    try:
        def _parent(span_id):
            return conn.execute(
                "SELECT parent_id FROM session_spans WHERE trace_id='sess' "
                "AND span_id=?", (span_id,)).fetchone()["parent_id"]
        # this run's agents now hang off the launching tool.Workflow span
        assert _parent("sa-a") == "parentspan01"
        assert _parent("sa-b") == "parentspan01"
        # an agent that isn't part of this run is left untouched (still orphan)
        assert _parent("sa-other") is None
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
