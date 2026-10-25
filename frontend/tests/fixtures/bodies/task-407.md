## Delivers

lib/tasks/runs.py`：B-R2，`_settle_and_fire` 结算为 done 而 `_open_fix_defects(task_id)` 非空时改记 failed，`exit_reason` 写明 `ended with defect #… still open`，依赖不放行；B-R4/I-B2：defect 视图（列表与 feature 卡片的 API）带 `fix_drift`（defect open ∧ fix task done/archived），并提供与 I-B2 查询一致的计数；B-R1/B-R3/B-R5 保持现状；测试放在 `tests/defects/test_done_consistency.py

## Spec 依据

B-R1, B-R2, B-R3, B-R4, B-R5, I-B1, I-B2, S2, S3

## 验收测试名

- `test_clean_exit_with_an_open_fix_defect`
- `test_clean_exit_after_the_defect_is_closed`
- `test_drift_is_visible`
- `test_stop_idle_run_with_open_defect_settles_failed`

## 收尾动作

合回 `f143/integration`（这是报告完成的前置条件，不是可选步骤）
