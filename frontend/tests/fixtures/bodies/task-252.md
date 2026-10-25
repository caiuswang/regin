## Delivers

B1–B8: declare picker id, unscoped triage queue with "all projects" header, merge → keeper, re-arm prefill, inline unattribute refusal, tracked spec read-only, gate copy, dead card deleted; never restarts :8321 (live check is S10)

## Spec 依据

R1, D1, D4, I6

## 验收测试名

- `test_declare_feature_dialog_attributes_chosen_row`
- `test_triage_queue_ignores_project_scope`
- `test_defect_merge_lands_on_keeper`
- `test_task_detail_rearm_prefills_pending_params`
- `test_unattribute_refusal_stays_inline`
- `test_tracked_feature_spec_is_read_only`

## 收尾动作

合回 `f119/integration`（这是报告完成的前置条件，不是可选步骤）
