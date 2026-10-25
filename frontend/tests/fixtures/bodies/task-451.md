## Delivers

lib/pipeline/overview.py`：`_loop` 的 stages 改为 Features、Tasks、Defects，新增 `features_in_flight`（spec + building，按最近活动倒序，上限 5，所有作用域都返回）。`PipelineView.vue` 的 `TABS` 与 `CACHED_TABS` 改为 Overview、Features、Tasks（`/defects` 仍点亮 Features）。Overview：新的 Features 面板与 `NeedsDecisionPanel` 组成第一行，`RunNextPanel` 移到下方占满整行，<1024px 顺序为 Features、Needs decision、Run next；右上角 `PipelineCreateButton kind="feature"`；单项目作用域下 `ProjectScopePanel`（classic 与 pixel 两版）只保留项目信息头。改写受影响的 pipeline Playwright spec，新增 `frontend/tests/pipeline-feature-first.spec.js`；后端测试放在 `tests/pipeline/test_overview.py

## Spec 依据

A 决策表, A Features 面板

## 验收测试名

- `test_pipeline_tabs_feature_first`
- `test_overview_create_defaults_to_feature`
- `test_overview_loop_is_feature_first`
- `test_features_in_flight_total_matches_loop`
- `test_features_panel_above_run_next`
- `test_project_scope_lists_features_once`

## 收尾动作

合回 `f148/integration`（这是报告完成的前置条件，不是可选步骤）
