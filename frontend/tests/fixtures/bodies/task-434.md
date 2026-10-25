## Delivers

frontend/src/components/search/SearchResultList.vue` + `FullTextSearch.vue` + `SearchFacetRail.vue` + `useFullTextSearch.js` + `utils/searchDocs.js`: a two-line `PaletteRow` (ref, title, owner chips, status, context, aside) cut off at 2 lines. The "+N more" `<p>` becomes a focusable "Show all N {kind} →" row that opens Drill, which pages by 12 with `offset` (load more, retry on failure, end of list). New kinds and group order. Empty kinds are disabled and ⇥ skips them. Facet count = `group.total`, with `…` / `!` / `—` / `N+`. Address states from `/api/palette/resolve`. Nothing is preselected while loading. Matches images 2, 3, 4 and 10. New e2e `frontend/tests/search-palette-drill.spec.js

## Spec 依据

R1, R2, R7, R8, Invariants, Screens 2–4, 10

## 验收测试名

- `test_show_all_reaches_every_session`
- `test_drill_pages_by_twelve`
- `test_drill_page_failure_retries`
- `test_facet_count_equals_total`
- `test_tab_skips_empty_kinds`
- `test_address_miss_note`

## 收尾动作

合回 `f146/integration`（这是报告完成的前置条件，不是可选步骤）
