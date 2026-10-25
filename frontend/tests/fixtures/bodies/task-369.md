## Delivers

MenuField` slot (classic Select / pixel key + listbox) wired into PipelineListToolbar for tasks GROUP + SORT and features SORT; `plScope` provide/inject so teleported primitives hook only inside the shell; TaskFilterMenu hooks + glyphs with R14 apply semantics; `pickChoice` extended to MenuField; e2e `pipeline-theme-menus.spec.js

## Spec 依据

R6, R13, R14, "Tasks filter", "Tasks group / sort", "Features sort" rows

## 验收测试名

- `test_task_filter_popover`
- `test_menus_are_keyboard_complete`
- `test_primitives_outside_the_shell_stay_unhooked`
- `test_choice_fields_under_both_themes`

## 收尾动作

合回 `f139/integration`（这是报告完成的前置条件，不是可选步骤）
