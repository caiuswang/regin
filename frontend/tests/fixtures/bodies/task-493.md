## Delivers

确认 S1–S5 都已合回 `f155/integration`，并且没有零 commit 的片。在 DB 副本上执行 `regin migrate` 与 `doctor`。按闸门跑一次汇总测试。用 spare port 起真实应用，走一遍 /tasks?feature_id=155、/features、/features/148，截图存到 `/tmp/f155-product/` 并通过 send_to_user 汇报

## Spec 依据

验收, 闸门

## 验收测试名

- `test_every_slice_merged_back`
- `test_migrate_leaves_no_schema_finding`

## 收尾动作

合回 `f155/integration`（这是报告完成的前置条件，不是可选步骤）
