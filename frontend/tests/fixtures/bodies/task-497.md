## Delivers

regin migrate` 按 R2 表处理没有 stamp 的库：schema.sql 的表和列全在就 stamp，有缺失就拒绝并列出缺失项。`regin rebuild` 只 drop / 重建 R3 名单里的缓存表及同源 FTS / 向量表，同时改正 docstring 和 CLI help。新增 alembic revision 0041，用 `sqlite_autoincrement=True` 重建 0011–0040 用 `op.create_table` 建出的整数主键表；`db/schema.sql` 和 `lib/orm/models/` 与之对齐。新增冻结的 0008 时代 schema 测试数据。重写 `alembic/README` 和 `0001_baseline.py` 的 docstring：init 自带 stamp，升级用 `regin migrate`，新表要带 AUTOINCREMENT

## Spec 依据

R2, R3, R7 alembic AUTOINCREMENT, R7 alembic README, 不变量 2, 不变量 3

## 验收测试名

- `test_migrate_refuses_an_unstamped_incomplete_database`
- `test_migrate_stamps_an_unstamped_complete_database`
- `test_rebuild_keeps_non_cache_tables`
- `test_upgraded_schema_matches_fresh_schema`

## 收尾动作

合回 `f156/integration`（这是报告完成的前置条件，不是可选步骤）
