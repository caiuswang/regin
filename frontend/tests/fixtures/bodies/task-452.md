## Delivers

新路由 `/projects/:id`（含 `/projects/default`），挂在 Pipeline shell 下、不点亮 Tab、不进 KeepAlive；页面含 Stages 分区（新增、改名、排序、自定义阶段设 role、归档/删除，行展开为基于 `PromptBody` 的提示词编辑器）与只读基本信息（链到 `/repos/:name`）；viewer 隐藏编辑控件。入口：`ProjectPicker` 的「Manage project…」与 `ProjectScopePanel` 头部设置按钮。`FeatureTasks.vue` 按 `position` 为每个未归档阶段渲染按钮；`FeatureStepDialog.vue` 显示阶段 label 与只读提示词预览，请求体发 `stage`。新增 e2e `frontend/tests/project-stages.spec.js

## Spec 依据

项目管理页, R6, R7, R8

## 验收测试名

- `test_project_stages_page_edits_stages`
- `test_feature_page_renders_stage_buttons`
- `test_archived_stage_hides_button`

## 收尾动作

合回 `f148/integration`（这是报告完成的前置条件，不是可选步骤）
