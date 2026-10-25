## Delivers

feature_memory_mounts` 表（ORM、`schema.sql`、alembic 修订）、`UNIQUE(feature_id,kind,ref)`、挂载读写的 store、沿 supersede 链向前的解析

## Spec 依据

Model, R1, R2, Invariant 7, Invariant 8

## 验收测试名

- `tests/features/test_mounts_schema_parity.py`
- `tests/features/test_mounts_crud.py`
- `tests/features/test_mounts_supersede.py`

## 收尾动作

合回 `master`（这是报告完成的前置条件，不是可选步骤）
