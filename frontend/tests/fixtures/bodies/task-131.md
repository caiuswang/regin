## Delivers

R2 与 R3：读 spec 的切片表，产出 task 行与 `agent_task_deps` 的边，按 `(feature_id, S<n>)` 幂等。**机械，不做任何判断** —— 遇到含糊即拒绝并报出缺什么，绝不猜、绝不补 spec 没写的东西。

## Spec 依据

R2、R3、Model 的三分状态机、不变量 1/2/3

## 验收测试名

- `test_transcribe_is_idempotent_on_slice_id`
- `test_transcribe_refuses_a_running_slice`
- `test_transcribe_refuses_an_unknown_dependency`
- `test_transcribe_refuses_a_row_missing_base`
- `test_transcribe_writes_worktree_base_from_the_table`

## 收尾动作

合回 `f76/integration`（这是报告完成的前置条件，不是可选步骤）

---

Base: `f76/integration` · 分支 `f76/s` 见 arm 参数。本片属于 feature #76 的切片表；改动请改 spec 的表，再重跑转录。