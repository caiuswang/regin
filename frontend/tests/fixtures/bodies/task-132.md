## Delivers

把切片表（含 `Base` 列与表头 `Base:`）写进 `features.body`。spec 没有可证伪验收项就拒绝并报出缺什么。切分前必做 G-SURFACE 自检：**还有哪个界面也干这件事**。两片都要改 schema 时拒绝。

## Spec 依据

R2 最后两行、Intent 的 Non-goals

## 验收测试名

- `test_slicing_refuses_a_spec_without_named_tests`
- `test_slicing_refuses_two_slices_owning_one_schema_change`

## 收尾动作

合回 `f76/integration`（这是报告完成的前置条件，不是可选步骤）

---

Base: `f76/integration` · 分支 `f76/s` 见 arm 参数。本片属于 feature #76 的切片表；改动请改 spec 的表，再重跑转录。