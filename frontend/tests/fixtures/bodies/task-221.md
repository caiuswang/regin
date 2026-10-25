## Delivers

one-line header, project picker, three-tab strip; /overview (1a, 1d); Features tab (1c) with triage rail and A/D/X/S keys; retired tabs, routes and views deleted; mobile 2a, 2c, 2d

## Spec 依据

Intent, Retired tabs, Shape (ShellHeader, ProjectPicker, Overview, FeaturesTab, TriageRail, Mobile), Rules (Next action, Scope, Triage verbs, Viewport), I3–I8

## 验收测试名

- `test_pipeline_shell_three_tabs_one_line_header`
- `test_pipeline_retired_tabs_are_gone`
- `test_pipeline_hotkeys`
- `test_overview_panels_and_empty_states`
- `test_features_stage_tabs_expand_and_triage_keys`

## 收尾动作

合回 `f110/integration`（这是报告完成的前置条件，不是可选步骤）
