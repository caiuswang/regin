## Delivers

R4 全部：run 结束后在独立子进程里跑 `verify_cmd`，非零即 `failed`，任何情况都写 `verify_exit_code`。关闭 **d158**。

## Spec 依据

R4、不变量 4、Operating constraint

## 验收测试名

- `test_verify_cmd_non_zero_settles_the_run_failed`
- `test_verify_cmd_zero_settles_the_run_done`
- `test_absent_verify_cmd_leaves_verify_exit_code_null`

## 收尾动作

合回 `f76/integration`（这是报告完成的前置条件，不是可选步骤）

---

Base: `f76/integration` · 分支 `f76/s` 见 arm 参数。本片属于 feature #76 的切片表；改动请改 spec 的表，再重跑转录。