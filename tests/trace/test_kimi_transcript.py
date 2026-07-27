"""Tests for the Kimi wire.jsonl transcript parser."""

from __future__ import annotations

import base64
import hashlib
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
           "name": "Bash", "args": {"command": "echo hi"}}, time=1_100),
    _loop({"type": "tool.result", "toolCallId": "call-1",
           "result": {"output": "hi\n"}}, time=1_450),
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


def test_tool_duration_from_call_result_record_times(tmp_path: Path):
    (call,) = _parsed(tmp_path).turns[0].tool_calls
    assert call["duration_ms"] == 350


def test_tool_duration_absent_when_result_never_lands(tmp_path: Path):
    recs = [
        {"type": "turn.prompt", "input": [{"type": "text", "text": "x"}], "time": 1},
        _loop({"type": "step.begin", "uuid": "s1"}),
        _loop({"type": "tool.call", "stepUuid": "s1", "toolCallId": "c1",
               "name": "Bash", "args": {"command": "sleep 9"}}, time=1_000),
        _loop({"type": "step.end", "uuid": "s1", "usage": {"output": 1}}, time=2_000),
    ]
    u = read_usage_kimi(_wire(tmp_path / "w.jsonl", recs))
    assert "duration_ms" not in u.turns[0].tool_calls[0]


def test_prompt_ids_minted_and_stamped_on_tool_calls(tmp_path: Path):
    u = _parsed(tmp_path)
    # The anchor uuid doubles as the join value both ladder rungs use.
    assert u.prompt_ids == {"kprompt-0": "kprompt-0"}
    assert u.turns[0].tool_calls[0]["source_prompt_id"] == "kprompt-0"


def test_prompt_ids_track_the_prompt_in_flight(tmp_path: Path):
    recs = [
        {"type": "turn.prompt", "input": [{"type": "text", "text": "one"}], "time": 1},
        _loop({"type": "step.begin", "uuid": "s1"}),
        _loop({"type": "tool.call", "stepUuid": "s1", "toolCallId": "c1",
               "name": "Bash", "args": {"command": "ls"}}, time=2),
        _loop({"type": "step.end", "uuid": "s1", "usage": {"output": 1}}),
        {"type": "turn.prompt", "input": [{"type": "text", "text": "two"}], "time": 9},
        _loop({"type": "step.begin", "uuid": "s2"}),
        _loop({"type": "tool.call", "stepUuid": "s2", "toolCallId": "c2",
               "name": "Bash", "args": {"command": "pwd"}}, time=10),
        _loop({"type": "step.end", "uuid": "s2", "usage": {"output": 1}}),
    ]
    u = read_usage_kimi(_wire(tmp_path / "w.jsonl", recs))
    assert sorted(u.prompt_ids) == ["kprompt-0", "kprompt-1"]
    assert [t.tool_calls[0]["source_prompt_id"] for t in u.turns] == [
        "kprompt-0", "kprompt-1",
    ]


def _reminder(text: str, time: int) -> dict:
    return {"type": "context.append_message", "time": time, "message": {
        "role": "user",
        "content": [{"type": "text", "text": text}],
        "origin": {"kind": "injection", "variant": "todo_list_reminder"},
    }}


def test_system_reminder_becomes_task_reminder_attachment(tmp_path: Path):
    recs = [
        {"type": "turn.prompt", "input": [{"type": "text", "text": "x"}], "time": 1},
        _reminder("<system-reminder>\nUpdate the todo list.\n</system-reminder>", 2),
        # A plain user echo of the prompt is not a reminder and is skipped.
        {"type": "context.append_message", "time": 3, "message": {
            "role": "user", "content": [{"type": "text", "text": "x"}],
            "origin": {"kind": "user"}}},
        _loop({"type": "step.begin", "uuid": "s1"}),
        _loop({"type": "step.end", "uuid": "s1", "usage": {"output": 1}}),
    ]
    u = read_usage_kimi(_wire(tmp_path / "w.jsonl", recs))
    (att,) = u.attachments
    assert att.kind == "task_reminder"
    assert "Update the todo list." in att.payload["content"]
    assert att.parent_uuid == "kprompt-0"
    assert att.timestamp is not None


