## Delivers

useTaskArm` / `useArmPrefs` / `TaskArmSheet` / `SliceArmSheet`：从 launch-options 的 `providers` 渲染 provider 选择（不可用的项置灰并写明缺哪个二进制）；字段和 launch 选项按 `fields` / `launch_modes` / `labels` 显隐；body 带上 `provider`，不发 provider 未声明的字段；切换 provider 时丢弃记住的 model；skill 选择器带 `?provider=`；新增 e2e `frontend/tests/task-arm-provider.spec.js

## Spec 依据

Invariant 4, 形状

## 验收测试名

- `test_arm_sheet_provider_picker_lists_available_providers`
- `test_kimi_arm_hides_claude_only_fields`
- `test_kimi_arm_body_carries_provider_and_no_claude_fields`
- `test_slice_arm_sheet_offers_provider`
- `test_arm_prefs_drop_model_on_provider_switch`
- `test_claude_arm_sheet_unchanged`

## 收尾动作

合回 `f140/integration`（这是报告完成的前置条件，不是可选步骤）
