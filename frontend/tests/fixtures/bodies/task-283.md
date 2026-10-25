## Delivers

queue=out` defects filter; loop Open link; Run next ready count + blocked link; merge picker "more"; triage rail snoozed block from server

## Spec 依据

R3, I2, I3

## 验收测试名

- `test_defects_queue_out_filter`
- `test_overview_loop_open_link_matches_count`
- `test_run_next_count_is_ready_and_blocked_link`
- `test_merge_picker_more_excludes_self_and_shown`
- `test_triage_rail_snoozed_count_from_server`

## 收尾动作

合回 `f125/integration`（这是报告完成的前置条件，不是可选步骤）
