## Delivers

GET /api/features/<id>/next-step`。`TasksScopeBanner.vue` 改为带主按钮的头部（R1 表），主按钮动作复用 `useTaskVerbs` 与 `SliceArmSheet` 的打开方式。`TasksView.vue` 中的 New task、`c` 快捷键与空状态在作用域下预填 `feature_id`。新增 e2e `frontend/tests/f155-scoped-tasks.spec.js`，后端测试放在 `tests/features/test_next_step_endpoint.py

## Spec 依据

R1

## 验收测试名

- `test_scoped_tasks_head_shows_next_step_button`
- `test_scope_head_action_follows_decision_table`
- `test_scoped_new_task_prefills_feature`
- `test_feature_next_step_endpoint`

## 收尾动作

合回 `f155/integration`（这是报告完成的前置条件，不是可选步骤）
