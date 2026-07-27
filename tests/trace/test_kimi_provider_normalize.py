"""Kimi tool-result normalization (`KimiProvider.normalize_tool_response`).

Kimi returns every tool result as one text blob under `tool_response.output`,
so the shared `post_tool_trace` builders — which read Claude's tool-specific
keys — see nothing. These fixtures are copied verbatim out of a real
`~/.kimi-code/hook-payloads.jsonl` (session_113dcdf9-…) and pin the reshape for
the three tools whose card body is result-derived: TaskOutput (task record +
`[output]` section), Read (gutter-numbered body + `<system>` slice footer) and
Bash (merged stdout/stderr, plus the task record a backgrounded Bash returns).
"""

from __future__ import annotations

from lib.providers.registry import build_provider


_TASKOUTPUT_BLOB = (
    "retrieval_status: success\n"
    "task_id: bash-phlgzri0\n"
    "description: Re-run spend-panel specs after top-pair revert\n"
    "status: completed\n"
    "detached: true\n"
    "started_at: 1785036684094\n"
    "ended_at: 1785036696076\n"
    "timeout_ms: 300000\n"
    "kind: process\n"
    "command: cd /Users/taowang/regin/frontend && ./node_modules/.bin/playwright "
    "test spend-panel-scroll --reporter=line 2>&1 | tail -6\n"
    "pid: 53411\n"
    "exit_code: 0\n"
    "output_path: /Users/taowang/.kimi-code/sessions/wd_regin_5bbe3b09626a/"
    "session_113dcdf9-f20c-488a-a53a-619671b96ad2/agents/main/tasks/bash-phlgzri0/output.log\n"
    "output_size_bytes: 270\n"
    "output_truncated: false\n"
    "full_output_available: true\n"
    "full_output_tool: Read\n"
    "full_output_hint: The preview above is the complete output. Use the Read tool "
    "with the output_path if you need to re-read the full log later.\n"
    "\n"
    "[output]\n"
    "  1 failed\n"
    "    [chromium] > tests/spend-panel-scroll.spec.js:115:1\n"
    "  1 passed (11.7s)\n"
)

_BACKGROUND_BASH_BLOB = (
    "task_id: bash-9ecj8jbe\n"
    "pid: 49174\n"
    "description: Run header-collapse e2e test\n"
    "status: running\n"
    "automatic_notification: true\n"
    "next_step: The completion arrives automatically in a later turn.\n"
    "human_shell_hint: Tell the human to run /tasks."
)

_READ_BLOB = (
    "470\t\n"
    '471\t      <aside class="pdv-rail" aria-label="Deployment">\n'
    '472\t        <div class="pdv-rail-head">\n'
    "<system>40 lines read from file starting from line 470. "
    "Total lines in file: 833.</system>"
)


def _kimi():
    return build_provider("kimi")


def test_kimi_taskoutput_becomes_claude_task_envelope():
    """The whole background-task result is an unparsed blob; parsed, it feeds
    `_build_taskoutput_attrs` the exact keys it reads."""
    out = _kimi().normalize_tool_response(
        "TaskOutput",
        {"task_id": "bash-phlgzri0", "block": True, "timeout": 240},
        {"output": _TASKOUTPUT_BLOB},
    )
    assert out["retrieval_status"] == "success"
    task = out["task"]
    assert task["task_id"] == "bash-phlgzri0"
    assert task["status"] == "completed"
    assert task["task_type"] == "process"
    assert task["description"] == "Re-run spend-panel specs after top-pair revert"
    assert task["exit_code"] == 0
    assert task["output"].startswith("  1 failed")
    # The header lines are the task record, not program output.
    assert "retrieval_status" not in task["output"]
    # The original envelope is preserved.
    assert out["output"] == _TASKOUTPUT_BLOB


