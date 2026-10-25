## Delivers

"New ▾" menu with New defect dialog; scope prefill; quick capture scope + count refresh; action toasts; every loop fact and project count linked; `group=` list anchor; back-to-origin; project change → parent tab; picker keys + server search; NewFeatureDialog draft

## Spec 依据

R6, I2

## 验收测试名

- `test_new_defect_dialog_unattributed_lands_in_triage`
- `test_pipeline_create_menu_prefills_scope`
- `test_quick_capture_carries_scope_and_refreshes_counts`
- `test_overview_every_fact_links_to_filtered_tab`
- `test_detail_back_link_returns_to_origin`
- `test_project_change_on_detail_goes_to_parent_tab`
- `test_project_picker_keyboard_and_server_search`
- `test_new_feature_dialog_keeps_draft`
- `test_action_toasts_link_the_object`

## 收尾动作

合回 `f119/integration`（这是报告完成的前置条件，不是可选步骤）
