## Delivers

loop as one card with a facts line and neutral bars; Escaped panel and `escaped` payload removed; Projects panel without search/sort/legend; decision rows with Attribute only; caption sentences dropped; colour budget on /overview incl. 1d and 2a; closes defect #187 (tints drawn against spec)

## Spec 依据

Intent (Overview override), Shape (Overview, LoopStage, ProjectsPanel, TriageRow), Rules (Next action, Overview ordering, Scope, Loop facts, Overview colour budget), I10, I11

## 验收测试名

- `test_overview_quiet_loop_facts_and_links`
- `test_overview_colour_budget`
- `test_overview_payload_has_no_escaped_list`
- `test_overview_panels_and_empty_states`

## 收尾动作

合回 `f110/integration`（这是报告完成的前置条件，不是可选步骤）
