## Delivers

R4a matrix on list/board/graph/detail/Overview; Failed group + Retry; Stop on running rows; disarm endpoint; mark done with fix-task check; mark done releases armed dependents; `TaskRefusal` everywhere; run-all names refusals; Queued retired from surfaces; never restarts :8321 (live check is S12)

## Spec 依据

D5, R4a, Shape (TaskVerb), I2

## 验收测试名

- `test_next_step_failed_group_and_retry`
- `test_task_disarm_returns_to_idea_and_drops_pending_run`
- `test_task_mark_done_fix_task_names_open_defects`
- `test_task_mark_done_releases_armed_dependents`
- `test_task_primary_action_on_every_surface`
- `test_task_stop_on_running_rows`
- `test_run_all_names_each_refusal`
- `test_queued_not_offered`

## 收尾动作

合回 `f119/integration`（这是报告完成的前置条件，不是可选步骤）
