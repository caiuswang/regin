## Delivers

R2：`FeatureTasks` 每行按 `waits_on` / `blocked_by` 显示 "waits on #N"（链到 `/tasks/N`，`blocked_by.length > 1` 追加 "+k"），无依赖不显示（D4、D5）；卡片头加 "Open in Tasks"（`/tasks?feature_id=F&view=list`）与 "Graph"（`/tasks?feature_id=F&view=graph&open=0`）两个只读链接，不加 Arm/Run、不嵌 graph（I3）；若卡片的数据源没带 `blocked_by`/`waits_on`，在其 API 上补齐；新增 e2e `frontend/tests/f144-feature-tasks.spec.js

## Spec 依据

R2, D4, D5, I3

## 验收测试名

- `Feature task rows show what they wait on`
- `Feature tasks jump to the Tasks page scoped to the feature`

## 收尾动作

合回 `f144/integration`（这是报告完成的前置条件，不是可选步骤）
