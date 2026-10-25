## Delivers

lib/tasks/run_prompt.py`：A-R1，`<references>` 并入 `by_fix_item(task_id)` 的全部 defect（open/closed，按 id 升序去重，launch 时实时解析）；`lib/defects/fix_items.py` + `lib/defects/store.py`：A-R2/A-R3 批量创建（全有或全无，`external_id=defect:<最小id>`，重复提交幂等），A-R4/A-R5 在 link/unlink 后按 `auto_title` 重算标题，A-R7 改 `_is_own_task`，A-R8 复制每个 defect 的截图并重编号；`POST /api/defects/fix-task`（200 / 409 + blocking）；测试放在 `tests/defects/test_multi_defect_fix_task.py

## Spec 依据

A-R1, A-R2, A-R3, A-R4, A-R5, A-R6, A-R7, A-R8, S1, S4

## 验收测试名

- `test_every_linked_defect_reaches_the_run_s_references`
- `test_one_task_from_three_defects`
- `test_batch_is_all_or_none`
- `test_linking_a_second_defect_retitles_an_auto_titled_task`
- `test_create_on_a_defect_already_on_a_defect_sourced_task`
- `test_batch_copies_every_defect_image`

## 收尾动作

合回 `f143/integration`（这是报告完成的前置条件，不是可选步骤）
