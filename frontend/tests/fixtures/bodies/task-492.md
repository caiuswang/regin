## Delivers

FeaturesView.vue` 默认改为 `created:desc`，补上 stage 选项；`useListQuery` 支持把 sort 记在 localStorage（URL 优先）。Tasks 与 Features 列表在创建事件（`TASK_CAPTURED_EVENT` 携带新 id，feature 同理）后滚动到新行并高亮，筛选、排序或路由变化时清除；新行不可见时 flash 显示 Show。测试：`frontend/tests/f155-list-order.spec.js`，以及断言前后端排序键一致的 pytest

## Spec 依据

R5

## 验收测试名

- `test_features_default_sort_newest`
- `test_features_sort_is_remembered`
- `test_created_task_row_is_highlighted`

## 收尾动作

合回 `f155/integration`（这是报告完成的前置条件，不是可选步骤）
