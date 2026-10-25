## Delivers

frontend/src/components/tasks/TaskBodyEditor.vue` 与 `frontend/src/components/defects/DefectSymptom.vue` 改用 `MarkdownEditor`：删除各自的 `previewing` 状态与 Preview/Source 按钮，源码 textarea 保留原 `data-testid`（`task-body-input` / 既有 defect symptom input id）、`aria-label` 与 paste/drop 监听，预览面 `data-testid` 分别为 `task-body-preview-pane` / `defect-symptom-preview-pane`；两处头注释里「Source over a rendered preview」的理由改为引用 spec 的往返实测，不再各写一遍。`QuickCaptureBubble` / `TaskComposerDialog` / `NewDefectDialog` 等 composer 不在本片范围。E2E `frontend/tests/f174-reuse.spec.js` 覆盖 test_component_reused_in_task_and_defect_editors；`task-body-markdown.spec.js`、`defect-symptom-verbatim.spec.js`、`defect-edit-inline.spec.js` 不改输入方式即通过

## Spec 依据

Model（宿主替换顺序）, Invariants 2 6

## 验收测试名

- `test_component_reused_in_task_and_defect_editors`

## 收尾动作

合回 `f174/integration`（这是报告完成的前置条件，不是可选步骤）
