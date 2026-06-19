"""Tests for the Kimi wire.jsonl transcript parser."""

from __future__ import annotations

import json
from pathlib import Path

from lib.trace.kimi_transcript import read_usage_kimi


def _wire(path: Path, records: list[dict]) -> str:
    path.write_text("\n".join(json.dumps(r) for r in records))
    return str(path)


def _loop(event: dict, time: int = 0) -> dict:
    return {"type": "context.append_loop_event", "event": event, "time": time}


# A two-step turn: step 1 thinks + calls a tool, step 2 answers in text.
_RECORDS = [
    {"type": "metadata", "protocol_version": "1.4"},
    {"type": "turn.prompt",
     "input": [{"type": "text", "text": "echo hi please"}],
     "origin": {"kind": "user"}, "time": 1_000},
    _loop({"type": "step.begin", "uuid": "step-1", "turnId": "0", "step": 1}),
    _loop({"type": "content.part", "stepUuid": "step-1",
           "part": {"type": "think", "think": "I should run echo."}}),
    _loop({"type": "tool.call", "stepUuid": "step-1", "toolCallId": "call-1",
           "name": "Bash", "args": {"command": "echo hi"}}),
    _loop({"type": "tool.result", "toolCallId": "call-1",
           "result": {"output": "hi\n"}}),
    _loop({"type": "step.end", "uuid": "step-1", "step": 1,
           "usage": {"inputOther": 3000, "output": 40,
                     "inputCacheRead": 10000, "inputCacheCreation": 5},
           "llmStreamDurationMs": 1200}, time=2_000),
    _loop({"type": "step.begin", "uuid": "step-2", "turnId": "0", "step": 2}),
    _loop({"type": "content.part", "stepUuid": "step-2",
           "part": {"type": "text", "text": "It printed hi."}}),
    _loop({"type": "step.end", "uuid": "step-2", "step": 2,
           "usage": {"inputOther": 100, "output": 20,
                     "inputCacheRead": 13000, "inputCacheCreation": 0}}, time=3_000),
    {"type": "usage.record", "model": "kimi-code/kimi-for-coding",
     "usage": {"inputOther": 3100, "output": 60}, "usageScope": "turn", "time": 3_000},
]


def _parsed(tmp_path: Path):
    u = read_usage_kimi(_wire(tmp_path / "wire.jsonl", _RECORDS))
    assert u is not None
    return u


def test_parses_prompt_and_model(tmp_path: Path):
    u = _parsed(tmp_path)
    assert u.model == "kimi-code/kimi-for-coding"
    assert u.prompt_texts == {"kprompt-0": "echo hi please"}
    assert "kprompt-0" in u.prompt_timestamps


def test_steps_become_turns_anchored_to_prompt(tmp_path: Path):
    u = _parsed(tmp_path)
    assert [t.uuid for t in u.turns] == ["step-1", "step-2"]
    assert [t.prompt_uuid for t in u.turns] == ["kprompt-0", "kprompt-0"]


def test_token_mapping_per_step(tmp_path: Path):
    t0 = _parsed(tmp_path).turns[0]
    assert (t0.input_tokens, t0.output_tokens) == (3000, 40)
    assert (t0.cache_read_tokens, t0.cache_creation_tokens) == (10000, 5)
    assert t0.inference_duration_ms == 1200


def test_content_split_think_text(tmp_path: Path):
    t0, t1 = _parsed(tmp_path).turns
    assert t0.thinking_text == "I should run echo."
    assert t0.thinking_blocks == 1
    assert t0.text is None
    assert t1.text == "It printed hi."


def test_tool_call_shape_and_mapping(tmp_path: Path):
    u = _parsed(tmp_path)
    (call,) = u.turns[0].tool_calls
    assert call["id"] == "call-1"
    assert call["name"] == "Bash"
    assert call["is_error"] is False          # patched by the tool.result
    assert "output_token_estimate" in call
    assert u.tool_use_to_turn_uuid == {"call-1": "step-1"}


