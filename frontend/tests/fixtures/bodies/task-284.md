## Delivers

V1–V4, V6 and L1–L6: canWrite threaded to the flat/grouped list; runs route viewer-readable; Starting… state; Arm busy; blocked-by → detail; mobile Group/Sort; date follows sort; selection trim + Esc + select-all; single Archive; board Failed sub-group; bulk rejected named

## Spec 依据

R4 (V1–V4, V6), R5, D6, I4

## 验收测试名

- `test_task_list_viewer_sees_no_write_controls`
- `test_task_runs_readable_by_viewer`
- `test_primary_action_starting_state`
- `test_blocked_by_link_opens_task`
- `test_tasks_mobile_group_and_sort_menus`
- `test_flat_row_date_follows_sort`
- `test_next_step_selection_trimmed_on_refresh`
- `test_task_detail_archive_once`
- `test_board_failed_subgroup`
- `test_flat_bulk_names_rejected`

## 收尾动作

合回 `f125/integration`（这是报告完成的前置条件，不是可选步骤）
