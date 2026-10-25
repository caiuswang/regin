## Delivers

MarkdownContent.vue` gains the opt-in refs prop (D11) and renders references as chips C1–C6, deciding chip-ness with utils/refs.js and setting chip text with textContent; a delegated in-app click handler; `frontend/src/composables/useRefResolve.js` (batched per C4); refs set on FeatureSpec.vue and TaskBodyEditor.vue views and previews; `lib/refs/backlinks.py` and referenced_by in `_ledger_body` (B1, B3); `frontend/src/components/features/FeatureBacklinks.vue` mounted under the Spec section in FeatureLedgerView.vue (B2)

## Spec 依据

D4, D8, D11, R6, R8, I2, I4, I6, I8

## 验收测试名

- `test_reference_renders_as_a_live_chip`
- `test_chip_click_navigates_in_app`
- `test_dangling_reference_renders_not_found`
- `test_reference_inside_code_stays_code`
- `test_references_resolve_in_one_request`
- `test_hostile_title_renders_as_text`
- `test_referenced_by_lists_citing_features_and_tasks`
- `test_ledger_body_carries_referenced_by_on_every_write`
- `test_backlinks_ignore_code_self_and_own_tasks`

## 收尾动作

合回 `f125/integration`（这是报告完成的前置条件，不是可选步骤）
