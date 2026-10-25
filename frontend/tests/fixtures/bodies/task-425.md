## Delivers

检查 S1–S4 都已合回 `f144/integration`，没有零 commit 的切片；冲突就地解决（`useTaskQuery.js` / `taskChips.js` 可能被 S1 与 S4 同时改）

## Spec 依据

Invariants

## 验收测试名

- `test_every_slice_merged_back`

## 收尾动作

合回 `f144/integration`（这是报告完成的前置条件，不是可选步骤）
