## Delivers

frontend/src/components/features/FeatureDefects.vue` + `frontend/src/components/defects/defectForms.js` + `DefectActions.vue`：open 且未 link 的行显示复选框，选中 ≥1 时出现 `Create one fix task (N)` 调 `POST /api/defects/fix-task`，409 时就地显示 blocking；`Link existing task` 改为下拉（本 feature 中非 done/archived 的 task，保留输入 id）；drift 行显示 `fix done · still open` 徽标与 `Reopen fix task` / `Create fix task`；全局 `/defects` 不加复选框；新增 e2e `frontend/tests/feature-defects-batch.spec.js

## Spec 依据

A UI, B-R4

## 验收测试名

- `test_feature_defects_batch_create_one_fix_task`
- `test_link_existing_task_offers_dropdown`
- `test_drift_row_shows_badge_and_reopen`
- `test_global_defects_list_has_no_checkboxes`

## 收尾动作

合回 `f143/integration`（这是报告完成的前置条件，不是可选步骤）
