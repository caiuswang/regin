## Delivers

slices / tail / transcribe / arm-proposal / arm-slices endpoints over lib/tasks; ledger Slices panel; arm-all sheet; `FeatureActiveTasks` "not armed" part; arm-sheet prefill order; never restarts :8321 (live check is S13)

## Spec 依据

D7, R3, R4b, Shape (SlicesReport), I1

## 验收测试名

- `test_feature_slices_endpoint_reports_refusal_and_rows`
- `test_feature_slices_tail_appends_once`
- `test_feature_transcribe_endpoint_matches_cli`
- `test_task_arm_proposal_from_slice_row`
- `test_arm_slices_bulk_names_refusals_and_never_runs`
- `test_feature_slice_panel_transcribe_and_arm_all`

## 收尾动作

合回 `f119/integration`（这是报告完成的前置条件，不是可选步骤）
