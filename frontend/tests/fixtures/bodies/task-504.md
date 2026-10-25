## Delivers

确认 S1–S8 都已合回 `f156/integration`，没有零 commit 的片。在 DB 副本上跑 `.venv/bin/python cli/regin.py migrate` 和 `doctor`，确认这个安装没落后。全量跑 `.venv/bin/python -m pytest -q -n auto --dist=loadfile`。在临时目录里 clone `f156/integration`，按新用户路径走一遍 `scripts/setup.sh` → 重跑 → `regin uninstall --dry-run`，通过 send_to_user 汇报输出

## Spec 依据

验收, 不变量 1, 不变量 6

## 验收测试名

- `test_every_slice_merged_back`
- `test_migrate_leaves_no_schema_finding`
- `test_full_suite_passes`

## 收尾动作

合回 `f156/integration`（这是报告完成的前置条件，不是可选步骤）
