## Delivers

新增 `lib/providers/codebuddy/`：`CodeBuddyFamily` 数据类（Model 节列出的字段）+ 共用实现 `CodeBuddyFamilyProvider`，子类 `CodeBuddyProvider`（id `codebuddy`）与 `WorkBuddyProvider`（id `workbuddy`），注册进 `lib/providers/registry.py` 的 provider 表，`list_provider_ids()` 与 `GET /api/providers` 都能看到。config_root 默认 `~/.codebuddy` / `~/.workbuddy`，`CODEBUDDY_CONFIG_DIR` / `WORKBUDDY_CONFIG_DIR` 覆盖经 `lib/settings.py` 读取（不直接读 env）。实现 `hook_settings_path()`=`<root>/settings.json`、`hook_events()`=Claude 同名同形事件、`global_skills_dir()`=`<root>/skills`、codebuddy 的 `project_skills_subpath()`=`.codebuddy/skills`、`transcript_projects_dir()`=`<root>/projects`、`find_session_transcript(sid)` 按 `projects/*/<sid>.jsonl` 查找、capabilities 的 skills/hooks/sessions/transcript_usage 全为 true。`lib/providers/base.py` 新增 `mcp_config_path()` 与 `acp_argv()`，默认返回 None（Claude/Kimi/Codex/generic 行为不变）；codebuddy-family 的 `mcp_config_path()` 返回 `<root>/.mcp.json`（codebuddy）或 `<root>/mcp.json`（workbuddy）。本片两个 provider 的 `launch_capabilities()` 都返回空 launch_modes（codebuddy 的启动在 S6 打开）。前端 `frontend/src/utils/sessionRowFormat.js` 与 `frontend/src/components/sessions/SessionAgentIcon.vue` 补 codebuddy/workbuddy 的会话标签与图标配色。测试 `tests/providers/test_codebuddy_family.py`，并断言 Claude/Kimi/Codex 的 `hook_events()`、技能目录不变

## Spec 依据

Model, Rules 接入面矩阵, Invariants

## 验收测试名

- `test_providers_registered`
- `test_workbuddy_cannot_be_launched`

## 收尾动作

合回 `f168/integration`（这是报告完成的前置条件，不是可选步骤）
