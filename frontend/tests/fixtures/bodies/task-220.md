## Delivers

GET /api/pipeline/overview (loop, run next, needs decision, escaped, projects panel, project head and features); projects attention sort; feature stage counts and attribution header; tasks group=next_step and per-status board paging

## Spec 依据

Surface map, Shape (Overview, ListGroup, BoardColumn, FeaturesTab), Rules (Overview ordering, List grouping, Scope), I1, I2

## 验收测试名

- `test_overview_loop_parts_sum_to_totals`
- `test_overview_run_next_orders_dependency_clear_first`
- `test_overview_project_scope_replaces_projects_panel`
- `test_projects_attention_sort`
- `test_features_stage_counts_and_attribution_header`
- `test_tasks_next_step_groups`

## 收尾动作

合回 `f110/integration`（这是报告完成的前置条件，不是可选步骤）
