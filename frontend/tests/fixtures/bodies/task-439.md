## Delivers

No schema change in this feature. Run `.venv/bin/python cli/regin.py migrate` and `doctor` on a **copy** of the live DB (`sqlite3 .backup`, pointed at via `REGIN_TRACE_DB_PATH`), and confirm there is no schema finding

## Spec 依据

Shared rules

## 验收测试名

- `test_migrate_leaves_no_schema_finding`

## 收尾动作

合回 `f146/integration`（这是报告完成的前置条件，不是可选步骤）
