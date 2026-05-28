# Session Trace Regression Suite

Tmux-driven integration tests that exercise every hook event path in the
session-trace pipeline (see `ARCHITECTURE.md` + `lib/hook_plugin.py`) by
driving a real `claude` CLI and asserting what lands in `session_spans`
(via `GET /api/sessions/{trace_id}`) and in `~/.claude/hook-payloads.jsonl`.

## What it covers

Span-emitting hooks now live under `hook_manager/handlers/` and are
dispatched by `python -m hook_manager <EventName>`. The tests assert on
spans materialized via `GET /api/sessions/<trace_id>` (`web/blueprints/trace/`):

| Span name      | Emitting handler                         | Test file                |
|----------------|------------------------------------------|--------------------------|
| `prompt`       | `hook_manager/handlers/prompt_trace.py`  | `test_prompt_spans.py`   |
| `plan.decision`| `hook_manager/handlers/prompt_trace.py`  | `test_plan_mode_spans.py` (slow) |
| `skill.read`   | `hook_manager/handlers/skill_read.py`    | `test_skill_read_spans.py` |
| `plan.exit`    | `hook_manager/handlers/plan_trace.py`    | `test_plan_mode_spans.py` (slow) |
| `turn` (+ `turn_usage` rows) | `hook_manager/handlers/turn_trace/` | implicitly via prompt + multi-turn fixtures |

For tools whose generic span emitter (`post_tool_trace`) is not driving the
assertion, tests instead match on `hook-payloads.jsonl` entries with the
expected `tool_name`:

| Test file                 | Hook event validated                  |
|---------------------------|---------------------------------------|
| `test_tool_spans.py`      | PostToolUse for Read / Bash / Grep    |
| `test_subagent_spans.py`  | PostToolUse for Agent (slow)          |
| `test_mcp_tool_spans.py`  | PostToolUse for `mcp__*` tools (slow) |
| `test_notification_stop.py` | Notification, Stop                   |

Tests tagged `@pytest.mark.slow` are **skipped by default** because they
consume live Anthropic API credits (plan mode, subagent, mcp, permission).
Pass `--run-slow` to include them.

## Prerequisites

- `tmux` on `$PATH`
- `claude` on `$PATH`, already authenticated
- Flask API reachable at `http://127.0.0.1:8321/api/sessions` (the runner
  script will start one if needed)
- `pytest` in the venv (`./.venv/bin/pip install pytest`)

## Running

```bash
# Convenience wrapper — spins up the API server if not already up.
scripts/run_trace_tests.sh                 # fast tests only
scripts/run_trace_tests.sh --run-slow      # include slow scenarios
scripts/run_trace_tests.sh -k prompt       # one scenario group

# Direct pytest invocation (API must already be running)
./.venv/bin/python -m pytest tests/trace/ -v

# One test in isolation
./.venv/bin/python -m pytest tests/trace/integration/test_plan_mode_spans.py::test_plan_mode_approve_flow -v --run-slow
```

## Debugging with the demo harness

`harness.py` is executable — run it standalone to watch a scripted session
run in tmux and print the resulting span tree:

```bash
./.venv/bin/python tests/trace/integration/harness.py --demo "read sample.txt"
./.venv/bin/python tests/trace/integration/harness.py "reply ONE" "reply TWO"
```

When a test hangs, the `TraceSessionError` already includes the last 60
lines of the tmux pane so you can see whether Claude was stuck on a
permission dialog or crashed before submitting.

## Architecture notes

- `TraceSession.trace_id` is discovered from the first `UserPromptSubmit`
  entry in `~/.claude/hook-payloads.jsonl` written after `start()`, because
  hooks use `session_id` as the `trace_id`
  (see `lib/hook_plugin.py`'s `HookContext.session_id → trace_id`).
- "Claude is idle" is detected by tailing the same jsonl for a `Stop`
  entry — pane ANSI text parsing is unreliable across claude CLI versions.
- On first run in a new workdir Claude shows a "Trust this folder?"
  dialog. `_wait_for_ready_prompt` auto-dismisses it by pressing Enter
  (default = trust).
- `GET /api/sessions/{trace_id}` (`web/blueprints/trace/`) materializes
  the parent/child tree lazily, so parent-chain assertions must be made
  *after* the session has finished producing spans.

## Adding a new scenario

1. Add a new `test_*.py` in this directory using the `trace_session`
   fixture.
2. If Claude needs to see interactive keys (plan approval, slash command,
   dialog), use `trace_session.send_keys` with raw tmux keys (`"Enter"`,
   `"Escape"`, `"C-c"`, etc.).
3. Tag expensive scenarios `@pytest.mark.slow`.
4. Register the new scenario in the tables above.

## Enabling coverage for generic `tool.*` / `pre_tool.*` spans

If a generic tool-span emitter (e.g. `hook_manager.handlers.post_tool_trace`
configured to emit `tool.*` spans) is registered in
`~/.claude/settings.json`, flip `test_tool_spans.py`,
`test_mcp_tool_spans.py`, and `test_subagent_spans.py` from the
`hook_events(event="PostToolUse")` check to
`trace_session.assert_span("tool.<Name>", min_count=1)` — the harness
already supports span-level assertions; only the test bodies need an edit.
