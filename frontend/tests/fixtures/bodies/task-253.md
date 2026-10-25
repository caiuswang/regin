## Delivers

reopen` in store/API/CLI; ship refusals in store; ship confirm listing open work; tracked-ship warning; `next_step` on detail and list rows; inline rename; unattribute confirm; Build hidden once slices exist; never restarts :8321 (live check is S11)

## Spec 依据

D3, R2, Shape (FeatureNextStep, Feature stage), I1, I4

## 验收测试名

- `test_feature_reopen_appends_restarted_and_counts_round`
- `test_feature_reopen_from_abandoned_returns_to_spec`
- `test_feature_ship_refused_when_shipped_escaped_or_abandoned`
- `test_feature_next_step_per_stage`
- `test_feature_ship_confirm_lists_open_work`

## 收尾动作

合回 `f119/integration`（这是报告完成的前置条件，不是可选步骤）