def test_tools_snapshot_diffs_against_active_tools_ignoring_globs(tmp_path: Path):
    recs = [
        {"type": "tools.set_active_tools",
         "names": ["Read", "Bash", "mcp__*"], "time": 1},
        {"type": "llm.tools_snapshot", "time": 2,
         "tools": [{"name": "Read"}, {"name": "Bash"}]},
        {"type": "tools.set_active_tools",
         "names": ["Read", "Grep"], "time": 3},
        {"type": "turn.prompt", "input": [{"type": "text", "text": "x"}], "time": 4},
        _loop({"type": "step.begin", "uuid": "s1"}),
        _loop({"type": "step.end", "uuid": "s1", "usage": {"output": 1}}),
    ]
    u = read_usage_kimi(_wire(tmp_path / "w.jsonl", recs))
    kinds = [a.kind for a in u.attachments]
    # The snapshot re-states the same surface (the `mcp__*` selector is not a
    # tool), so it must not manufacture a second delta.
    assert kinds == ["deferred_tools_delta", "deferred_tools_delta"]
    assert u.attachments[0].payload == {
        "added_names": ["Read", "Bash"], "removed_names": [],
    }
    assert u.attachments[1].payload == {
        "added_names": ["Grep"], "removed_names": ["Bash"],
    }


def test_user_steer_becomes_queued_prompt_and_background_steer_does_not(tmp_path: Path):
    recs = [
        {"type": "turn.prompt", "input": [{"type": "text", "text": "go"}], "time": 1},
        _loop({"type": "step.begin", "uuid": "s1"}),
        {"type": "turn.steer", "time": 2, "origin": {"kind": "user"},
         "input": [{"type": "text", "text": "actually, use ripgrep"}]},
        {"type": "turn.steer", "time": 3,
         "origin": {"kind": "background_task", "taskId": "bash-1"},
         "input": [{"type": "text", "text": "<notification>done</notification>"}]},
        _loop({"type": "step.end", "uuid": "s1", "usage": {"output": 1}}),
    ]
    u = read_usage_kimi(_wire(tmp_path / "w.jsonl", recs))
    (att,) = u.attachments
    assert att.kind == "queued_command"
    assert att.payload == {
        "command_mode": "prompt", "prompt": "actually, use ripgrep",
    }


def test_steps_after_a_user_steer_anchor_to_it(tmp_path: Path):
    # A steer ends the current turn: steps beginning after it are the steer's
    # response and must anchor to the steer (`prompt-katt-N`), not the
    # interrupted prompt — otherwise the trace UI groups the whole steered
    # turn under the previous prompt (CAI-14).
    recs = [
        {"type": "turn.prompt", "input": [{"type": "text", "text": "go"}], "time": 1},
        _loop({"type": "step.begin", "uuid": "s1"}),
        _loop({"type": "step.end", "uuid": "s1", "usage": {"output": 1}}),
        {"type": "turn.steer", "time": 2, "origin": {"kind": "user"},
         "input": [{"type": "text", "text": "actually, use ripgrep"}]},
        _loop({"type": "step.begin", "uuid": "s2"}),
        _loop({"type": "tool.call", "stepUuid": "s2", "toolCallId": "c2",
               "name": "Grep", "args": {"pattern": "x"}}, time=3),
        _loop({"type": "step.end", "uuid": "s2", "usage": {"output": 1}}),
        # A real prompt after the steer re-anchors the steps that follow it.
        {"type": "turn.prompt", "input": [{"type": "text", "text": "next"}], "time": 4},
        _loop({"type": "step.begin", "uuid": "s3"}),
        _loop({"type": "step.end", "uuid": "s3", "usage": {"output": 1}}),
    ]
    u = read_usage_kimi(_wire(tmp_path / "w.jsonl", recs))
    assert [t.prompt_uuid for t in u.turns] == [
        "kprompt-0", "katt-0", "kprompt-1",
    ]
    (call,) = u.turns[1].tool_calls
    assert call["source_prompt_id"] == "katt-0"
    # The interrupted step keeps its original anchor.
    assert u.turns[0].prompt_uuid == "kprompt-0"


