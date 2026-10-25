## Delivers

AgentProvider.prepare_workspace`（kimi 按 D8 预写 trust）在 channel launch 之前调用；`AgentProvider.skill_invocation`（D7，先用真机 ACP 探测 `/skill:<name>` 能否展开，结论写进本 feature spec 的 D7）；`lib/tasks/run_prompt.py` 改为通过 provider 生成 skill 段；`skill_catalog.available_skills(provider)` 与 `skill-options?provider=`；worktree 同步每个 provider 的 project dir（`.kimi-code/` 与 `.claude/` 一样处理）；测试放在 `tests/tasks/test_kimi_workspace.py

## Spec 依据

D7, D8, Invariant 1, Invariant 3

## 验收测试名

- `test_kimi_prepare_workspace_writes_trust_record`
- `test_prepare_workspace_keeps_existing_record`
- `test_channel_launch_prepares_workspace_before_session_new`
- `test_skill_invocation_is_provider_owned`
- `test_claude_skill_invocation_unchanged`
- `test_skill_catalog_lists_provider_skills`
- `test_worktree_syncs_provider_project_dir`

## 收尾动作

合回 `f140/integration`（这是报告完成的前置条件，不是可选步骤）