def test_aggregate_totals(tmp_path: Path):
    u = _parsed(tmp_path)
    assert u.input_tokens == 3100
    assert u.output_tokens == 60
    assert u.cache_read_tokens == 23000
    assert u.peak_context_tokens == max(t.context_used for t in u.turns)


def test_tool_error_sets_is_error(tmp_path: Path):
    recs = [
        {"type": "turn.prompt", "input": [{"type": "text", "text": "x"}], "time": 1},
        _loop({"type": "step.begin", "uuid": "s1"}),
        _loop({"type": "tool.call", "stepUuid": "s1", "toolCallId": "c1",
               "name": "Bash", "args": {"command": "false"}}),
        _loop({"type": "tool.result", "toolCallId": "c1",
               "result": {"isError": True, "output": "boom"}}),
        _loop({"type": "step.end", "uuid": "s1", "usage": {"output": 1}}),
    ]
    u = read_usage_kimi(_wire(tmp_path / "w.jsonl", recs))
    assert u.turns[0].tool_calls[0]["is_error"] is True


def test_denied_permission_recorded_approved_ignored(tmp_path: Path):
    recs = [
        {"type": "turn.prompt", "input": [{"type": "text", "text": "x"}], "time": 1},
        _loop({"type": "step.begin", "uuid": "s1"}),
        _loop({"type": "tool.call", "stepUuid": "s1", "toolCallId": "ok",
               "name": "Bash", "args": {"command": "ls"}}),
        _loop({"type": "tool.call", "stepUuid": "s1", "toolCallId": "no",
               "name": "Bash", "args": {"command": "rm -rf /"}}),
        {"type": "permission.record_approval_result", "toolCallId": "ok",
         "toolName": "Bash", "action": "Running: ls",
         "result": {"decision": "approved"}, "time": 5},
        {"type": "permission.record_approval_result", "toolCallId": "no",
         "toolName": "Bash", "action": "Running: rm -rf /",
         "result": {"decision": "rejected"}, "time": 6},
        _loop({"type": "step.end", "uuid": "s1", "usage": {"output": 1}}),
    ]
    u = read_usage_kimi(_wire(tmp_path / "w.jsonl", recs))
    # The approval is dropped; only the rejection becomes a denial record.
    assert len(u.permission_denials) == 1
    d = u.permission_denials[0]
    assert d["tool_use_id"] == "no"
    assert d["tool_name"] == "Bash"
    assert d["denial_reason"] == "Running: rm -rf /"
    # The denied call's command is carried so the trace shows what was rejected.
    assert d["tool_input"] == {"command": "rm -rf /"}


def test_no_permission_events_means_no_denials(tmp_path: Path):
    recs = [
        {"type": "turn.prompt", "input": [{"type": "text", "text": "x"}], "time": 1},
        _loop({"type": "step.begin", "uuid": "s1"}),
        _loop({"type": "step.end", "uuid": "s1", "usage": {"output": 1}}),
    ]
    assert read_usage_kimi(_wire(tmp_path / "w.jsonl", recs)).permission_denials == ()


def test_empty_or_missing_returns_none(tmp_path: Path):
    assert read_usage_kimi(str(tmp_path / "nope.jsonl")) is None
    assert read_usage_kimi(_wire(tmp_path / "empty.jsonl", [])) is None


def test_max_text_bytes_truncates(tmp_path: Path):
    recs = [
        {"type": "turn.prompt", "input": [{"type": "text", "text": "p"}], "time": 1},
        _loop({"type": "step.begin", "uuid": "s1"}),
        _loop({"type": "content.part", "stepUuid": "s1",
               "part": {"type": "text", "text": "x" * 5000}}),
        _loop({"type": "step.end", "uuid": "s1", "usage": {"output": 1}}),
    ]
    u = read_usage_kimi(_wire(tmp_path / "w.jsonl", recs), max_text_bytes=100)
    text = u.turns[0].text
    marker = "\n\n…[truncated]"
    assert u.turns[0].text_truncated is True
    # Shared _truncate_utf8 cuts at the byte cap and appends the marker, so the
    # captured body (minus marker) is bounded and the marker is present.
    assert text.endswith(marker)
    assert len(text[: -len(marker)].encode("utf-8")) <= 100
