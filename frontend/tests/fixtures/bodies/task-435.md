## Delivers

Empty query renders `/api/palette/home`: Needs me (tinted, "Show all N waiting →"), Active now, Active features, Sessions, plus recent searches in the rail. With This session, "This session's chain". With no current session, the scope chip is disabled and ⌃S shows a notice. Matches images 1 and 9. New e2e `frontend/tests/search-palette-home.spec.js

## Spec 依据

R1, R8, Screens 1, 9

## 验收测试名

- `test_needs_me_is_first`
- `test_calm_home_collapses_needs_me`
- `test_this_session_shows_chain`
- `test_ctrl_s_without_session_is_noop`

## 收尾动作

合回 `f146/integration`（这是报告完成的前置条件，不是可选步骤）
