## Delivers

R5 全部：一次完成之后若仍有 dependent 被一个 **failed** 的片挡着，发一条指名该片的消息；被未完成的片挡着则不发。关闭 **d157**。

## Spec 依据

R5、不变量 6

## 验收测试名

- `test_a_failed_slice_announces_the_broken_chain`
- `test_a_merely_unfinished_dependency_announces_nothing`

## 收尾动作

合回 `f76/integration`（这是报告完成的前置条件，不是可选步骤）

---

Base: `f76/integration` · 分支 `f76/s` 见 arm 参数。本片属于 feature #76 的切片表；改动请改 spec 的表，再重跑转录。