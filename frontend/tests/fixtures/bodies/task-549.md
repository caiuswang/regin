## Delivers

新增 `lib/trace/codebuddy_transcript.py`（与 `kimi_transcript.py` 并列，codebuddy/workbuddy 共用），按 Rules「transcript 条目 → span」表映射；`span_id` 只由 `(sessionId, 条目 id)` 决定，无 `id` 的条目不产出 span；turn 键优先 `providerData.conversationRequestId`，否则用开启该 turn 的 user message `id`；`startsNewUserRequest` 缺失视为开新 turn；有 `<user_query>` 时 prompt 文本只取其内容；usage 按 `providerData.messageId` 去重，cached / reasoning 取 `inputTokensDetails[*].cached_tokens`、`outputTokensDetails[*].reasoning_tokens` 之和，写 turn_usage；未配对 function_call 产出 PENDING tool span（append-only + `lib/trace/merge.py` serve-time merge，聚合读者排除 PENDING）；`ai-title` 写会话标题；`session-meta`、`file-history-snapshot`、未知 type 跳过不报错；不读 `tool-results/` 溢出文件。codebuddy-family provider 实现 `parse_transcript` 与 `find_session_transcript`，使 `lib/trace/live_rescan.trigger_rescan` 与 hook 触发的 turn_trace 重读走到新解析器；`sessions.agent_type` 写 provider id，`trace_id` 等于 `sessionId`。CLI `regin trace import-transcripts --provider <id> [--session <sid>]`（`cli/commands/trace.py`）。fixture 放 `tests/fixtures/codebuddy/`，从本机 `~/.workbuddy/projects` 的真实形状脱敏构造；测试 `tests/trace/test_codebuddy_transcript.py

## Spec 依据

Rules transcript 条目 → span, Rules 导入的触发方式, 调研 transcript 条目形状, Invariants

## 验收测试名

- `test_workbuddy_transcript_import_builds_tool_spans`
- `test_legacy_transcript_without_startsnewuserrequest_still_splits_turns`
- `test_desktop_prompt_is_unwrapped`
- `test_transcript_import_is_idempotent`
- `test_unpaired_function_call_stays_pending`
- `test_unknown_record_type_is_skipped`

## 收尾动作

合回 `f168/integration`（这是报告完成的前置条件，不是可选步骤）
