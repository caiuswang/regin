## Delivers

从 `ReposView.vue` 抽出 `AddProjectDialog.vue`（先 probe，按分支表选择 Repository/Workspace 模式），`ReposView` 改为复用它。入口：`ProjectPicker` 底部 "+ Add project…"（仅 editor）、`ProjectsPanel` 零项目首次使用卡片（editor/viewer 两种文案）、`NewFeatureDialog`/`TaskComposerDialog`/`TaskEditSheet` 零项目时的 "Add project" 链接、`TaskArmSheet`/`SliceArmSheet` working directory 末尾的 "+ Add project…"。提交后刷新 `useProjects`，并按 Rules A 选中新项目或切换 scope。e2e `frontend/tests/f167-add-project.spec.js

## Spec 依据

Rules A, AddProjectDialog 分支表, Invariants

## 验收测试名

- `test_project_picker_offers_add_project_to_editors`
- `test_project_picker_hides_add_project_from_viewers`
- `test_overview_empty_state_invites_adding_a_project`
- `test_adding_a_repo_from_the_picker_scopes_the_pipeline_to_it`
- `test_probe_rejects_a_folder_with_no_repos`

## 收尾动作

合回 `f167/integration`（这是报告完成的前置条件，不是可选步骤）
