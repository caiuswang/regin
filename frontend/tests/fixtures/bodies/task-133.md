## Delivers

人类 prompt 数的读数：归属于该 feature 且**不在 `agent_task_runs`** 里的 session，其 `prompts` 之和。外加 feature 73 归属的一次性考古（把人驱动的 session 补声明上去），把基线 8 补成真实数字。

## Spec 依据

不变量 7、Intent 的判据段

## 验收测试名

- `test_human_prompt_count_excludes_task_runs`

## 收尾动作

合回 `f76/integration`（这是报告完成的前置条件，不是可选步骤）

---

Base: `f76/integration` · 分支 `f76/s` 见 arm 参数。本片属于 feature #76 的切片表；改动请改 spec 的表，再重跑转录。