## Delivers

TaskArmSheet.vue` 勾选框改为 "Auto close" 文案，没有记住偏好时默认勾选。`SliceArmSheet.vue` 新增同一勾选框，提交时带 `one_shot`。`GET /api/agent-runs/launch-options` 返回 workspaces，`useTaskArm.js` 把 "workspace: <name>（N repos）" 排在 repo 路径之前。cwd 是非 git 的 workspace 根时，`LaunchWorktreeFields.vue` 禁用并显示原因。测试：e2e `frontend/tests/f167-arm-sheet.spec.js`，以及 launch cwd 的 pytest

## Spec 依据

Rules B（勾选框行）, Rules C（arm 行）

## 验收测试名

- `test_slice_arm_sheet_exposes_the_auto_close_checkbox`
- `test_arm_offers_the_workspace_root_as_working_directory`
- `test_worktree_is_disabled_for_a_non_git_workspace_root`
- `test_run_in_a_workspace_root_launches_with_that_cwd`

## 收尾动作

合回 `f167/integration`（这是报告完成的前置条件，不是可选步骤）
