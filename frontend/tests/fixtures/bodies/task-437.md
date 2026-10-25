## Delivers

SessionSwitcherList.vue` + `SessionSwitcherPreview.vue` + `utils/sessionSwitcher.js`: roster rows use `row` (same shape as search, no left rail). The 1–9 attach and `#` lookup are kept, and `retrying` is bucketed active. The roster preview reuses `SearchEntityPreview`. At ≤900px the rail becomes a horizontal scope + kind strip and ↵ / → opens the preview as a sheet. Matches images 8 and 9. Update `frontend/tests/session-switcher.spec.js` and add the narrow case

## Spec 依据

R4, R8, Screens 8, 9

## 验收测试名

- `test_roster_row_names_owners`
- `test_roster_number_attach_kept`
- `test_retrying_counts_active`
- `test_narrow_opens_sheet_first`

## 收尾动作

合回 `f146/integration`（这是报告完成的前置条件，不是可选步骤）