def test_turn_cancel_flags_only_in_flight_tool_calls(tmp_path: Path):
    recs = [
        {"type": "turn.prompt", "input": [{"type": "text", "text": "x"}], "time": 1},
        _loop({"type": "step.begin", "uuid": "s1"}),
        _loop({"type": "tool.call", "stepUuid": "s1", "toolCallId": "done",
               "name": "Bash", "args": {"command": "ls"}}, time=2),
        _loop({"type": "tool.result", "toolCallId": "done",
               "result": {"output": "ok"}}, time=5),
        _loop({"type": "tool.call", "stepUuid": "s1", "toolCallId": "flight",
               "name": "Bash", "args": {"command": "sleep 99"}}, time=6),
        {"type": "turn.cancel", "time": 7},
        _loop({"type": "step.end", "uuid": "s1", "usage": {"output": 1}}),
    ]
    u = read_usage_kimi(_wire(tmp_path / "w.jsonl", recs))
    flags = {c["id"]: c.get("interrupted") for c in u.turns[0].tool_calls}
    assert flags == {"done": None, "flight": True}


def test_late_result_clears_the_interrupt_flag(tmp_path: Path):
    recs = [
        {"type": "turn.prompt", "input": [{"type": "text", "text": "x"}], "time": 1},
        _loop({"type": "step.begin", "uuid": "s1"}),
        _loop({"type": "tool.call", "stepUuid": "s1", "toolCallId": "c1",
               "name": "Bash", "args": {"command": "sleep 1"}}, time=2),
        {"type": "turn.cancel", "time": 3},
        _loop({"type": "tool.result", "toolCallId": "c1",
               "result": {"output": "ok"}}, time=9),
        _loop({"type": "step.end", "uuid": "s1", "usage": {"output": 1}}),
    ]
    u = read_usage_kimi(_wire(tmp_path / "w.jsonl", recs))
    (call,) = u.turns[0].tool_calls
    assert "interrupted" not in call
    assert call["duration_ms"] == 7


_BLURB = (
    '<system>Image compressed to fit model limits: original 2848x196 image/png '
    '(162 KB) -> sent 2000x138 image/png (97 KB).</system>'
)


def _image_wire(tmp_path: Path, digest: str | None, blob: bytes | None) -> str:
    main = tmp_path / "main"
    (main / "blobs").mkdir(parents=True)
    if digest and blob is not None:
        (main / "blobs" / digest).write_bytes(blob)
    recs = [
        {"type": "turn.prompt", "time": 1, "origin": {"kind": "user"}, "input": [
            {"type": "text", "text": _BLURB},
            {"type": "image_url",
             "imageUrl": {"url": f"blobref:image/png;{digest}"}},
            {"type": "text", "text": " fix this"},
        ]},
        _loop({"type": "step.begin", "uuid": "s1"}),
        _loop({"type": "step.end", "uuid": "s1", "usage": {"output": 1}}),
    ]
    return _wire(main / "wire.jsonl", recs)


def test_prompt_image_blobref_resolved_and_blurb_stripped(tmp_path: Path):
    blob = b"\x89PNG\r\n\x1a\n" + b"pixels"
    digest = hashlib.sha256(blob).hexdigest()
    u = read_usage_kimi(_image_wire(tmp_path, digest, blob))
    assert u.prompt_texts == {"kprompt-0": "fix this"}
    assert u.prompt_image_parts == {"kprompt-0": [{
        "idx": 1,
        "media_type": "image/png",
        "data_b64": base64.b64encode(blob).decode("ascii"),
    }]}


def test_missing_blob_keeps_the_prompt_text(tmp_path: Path):
    u = read_usage_kimi(_image_wire(tmp_path, "deadbeef" * 8, None))
    assert u.prompt_texts == {"kprompt-0": "fix this"}
    assert u.prompt_image_parts == {}


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
