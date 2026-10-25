## Delivers

New `frontend/src/components/search/SearchEntityPreview.vue`, plus `SearchPreview.vue` dispatch. It shows the head (ref, status, meta), relations, ≤5 lists + "All N →", the read-only waiting card (red when destructive), latest prompts and tail. Preview-focus state machine: ⌥↑↓ picks, ⌥→ follows, ⌥← / esc pops, breadcrumb, dashed origin row, "↵ acts on <ref>". The R6 action bar is navigation plus Copy id only, with no write call anywhere. esc unwinds one layer at a time. Matches images 5, 6 and 7. New e2e `frontend/tests/search-palette-walk.spec.js

## Spec 依据

R5, R6, R8, Non-goals, Screens 5–7

## 验收测试名

- `test_walk_defect_to_task_to_session`
- `test_alt_left_restores_origin`
- `test_enter_acts_on_pivot`
- `test_waiting_card_has_no_approve`
- `test_defect_actions_have_no_triage`
- `test_palette_makes_no_write_request`

## 收尾动作

合回 `f146/integration`（这是报告完成的前置条件，不是可选步骤）
