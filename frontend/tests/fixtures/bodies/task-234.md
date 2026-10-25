## Delivers

empty columns → 48px rails, full columns share width with a card grid; "Queue is clear" banner; `done_week` on /api/tasks/board

## Spec 依据

Surface map (1g / 1h), Shape (BoardColumnView, QueueClearBanner), Rules (Board column layout), I1, I9

## 验收测试名

- `test_task_board_done_week`
- `test_tasks_board_sparse_layout`

## 收尾动作

合回 `f110/integration`（这是报告完成的前置条件，不是可选步骤）
