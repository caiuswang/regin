## Delivers

R5a：`lib/tasks/next_step.py` 加 `done` 组（排在最后，`coalesce(latest_run.ended_at, updated_at) DESC, id DESC`，预览 5 条，`only=done` 分页），删 `done_hidden`，`open` 只算前五组（I1、I2）；`NEXT_STEP_GROUPS` 加 `done`，`?group=done` 合法，标题行改为 "N open · M done"；R5b：`lib/pipeline/overview.py` 加 `recently_done = {week, rows≤5}`，遵守 project scope；`RunNextPanel` 底部 "Done recently · <week> this week" 块与 "All done →" 链接（带 scope），无行时不画（D11）；后端测试扩 `tests/tasks/test_next_step.py`、`tests/pipeline/test_overview.py`，前端 e2e `frontend/tests/f144-done-groups.spec.js

## Spec 依据

R5a, R5b, D9, D10, D11, I1, I2

## 验收测试名

- `test_done_group_is_last_and_newest_first`
- `test_open_excludes_done_and_done_hidden_removed`
- `test_every_unarchived_task_in_exactly_one_group`
- `test_recently_done_honours_project_scope_and_cap`
- `Next step lists done tasks last`
- `Group address for done`
- `Overview shows recently done tasks`
- `Overview with nothing done hides the block`

## 收尾动作

合回 `f144/integration`（这是报告完成的前置条件，不是可选步骤）
