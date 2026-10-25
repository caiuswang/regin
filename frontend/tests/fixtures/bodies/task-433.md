## Delivers

New `lib/palette/home.py` + `GET /api/palette/home`: Needs me (awaiting sessions, then defects with `triage_state=queue`, oldest wait first, cap 5 + total); Active now (working sessions, with a running task folded into its session); Active features (stage building); Sessions (latest not already shown, 4 + total). With `session=` it returns the chain instead. New `lib/palette/relations.py` + `GET /api/palette/relations/<kind>/<id>`: R5 relations, lists ≤5 with total (archived tasks excluded), body, and a read-only `waiting` block. Nothing written. Tests go in `tests/palette/test_home.py` and `test_relations.py

## Spec 依据

R1, R4, R5, Non-goals

## 验收测试名

- `test_needs_me_orders_oldest_wait_first`
- `test_running_task_folds_into_its_session`
- `test_this_session_chain`
- `test_defect_relations_name_reporter_and_fix`
- `test_feature_progress_excludes_archived`
- `test_waiting_block_is_read_only`

## 收尾动作

合回 `f146/integration`（这是报告完成的前置条件，不是可选步骤）
