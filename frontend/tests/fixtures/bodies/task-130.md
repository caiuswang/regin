## Delivers

R6 的 native 一支：`_finish_block` 对 native feature 的 run 也给出前置条件 —— 报告完成之前先合回 integration。远程那一支不许改。

## Spec 依据

R6

## 验收测试名

- `test_native_finish_block_requires_merging_back_first`
- `test_remote_finish_block_is_unchanged`

## 收尾动作

合回 `f76/integration`（这是报告完成的前置条件，不是可选步骤）

---

Base: `f76/integration` · 分支 `f76/s` 见 arm 参数。本片属于 feature #76 的切片表；改动请改 spec 的表，再重跑转录。