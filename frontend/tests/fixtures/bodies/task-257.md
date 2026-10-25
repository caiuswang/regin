## Delivers

/defects` list page + `triage=` filter; unsnooze / restore endpoints; R5 ⋯ matrix; merge picker with preview; `FeaturePicker` (server `q`, no abandoned); suspect-paths edit; image attach + images on triage cards; Accept unified (D6); `remedy` on refusals; triage arrival focus; never restarts :8321 (live check is S12)

## Spec 依据

D2, D6, R5, Shape (DefectVerb, Refusal), I3, I4, I6

## 验收测试名

- `test_defects_list_page_filters_and_pages`
- `test_defects_api_triage_filter`
- `test_defect_unsnooze_and_restore_keep_round`
- `test_defect_detail_menu_offers_verbs_by_state`
- `test_defect_merge_picker_previews_and_confirms`
- `test_feature_picker_server_search_excludes_abandoned`
- `test_defect_edit_suspect_paths`
- `test_defect_attach_image_on_detail`
- `test_triage_accept_same_on_rail_and_sheet`
- `test_defect_refusal_carries_remedy`
- `test_triage_arrival_focuses_card`

## 收尾动作

合回 `f119/integration`（这是报告完成的前置条件，不是可选步骤）
