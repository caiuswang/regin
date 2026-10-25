## Delivers

frontend/src/composables/useRefMentions.js` (T1–T4, query grammar, 180 ms debounce, sequence guard, stale and armed accept, mirroring useFileMentions.js); `frontend/src/components/refs/RefMenu.vue` (grouped listbox M1, M6–M10, per-instance ids, data-ref-menu); `frontend/src/components/refs/RefTextarea.vue` (keys R3, insert/undo/atomic Backspace/URL paste R4, single root with inheritAttrs false and defineExpose textarea per E6); `ui/Dialog.vue` outside-interaction guard (M10); `utils/caret.js` reads area.textarea first (E6); swapped in at the seven R5 sites, with a project prop threaded from FeatureLedgerView.vue into FeatureSpec.vue

## Spec 依据

D2, D6, R1, R2, R3, R4, R5, I5, I9, I10

## 验收测试名

- `test_at_sign_lists_recent_work_and_six_kinds`
- `test_typing_searches_every_kind`
- `test_enter_inserts_a_markdown_reference`
- `test_kind_row_drills_into_one_kind`
- `test_id_shortcut_finds_the_exact_item`
- `test_escape_closes_the_menu_not_the_dialog`
- `test_clicking_a_row_inside_a_dialog_inserts_and_keeps_the_dialog`
- `test_enter_before_results_land_accepts_the_fresh_row`
- `test_trigger_after_cjk_but_not_in_an_email_or_code`
- `test_ime_composition_never_accepts_a_row`
- `test_undo_restores_the_typed_query`
- `test_backspace_removes_a_whole_reference`
- `test_pasting_a_regin_url_inserts_a_reference`
- `test_file_search_is_scoped_to_the_project_repo`
- `test_every_body_editor_offers_references`
- `test_menu_fits_a_phone_viewport`

## 收尾动作

合回 `f125/integration`（这是报告完成的前置条件，不是可选步骤）
