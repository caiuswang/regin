## Delivers

S1–S5 of R6 plus V5: scope chip reads `?project=` on /defects with a reactive stored scope; `subline` published; New ▾ on defect pages; Overview bad scope → not-found; row tag from `queue_position`; attribute note follows the verb set

## Spec 依据

R6, R4 (V5)

## 验收测试名

- `test_defects_scope_chip_follows_query`
- `test_defect_pages_publish_subline_and_create`
- `test_overview_bad_scope_shows_not_found`
- `test_defect_row_tag_uses_queue_position`
- `test_attribute_note_follows_verb_set`

## 收尾动作

合回 `f125/integration`（这是报告完成的前置条件，不是可选步骤）
