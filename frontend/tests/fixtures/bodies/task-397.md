## Delivers

在 `f140/integration` 上跑一次全量 pytest（全 feature 唯一一次）；真机 kimi 用 ACP 与 tmux 各跑一个 task，直到 settle；跑 `regin migrate` 确认无 schema 落差（本期无 schema 变更）；失败就地修复

## Spec 依据

Acceptance, Invariant 2, Invariant 3

## 验收测试名

- `test_full_suite_passes`
- `test_real_kimi_tmux_run_settles_via_finish`
- `test_active_provider_kimi_task_run_end_to_end`

## 收尾动作

合回 `f140/integration`（这是报告完成的前置条件，不是可选步骤）
