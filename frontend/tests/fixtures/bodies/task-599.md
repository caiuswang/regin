## Delivers

跑 `.venv/bin/python cli/regin.py migrate` 与 `regin doctor`，确认本功能没有引入 schema 变更、这个安装没落后

## Spec 依据

Non-goals

## 验收测试名

- `test_migrate_leaves_no_schema_finding`

## 收尾动作

合回 `f174/integration`（这是报告完成的前置条件，不是可选步骤）
