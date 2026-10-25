## Delivers

alembic/versions/0037_*.py` + `db/schema.sql` + `lib/orm/models/`：新增 `features.branch TEXT NULL` 与 `agent_tasks.auto_title TEXT NULL`；迁移时对现有 `source='defect'` 的 task 设 `auto_title = title`；fresh install 与 migrate 后的 schema 一致；测试放在 `tests/defects/test_fix_task_schema.py

## Spec 依据

Shape, Schema

## 验收测试名

- `test_features_branch_column_exists`
- `test_agent_tasks_auto_title_backfilled_for_defect_tasks`
- `test_schema_sql_matches_alembic_head`

## 收尾动作

合回 `f143/integration`（这是报告完成的前置条件，不是可选步骤）