def test_kimi_taskoutput_span_carries_the_task_record():
    from hook_manager.core import HookPayload
    from hook_manager.handlers import post_tool_trace
    from lib import hook_plugin

    spans: list[dict] = []
    original = hook_plugin.post_span
    hook_plugin.post_span = lambda **kw: spans.append(kw)
    try:
        post_tool_trace._emit_span(HookPayload.from_stdin_json("PostToolUse", {
            "hook_event_name": "PostToolUse",
            "agent_type": "kimi",
            "session_id": "session_kimi_task",
            "tool_name": "TaskOutput",
            "tool_input": {"task_id": "bash-phlgzri0", "block": True},
            "tool_use_id": "tool_task_1",
            "tool_response": {"output": _TASKOUTPUT_BLOB},
        }))
    finally:
        hook_plugin.post_span = original

    attrs = spans[0]["attributes"]
    assert spans[0]["name"] == "tool.TaskOutput"
    assert attrs["task_id"] == "bash-phlgzri0"
    assert attrs["retrieval_status"] == "success"
    assert attrs["status"] == "completed"
    assert attrs["exit_code"] == 0
    assert "1 passed (11.7s)" in attrs["output"]


def test_kimi_taskoutput_without_a_task_record_is_left_alone():
    out = _kimi().normalize_tool_response(
        "TaskOutput", {"task_id": "bash-x"}, {"output": "no such task\n"})
    assert out == {"output": "no such task\n"}


def test_kimi_read_keeps_the_cat_n_gutter_and_carries_the_slice():
    """Claude's own Read content is line-numbered, so the gutter stays on
    `content` — the slice keys are additive metadata, not a replacement."""
    out = _kimi().normalize_tool_response(
        "Read",
        {"path": "frontend/src/views/PatternDetailView.vue",
         "line_offset": 470, "n_lines": 40},
        {"output": _READ_BLOB},
    )
    file_info = out["file"]
    assert file_info["content"] == (
        "470\t\n"
        '471\t      <aside class="pdv-rail" aria-label="Deployment">\n'
        '472\t        <div class="pdv-rail-head">'
    )
    assert "<system>" not in file_info["content"]
    assert file_info["start_line"] == 470
    assert file_info["num_lines"] == 40
    assert file_info["total_lines"] == 833


def test_kimi_read_falls_back_to_the_request_slice_without_a_footer():
    out = _kimi().normalize_tool_response(
        "Read", {"path": "a.py", "line_offset": 10, "n_lines": 2},
        {"output": "10\tfirst\n11\tsecond"})
    file_info = out["file"]
    assert file_info["content"] == "10\tfirst\n11\tsecond"
    assert file_info["start_line"] == 10
    assert file_info["num_lines"] == 2
    assert "total_lines" not in file_info


def test_kimi_read_leaves_unnumbered_output_untouched():
    out = _kimi().normalize_tool_response(
        "Read", {"path": "a.png"}, {"output": "binary blob"})
    assert out["file"]["content"] == "binary blob"


def test_kimi_background_bash_surfaces_its_task_id():
    out = _kimi().normalize_tool_response(
        "Bash",
        {"command": "playwright test", "run_in_background": True, "timeout": 420},
        {"output": _BACKGROUND_BASH_BLOB},
    )
    assert out["background_task_id"] == "bash-9ecj8jbe"
    assert out["stdout"] == _BACKGROUND_BASH_BLOB
    # A foreground Bash carries no task record, so no chip.
    fg = _kimi().normalize_tool_response("Bash", {"command": "ls"}, {"output": "a\nb"})
    assert "background_task_id" not in fg
    assert fg["stdout"] == "a\nb"


def test_kimi_errored_bash_output_lands_on_stderr():
    """Kimi merges stdout+stderr into one stream, so an `isError` envelope is
    the only signal that the stream is a failure — render it red."""
    out = _kimi().normalize_tool_response(
        "Bash", {"command": "ls /x"},
        {"output": "ls: /x: No such file or directory\n", "isError": True})
    assert out["stderr"] == "ls: /x: No such file or directory\n"
    assert "stdout" not in out


