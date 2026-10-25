## Delivers

在 `f143/integration` 上对现有库跑 `regin migrate`，确认 `features.branch` / `agent_tasks.auto_title` 落地、defect task 的 `auto_title` 已回填，doctor 无 schema finding

## Spec 依据

Schema

## 验收测试名

- `test_migrate_leaves_no_schema_finding`

## 收尾动作

合回 `f143/integration`（这是报告完成的前置条件，不是可选步骤）
