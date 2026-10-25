## Delivers

composables/usePipelineScope.js` 接受 `ws:<id>`。`lib/pipeline/`（counts/overview/projects）以及 Features、Tasks 列表接口按 `project_id ∈ members(w)` 过滤，workspace 不存在时退回 `all`。`ProjectPicker.vue` 把 workspace 显示为可选中的分组标题，成员缩进列在下面。测试 `tests/pipeline/test_workspace_scope.py` + e2e `frontend/tests/f167-workspace-scope.spec.js

## Spec 依据

Rules C（scope、ProjectPicker 行）, Invariants

## 验收测试名

- `test_workspace_scope_filters_the_pipeline_to_member_repos`

## 收尾动作

合回 `f167/integration`（这是报告完成的前置条件，不是可选步骤）
