## Delivers

确认 S1–S3 都已合回 `f148/integration`、没有零 commit 的片，冲突就地解决。在现有 DB 的副本上（`sqlite3 .backup`，经 `REGIN_TRACE_DB_PATH` 指向）跑 `regin migrate` 与 `doctor`，确认无 schema finding。跑一次 F148 范围的汇总测试（见闸门：触及目录的 pytest + pipeline 相关 Playwright + `responsive.spec.js`），不跑全量。最后起真实应用（spare port 上的 `regin serve` + vite），走一遍 /overview、/features/F、/projects/P，截图到 `/tmp/f148-product/`，经 `send_to_user` 汇报；发现问题就地修

## Spec 依据

验收, 闸门

## 验收测试名

- `test_every_slice_merged_back`
- `test_migrate_leaves_no_schema_finding`

## 收尾动作

合回 `f148/integration`（这是报告完成的前置条件，不是可选步骤）
