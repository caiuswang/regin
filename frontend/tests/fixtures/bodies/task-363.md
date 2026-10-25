## Delivers

dialog chrome + glyphs for the three create dialogs, the arm sheet (TaskArmSheet + TaskArmSkills) and the picker; `ChoiceField` slot (classic select / pixel radio keys) for priority + launch, plus the `pickChoice` test helper with existing specs switched to it; `SecretStage` slot (triple click on ALL via `detail >= 3`); e2e `pipeline-theme-secret.spec.js`, `pipeline-theme-dialogs.spec.js`

## Spec 依据

R6, R7, R11, R12, New-feature / New-task / Arm-task / Picker / Secret stage rows of the per-screen table (images 6, 7, 8, 9, 10)

## 验收测试名

- `test_secret_stage`
- `test_new_task_dialog`
- `test_arm_dialog_keeps_every_field_of_the_classic_sheet`
- `test_choice_fields_under_both_themes`

## 收尾动作

合回 `f139/integration`（这是报告完成的前置条件，不是可选步骤）
