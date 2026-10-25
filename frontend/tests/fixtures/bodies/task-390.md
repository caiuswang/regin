## Delivers

AgentProvider.launch_capabilities()`（claude 声明 sdk+tmux 与三个字段；kimi 先只声明 sdk 与 model，tmux 由 S5 补上；codex / generic 返回空）；arm 按能力校验 provider、launch、字段（D1–D3），去掉 `arm.py` 里 "launch modes are Claude-only" 的字面量；`runs._channel_launcher` 对无通道的非 claude provider 抛 RunRefused；`/api/agent-runs/launch-options` 增加 `providers` 字段；doctor 增加 "task launch providers" 组（which + `--version`）；测试放在 `tests/tasks/test_launch_capabilities.py`，并扩展 `tests/tasks/test_arm_provider.py

## Spec 依据

D1, D2, D3, Invariant 1, Invariant 4, 形状

## 验收测试名

- `test_provider_declares_launch_capabilities`
- `test_arm_rejects_provider_without_launch_channel`
- `test_arm_rejects_launch_mode_provider_does_not_declare`
- `test_arm_rejects_field_provider_does_not_declare`
- `test_launch_options_lists_providers_with_availability`
- `test_doctor_reports_task_launch_providers`
- `test_claude_arm_unchanged`

## 收尾动作

合回 `f140/integration`（这是报告完成的前置条件，不是可选步骤）
