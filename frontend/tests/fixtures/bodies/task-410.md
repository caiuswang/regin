## Delivers

feature 页面（`FeatureLedgerView.vue` 或其子组件）新增 Branch 一栏：integration（可编辑，PATCH）+ runs 列表（branch、last run、worktree、exists、merged），C-R7 的 `error` 显示在栏内；`TaskArmSheet.vue`：非 slice 且有 `feature_id` 的 task，base 预填 integration name，为空则不预填；slice proposal 不变；新增 e2e `frontend/tests/feature-branch.spec.js

## Spec 依据

C-R5, C-R6, C-R7

## 验收测试名

- `test_arm_prefill`
- `test_feature_page_shows_branch_panel`
- `test_branch_panel_shows_git_error`

## 收尾动作

合回 `f143/integration`（这是报告完成的前置条件，不是可选步骤）
