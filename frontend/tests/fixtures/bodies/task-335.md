## Delivers

在 `f125/integration` 上跑全量 pytest（排除基线红：`trace_payload` 6 条、并行下偶发的 `test_a_reserved_run_is_not_reachable_yet`）、四个 mod 的 `claude plugin test`、两个 queued 相关的 Playwright spec。复现于基线的失败只报告不修

## Spec 依据

Acceptance, I1–I6

## 验收测试名

- `test_full_suite_passes`

## 收尾动作

合回 `f125/integration`（这是报告完成的前置条件，不是可选步骤）