def test_kimi_plans_dir_resolves_a_real_session_plans_directory(tmp_path):
    from lib.providers.kimi import KimiProvider

    sessions = tmp_path / "sessions"
    plans = sessions / "wd_regin_abc" / "session_1" / "agents" / "main" / "plans"
    plans.mkdir(parents=True)
    (plans / "power-girl.md").write_text("# Plan\n", encoding="utf-8")

    p = KimiProvider({"transcript_projects_dir": str(sessions)})
    assert p.plans_dir() == plans
    assert p.session_plans_dir("session_1") == plans
    assert p.session_plans_dir("session_missing") is None
    # An explicit override still wins.
    over = KimiProvider({"transcript_projects_dir": str(sessions),
                         "plans_dir": str(tmp_path / "custom")})
    assert over.plans_dir() == tmp_path / "custom"


def test_kimi_untouched_tools_and_claude_still_pass_through():
    kimi = _kimi()
    # Edit/Write/Grep derive their card from tool_input — no reshape.
    assert kimi.normalize_tool_response("Edit", {}, {"output": "ok"}) == {"output": "ok"}
    assert kimi.normalize_tool_response("Bash", {}, {"output": ""}) == {"output": ""}
    assert kimi.normalize_tool_response("Bash", {}, {}) == {}
    # Claude payloads are identity — same object, not a copy.
    claude = build_provider("claude")
    tr = {"stdout": "out", "stderr": ""}
    assert claude.normalize_tool_response("Bash", {"command": "ls"}, tr) is tr
    read = {"file": {"content": "     1\thi", "numLines": 1, "startLine": 1}}
    assert claude.normalize_tool_response(
        "Read", {"file_path": "a.py"}, read) is read
    assert read["file"]["content"] == "     1\thi"


def test_kimi_askuserquestion_lifts_answers_out_of_the_output_blob():
    """Fixture copied from the CAI-13 payload (session_480d82c2-…): the answer
    arrives as a JSON string under `output`, and `_build_ask_attrs` reads
    top-level `answers` — so the card never showed what the user picked."""
    out = _kimi().normalize_tool_response(
        "AskUserQuestion",
        {"questions": [{"question": "Proceed?", "header": "H",
                        "options": [{"label": "Yes"}, {"label": "No"}]}]},
        {"output": '{"answers":{"Proceed?":"Yes"},"annotations":{"Proceed?":{"notes":"go"}}}'},
    )
    assert out["answers"] == {"Proceed?": "Yes"}
    assert out["annotations"] == {"Proceed?": {"notes": "go"}}
    # The original envelope is preserved alongside the lifted keys.
    assert out["output"].startswith('{"answers"')


def test_kimi_askuserquestion_non_json_output_adds_nothing():
    kimi = _kimi()
    tr = {"output": "The user dismissed the question."}
    assert kimi.normalize_tool_response("AskUserQuestion", {}, tr) == tr


def test_kimi_askuserquestion_span_carries_the_answers():
    """End-to-end: the emitted `tool.AskUserQuestion` span must carry the
    user's picks, or the session-view card shows only the questions (CAI-13)."""
    from hook_manager.core import HookPayload
    from hook_manager.handlers import post_tool_trace
    from lib import hook_plugin

    spans: list[dict] = []
    original = hook_plugin.post_span
    hook_plugin.post_span = lambda **kw: spans.append(kw)
    try:
        post_tool_trace._emit_span(HookPayload.from_stdin_json("PostToolUse", {
            "hook_event_name": "PostToolUse",
            "agent_type": "kimi",
            "session_id": "session_kimi_ask",
            "tool_name": "AskUserQuestion",
            "tool_input": {"questions": [
                {"question": "How aggressive?", "header": "Scope",
                 "options": [{"label": "Rename + consolidate"},
                             {"label": "Rename only"}]}]},
            "tool_use_id": "tool_ask_1",
            "tool_response": {"output":
                '{"answers":{"How aggressive?":"Rename + consolidate"}}'},
        }))
    finally:
        hook_plugin.post_span = original

    assert spans[0]["name"] == "tool.AskUserQuestion"
    attrs = spans[0]["attributes"]
    assert attrs["answers"] == {"How aggressive?": "Rename + consolidate"}
    assert attrs["questions"][0]["question"] == "How aggressive?"
